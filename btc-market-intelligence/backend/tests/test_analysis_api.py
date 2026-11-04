"""Snapshot schema, stored studies, and database failure behavior. No exchange calls."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.analysis.definitions import PIVOT_WIDTH, Bar
from app.analysis.events import breakout_events
from app.analysis.structure import structure_at
from app.core.timeutil import from_epoch_seconds
from app.data.normalizer import draft_from_ohlc
from app.database.models import Candle, ConditionMatch, HorizonStat, StudyRun
from app.database.repositories import MarketRepository
from app.main import create_app
from app.services.study_service import _day_bars, _load, run_studies
from tests.conftest import btc_product, make_settings

START = 1_700_003_400
BULL = [(10, 8, 9), (14, 11, 12), (11, 7, 9), (13, 10, 11), (16, 12, 15), (12, 8, 10), (15, 11, 13)]


def _draft(epoch: int, close: str = "100", high: str = "110", low: str = "90", timeframe: str = "1m", closed: bool = True):
    opened = from_epoch_seconds(epoch)
    step = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[timeframe]
    return draft_from_ohlc(
        symbol="BTCUSD",
        timeframe=timeframe,
        open_time=opened,
        timestamp_unit="s",
        exchange_time_raw=str(epoch),
        open_=close,
        high=high,
        low=low,
        close=close,
        volume="2",
        source="delta_rest",
        as_of=opened + timedelta(seconds=step if closed else 1),
    )


def _client(session_factory):
    app = create_app(make_settings(), session_factory, engine=None, start_socket=False)
    return TestClient(app)


def _seed(session, rows=BULL):
    repo = MarketRepository(session)
    product = repo.upsert_product(btc_product())
    drafts = []
    for index, (high, low, close) in enumerate(rows):
        drafts.append(_draft(START + index * 60, close=str(close), high=str(high), low=str(low)))
    repo.upsert_candles(product.id, drafts)
    session.commit()
    return product


def test_snapshot_reports_structure_without_a_stored_study(session_factory, session):
    _seed(session)
    with _client(session_factory) as client:
        missing = client.get("/analysis/snapshot")
        assert missing.status_code == 200
        body = missing.json()
        assert body["product"]["symbol"] == "BTCUSD"
        assert body["read_only"] is True
        assert set(body["timeframes"]) == {"1m", "5m", "15m", "1h", "4h", "1d"}
        assert body["comparison"]["short_term"]["1m"] == "INSUFFICIENT_DATA"
        assert body["comparison"]["higher"]["4h"] == "INSUFFICIENT_DATA"
        assert "not averaged" in body["comparison"]["note"]
        assert body["studies"]["status"] == "NOT_COMPUTED"
        assert body["freshness"]["latest_closed_candle_at"]
        assert body["definition_hash"]
        assert "crash is coming" not in missing.text.lower()
        for phrase in ("stop-loss", "take-profit", "leverage", "buy ", "sell "):
            assert phrase not in missing.text.lower()
        history = client.get("/analysis/history")
        assert history.status_code == 200
        assert history.json()["count"] == 0


def test_forming_candle_is_excluded_from_the_snapshot(session_factory, session):
    product = _seed(session)
    forming = _draft(START + 7 * 60, close="999", high="1000", low="998", closed=False)
    assert forming.is_closed is False
    MarketRepository(session).upsert_candles(product.id, [forming])
    session.commit()
    with _client(session_factory) as client:
        body = client.get("/analysis/snapshot").json()
    assert body["timeframes"]["1m"]["indicators"]["bars_used"] == len(BULL)


def test_invalid_stored_bar_is_not_used(session):
    product = _seed(session)
    opened = from_epoch_seconds(START + 100 * 60)
    session.add(
        Candle(
            product_id=product.id,
            symbol="BTCUSD",
            timeframe="1m",
            open_time=opened,
            open=10,
            high=1,
            low=12,
            close=10,
            volume=1,
            source="delta_rest",
            is_closed=True,
            exchange_time_raw=str(START),
            timestamp_unit="s",
            received_at=opened,
        )
    )
    session.commit()
    loaded = _load(session, product.id, "1m", None)
    assert len(loaded) == len(BULL)
    assert all(bar.high >= bar.low for bar in loaded)


def test_studies_persist_the_known_match_count_and_refuse_a_small_sample(session):
    product = _seed(session)
    original = PIVOT_WIDTH["1m"]
    PIVOT_WIDTH["1m"] = 1
    try:
        first = run_studies(session)
        assert first["status"] == "computed"
        rows = session.scalars(select(ConditionMatch).where(ConditionMatch.product_id == product.id)).all()
        series = [
            Bar(from_epoch_seconds(START + index * 60), close, high, low, close, 1)
            for index, (high, low, close) in enumerate(BULL)
        ]
        assert len(rows) == len(breakout_events(series, 1)) == 3
        stats = session.scalars(select(HorizonStat).where(HorizonStat.product_id == product.id)).all()
        assert stats
        assert {row.status for row in stats} == {"INSUFFICIENT_SAMPLE"}
        assert all(row.positive_rate is None for row in stats)
        second = run_studies(session)
        assert second["status"] == "current"
        changed = parameters_hash = None
        from app.analysis.definitions import definition_hash, parameters

        changed = parameters()
        changed["version"] = "phase3-test"
        assert definition_hash(changed) != first["definition_hash"]
        _ = parameters_hash
    finally:
        PIVOT_WIDTH["1m"] = original


def test_a_changed_definition_keeps_the_previous_matches(session):
    product = _seed(session)
    original = PIVOT_WIDTH["1m"]
    from app.analysis.definitions import definition_hash

    PIVOT_WIDTH["1m"] = 1
    try:
        run_studies(session)
        first_hash = definition_hash()
        kept = session.scalar(select(ConditionMatch).where(ConditionMatch.definition_hash == first_hash))
        assert kept is not None
    finally:
        PIVOT_WIDTH["1m"] = original
    run_studies(session)
    still = session.scalar(select(ConditionMatch).where(ConditionMatch.definition_hash == first_hash))
    assert still is not None
    assert session.scalar(select(StudyRun).where(StudyRun.definition_hash == definition_hash())) is not None


def test_reloading_after_a_future_edit_keeps_the_prefix_structure(session):
    product = _seed(session)
    before = structure_at(_load(session, product.id, "1m", None), 1, end=4)
    last = session.scalar(select(Candle).where(Candle.product_id == product.id).order_by(Candle.open_time.desc()))
    last.close = 1
    last.high = 50
    last.low = 1
    session.commit()
    after = structure_at(_load(session, product.id, "1m", None), 1, end=4)
    assert after == before


def test_snapshot_reads_the_persisted_study(session_factory, session):
    _seed(session)
    original = PIVOT_WIDTH["1m"]
    PIVOT_WIDTH["1m"] = 1
    try:
        run_studies(session)
        with _client(session_factory) as client:
            body = client.get("/analysis/snapshot").json()
            history = client.get("/analysis/history").json()
        assert body["studies"]["status"] == "CURRENT"
        assert body["freshness"]["study"]["up_to_date"] is True
        assert body["studies"]["status"] != "READY"
        assert body["studies"]["by_timeframe"]["1m"]["bullish_structure"]["15m"]["status"] == "INSUFFICIENT_SAMPLE"
        assert body["studies"]["by_timeframe"]["1m"]["bullish_structure"]["15m"]["positive_rate"] is None
        assert history["count"] == 1
        assert history["snapshots"][0]["definition_hash"] == body["definition_hash"]
    finally:
        PIVOT_WIDTH["1m"] = original


def test_a_newer_candle_marks_the_study_lagging(session_factory, session):
    product = _seed(session)
    original = PIVOT_WIDTH["1m"]
    PIVOT_WIDTH["1m"] = 1
    try:
        run_studies(session)
        MarketRepository(session).upsert_candles(product.id, [_draft(START + len(BULL) * 60, close="20", high="21", low="19")])
        session.commit()
        with _client(session_factory) as client:
            body = client.get("/analysis/snapshot").json()
        assert body["studies"]["status"] == "LAGGING"
        assert body["freshness"]["study"]["up_to_date"] is False
        assert body["freshness"]["study"]["lag_seconds"] >= 60
        assert body["freshness"]["study"]["computed_at"]
        assert body["studies"]["status"] != "READY"
    finally:
        PIVOT_WIDTH["1m"] = original


def test_previous_utc_day_is_not_cut_by_the_indicator_window(session):
    product = _seed(session)
    morning = datetime(2026, 11, 3, 1, tzinfo=timezone.utc)
    afternoon = datetime(2026, 11, 3, 18, tzinfo=timezone.utc)
    latest = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
    MarketRepository(session).upsert_candles(
        product.id,
        [
            _draft(int(morning.timestamp()), close="100", high="900", low="90"),
            _draft(int(afternoon.timestamp()), close="100", high="110", low="90"),
            _draft(int(latest.timestamp()), close="100", high="120", low="90"),
        ],
    )
    session.commit()
    window = [
        Bar(afternoon, 100, 110, 90, 100, 1),
        Bar(latest, 100, 120, 90, 100, 1),
    ]
    covered = _day_bars(session, product.id, window)
    assert max(bar.high for bar in covered if bar.open_time.date() == morning.date()) == 900


def test_database_failure_hides_the_connection_string(session_factory):
    class Broken:
        def scalar(self, *_args, **_kwargs):
            raise OperationalError("SELECT", {}, Exception("postgresql://user:secret@db.internal/neondb"))

        def close(self):
            return None

    app = create_app(make_settings(), lambda: Broken(), engine=None, start_socket=False)
    with TestClient(app) as client:
        response = client.get("/analysis/snapshot")
    assert response.status_code == 503
    assert "secret" not in response.text
    assert "db.internal" not in response.text


def test_missing_product_is_not_a_fabricated_snapshot(session_factory):
    with _client(session_factory) as client:
        response = client.get("/analysis/snapshot")
    assert response.status_code == 404
    assert "not in the database" in response.json()["detail"]
