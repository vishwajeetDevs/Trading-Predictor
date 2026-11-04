from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.timeutil import as_utc, from_epoch_seconds, to_decimal, utcnow
from app.data.candle_builder import missing_opens
from app.data.normalizer import draft_from_ohlc
from app.database.models import DataQualityEvent
from app.database.repositories import MarketRepository
from app.main import create_app
from app.services.health_service import build_health
from app.services.ingestion_service import RuntimeStatus, _store_candles
from tests.conftest import btc_product, make_settings


def _draft(epoch: int):
    opened = from_epoch_seconds(epoch)
    return draft_from_ohlc(
        symbol="BTCUSD",
        timeframe="1m",
        open_time=opened,
        timestamp_unit="s",
        exchange_time_raw=str(epoch),
        open_="100",
        high="110",
        low="90",
        close="105",
        volume="2",
        source="delta_ws",
        as_of=opened + timedelta(seconds=120),
    )


def test_read_only_endpoints_and_stale_detection(session_factory):
    settings = make_settings()
    app = create_app(settings, session_factory, engine=None, start_socket=False)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(token in path for path in paths for token in ("order", "position", "wallet", "leverage"))
    with TestClient(app) as client:
        assert client.get("/products/btc").status_code == 404
        root = client.get("/")
        assert root.status_code == 200
        assert "/health" in root.json()["endpoints"]
        assert "password" not in root.text.lower()
        empty = client.get("/health").json()
        assert empty["database"]["connected"] is True
        assert empty["stale"] is True
        assert empty["status"] == "degraded"
        assert client.get("/candles").json()["count"] == 0

    db = session_factory()
    repo = MarketRepository(db)
    product = repo.upsert_product(btc_product())
    now = utcnow()
    status = RuntimeStatus(ws_connected=True, last_message_at=now, subscriptions=["candlestick_1m"])
    stale_open = int(now.timestamp()) // 60 * 60 - 900
    repo.upsert_candles(product.id, [_draft(stale_open)])
    db.commit()
    stale = build_health(db, settings, status, now=now)
    assert stale["stale"] is True
    assert stale["gaps"] is False
    repo.upsert_candles(product.id, [_draft(stale_open + 180)])
    db.commit()
    gappy = build_health(db, settings, status, now=now)
    assert gappy["stale"] is True
    assert gappy["gaps"] is True
    assert missing_opens(repo.recent_closed_opens(product.id, "1m"), 60)

    fresh_open = int(now.timestamp()) // 60 * 60 - 60
    repo.upsert_candles(product.id, [_draft(fresh_open)])
    db.commit()
    fresh = build_health(db, settings, status, now=now)
    assert fresh["stale"] is False

    with TestClient(app) as client:
        body = client.get("/products/btc").json()
        assert body["symbol"] == "BTCUSD"
        assert body["tick_size"] == "0.5"
        assert body["underlying_asset"] == "BTC"
        assert "leverage" not in body
        candles = client.get("/candles", params={"timeframe": "1m", "limit": 10}).json()
        assert candles["count"] >= 1
        assert candles["candles"][0]["source"]
        assert client.get("/candles", params={"timeframe": "2h"}).status_code == 422
        assert client.get("/data/status").json()["product"]["symbol"] == "BTCUSD"
    db.close()


class GapClient:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def fetch_candles(self, symbol, resolution, start, end):
        self.calls.append((symbol, resolution, start, end))
        return {
            "success": True,
            "result": [{"time": start, "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1}],
        }


def test_websocket_gap_is_recovered_from_rest(session_factory, monkeypatch):
    db = session_factory()
    MarketRepository(db).upsert_product(btc_product())
    db.commit()
    db.close()
    holder = {}

    class Recording(GapClient):
        def __init__(self, *args, **kwargs):
            super().__init__()
            holder["client"] = self

    monkeypatch.setattr("app.services.ingestion_service.DeltaRestClient", Recording)
    start = 1_699_999_800
    _store_candles(make_settings(), session_factory, "BTCUSD", [_draft(start), _draft(start + 180)], "gap")
    check = session_factory()
    opens = sorted(int(as_utc(row.open_time).timestamp()) for row in check.scalars(select_candles(check)))
    assert start + 60 in opens
    assert holder["client"].calls
    kinds = check.scalars(select(DataQualityEvent.kind)).all()
    assert "missing_candle" in kinds
    check.close()


def test_recent_gap_recovery_is_idempotent_and_a_short_response_is_a_failure(session_factory):
    from app.services.backfill_service import BackfillService

    db = session_factory()
    repo = MarketRepository(db)
    product = repo.upsert_product(btc_product())
    start = int(utcnow().timestamp()) // 60 * 60 - 180
    repo.upsert_candles(product.id, [_draft(start), _draft(start + 180)])
    db.commit()

    class Complete:
        def fetch_candles(self, symbol, resolution, start, end):
            cursor = start
            rows = []
            while cursor <= end:
                rows.append({"time": cursor, "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1})
                cursor += 60
            return {"success": True, "result": rows}

    service = BackfillService(Complete(), make_settings())
    first = service.recover_recent_gaps(db, product, utcnow(), window_minutes=30)
    stored = len(db.scalars(select_candles(db)).all())
    second = service.recover_recent_gaps(db, product, utcnow(), window_minutes=30)
    assert first["status"] == "succeeded"
    assert second["status"] == "not_needed"
    assert len(db.scalars(select_candles(db)).all()) == stored
    db.close()


def _forming(epoch: int):
    opened = from_epoch_seconds(epoch)
    return draft_from_ohlc(
        symbol="BTCUSD",
        timeframe="1m",
        open_time=opened,
        timestamp_unit="s",
        exchange_time_raw=str(epoch),
        open_="100",
        high="110",
        low="90",
        close="101",
        volume="1",
        source="delta_ws",
        as_of=opened + timedelta(seconds=10),
    )


def _rest_bar(epoch: int) -> dict:
    return {"time": epoch, "open": 200, "high": 210, "low": 190, "close": 205, "volume": 9}


class _CompleteRest:
    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    def fetch_candles(self, symbol, resolution, start, end):
        self.calls.append((start, end))
        cursor = start
        rows = []
        while cursor <= end:
            rows.append(_rest_bar(cursor))
            cursor += 60
        return {"success": True, "result": rows}


def _rows_at(session, epoch: int):
    return [
        row
        for row in session.scalars(select_candles(session)).all()
        if int(as_utc(row.open_time).timestamp()) == epoch
    ]


def test_elapsed_unclosed_minute_and_a_hole_are_recovered_once(session_factory):
    from app.services.backfill_service import BackfillService

    base = 1_700_000_040
    db = session_factory()
    repo = MarketRepository(db)
    product = repo.upsert_product(btc_product())
    repo.upsert_candles(product.id, [_draft(base), _forming(base + 60), _draft(base + 180)])
    db.commit()
    assert _forming(base + 60).is_closed is False
    client = _CompleteRest()
    moment = from_epoch_seconds(base + 210)
    outcome = BackfillService(client, make_settings()).recover_recent_gaps(db, product, moment, window_minutes=30)
    replaced = _rows_at(db, base + 60)
    hole = _rows_at(db, base + 120)
    assert outcome["status"] == "succeeded"
    assert client.calls[0][0] <= base + 60
    assert client.calls[0][1] >= base + 120
    assert len(replaced) == 1
    assert replaced[0].is_closed is True
    assert replaced[0].source == "delta_rest"
    assert to_decimal(replaced[0].close) == Decimal("205")
    assert to_decimal(replaced[0].volume) == Decimal("9")
    assert len(hole) == 1
    assert hole[0].is_closed is True
    stored = len(db.scalars(select_candles(db)).all())
    second = BackfillService(_CompleteRest(), make_settings()).recover_recent_gaps(db, product, moment, window_minutes=30)
    assert second["status"] == "not_needed"
    assert len(db.scalars(select_candles(db)).all()) == stored
    db.close()


def test_unavailable_exchange_minute_is_recorded_and_a_later_retry_fills_it(session_factory):
    from app.services.backfill_service import BackfillService

    base = 1_700_000_040
    db = session_factory()
    repo = MarketRepository(db)
    product = repo.upsert_product(btc_product())
    repo.upsert_candles(product.id, [_draft(base), _draft(base + 180)])
    db.commit()
    moment = from_epoch_seconds(base + 210)

    class OneBar:
        def fetch_candles(self, symbol, resolution, start, end):
            return {"success": True, "result": [_rest_bar(start)]}

    failed = BackfillService(OneBar(), make_settings()).recover_recent_gaps(db, product, moment, window_minutes=30)
    assert failed["status"] == "failed"
    assert failed["still_missing"] == 1
    assert from_epoch_seconds(base + 120).isoformat() in failed["unavailable"]
    assert _rows_at(db, base + 120) == []
    assert len(_rows_at(db, base + 60)) == 1
    retried = BackfillService(_CompleteRest(), make_settings()).recover_recent_gaps(db, product, moment, window_minutes=30)
    assert retried["status"] == "succeeded"
    assert len(_rows_at(db, base + 120)) == 1
    assert len(db.scalars(select_candles(db)).all()) == 4
    db.close()


def test_recovery_repeats_for_minutes_that_close_during_the_fill(session_factory):
    from app.services.backfill_service import BackfillService

    base = 1_700_000_040
    db = session_factory()
    repo = MarketRepository(db)
    product = repo.upsert_product(btc_product())
    repo.upsert_candles(product.id, [_draft(base - 120), _draft(base), _forming(base + 60)])
    db.commit()
    times = [from_epoch_seconds(base + 70), from_epoch_seconds(base + 190)]

    def clock():
        return times.pop(0) if len(times) > 1 else times[0]

    client = _CompleteRest()
    outcome = BackfillService(client, make_settings()).recover_until_current(
        db, product, clock, window_minutes=30
    )
    assert outcome["passes_succeeded"] == 2
    assert outcome["status"] == "not_needed"
    assert any(start <= base - 60 <= end for start, end in client.calls)
    assert any(start <= base + 120 <= end for start, end in client.calls)
    assert len(_rows_at(db, base + 60)) == 1
    assert _rows_at(db, base + 60)[0].is_closed is True
    assert _rows_at(db, base + 60)[0].source == "delta_rest"
    assert len(_rows_at(db, base + 120)) == 1
    opens = [int(as_utc(row.open_time).timestamp()) for row in db.scalars(select_candles(db)).all()]
    assert len(opens) == len(set(opens))
    db.close()


def test_short_rest_response_does_not_invent_the_missing_minutes(session_factory):
    from app.services.backfill_service import BackfillService

    db = session_factory()
    repo = MarketRepository(db)
    product = repo.upsert_product(btc_product())
    start = int(utcnow().timestamp()) // 60 * 60 - 180
    repo.upsert_candles(product.id, [_draft(start), _draft(start + 180)])
    db.commit()

    class Empty:
        def fetch_candles(self, symbol, resolution, start, end):
            return {"success": True, "result": []}

    failed = BackfillService(Empty(), make_settings()).recover_recent_gaps(db, product, utcnow(), window_minutes=30)
    assert failed["status"] == "failed"
    assert failed["still_missing"] == 2
    assert len(db.scalars(select_candles(db)).all()) == 2
    db.close()


def test_diagnostics_hide_secrets_and_latest_candle_order(session_factory):
    import json

    settings = make_settings()
    app = create_app(settings, session_factory, engine=None, start_socket=False)
    db = session_factory()
    repo = MarketRepository(db)
    product = repo.upsert_product(btc_product())
    older = int(utcnow().timestamp()) // 60 * 60 - 180
    repo.upsert_candles(product.id, [_draft(older), _draft(older + 60)])
    forming = draft_from_ohlc_open(older + 120)
    repo.upsert_candles(product.id, [forming])
    repo.add_quality("missing_candle", {"count": 1}, product_id=product.id, timeframe="1m")
    db.commit()
    db.close()
    with TestClient(app) as client:
        body = client.get("/diagnostics").json()
        text = json.dumps(body).lower()
        assert "postgresql://" not in text
        assert "password" not in text
        assert body["tables"]["candles"] is True
        assert body["constraints"]["uq_candle_slot"] is True
        assert body["counts"]["products"] == 1
        assert body["counts"]["candles"] == 3
        newest = client.get("/candles", params={"timeframe": "1m", "limit": 1, "order": "desc"}).json()
        assert newest["count"] == 1
        assert newest["candles"][0]["is_closed"] is True
        forming_row = client.get(
            "/candles", params={"timeframe": "1m", "limit": 1, "order": "desc", "include_forming": True}
        ).json()
        assert forming_row["candles"][0]["is_closed"] is False
    db.close()


def draft_from_ohlc_open(epoch: int):
    opened = from_epoch_seconds(epoch)
    return draft_from_ohlc(
        symbol="BTCUSD",
        timeframe="1m",
        open_time=opened,
        timestamp_unit="s",
        exchange_time_raw=str(epoch),
        open_="100",
        high="110",
        low="90",
        close="104",
        volume="1",
        source="delta_ws",
        as_of=opened + timedelta(seconds=10),
    )


def test_delta_probe_reports_a_public_read_without_storing_it(session_factory, monkeypatch):
    settings = make_settings()
    app = create_app(settings, session_factory, engine=None, start_socket=False)
    db = session_factory()
    MarketRepository(db).upsert_product(btc_product())
    db.commit()
    db.close()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def fetch_candles(self, symbol, resolution, start, end):
            assert symbol == "BTCUSD"
            assert resolution == "1m"
            return {"success": True, "result": [{"time": start}]}

    monkeypatch.setattr("app.data.delta_rest.DeltaRestClient", FakeClient)
    with TestClient(app) as client:
        body = client.get("/diagnostics/delta").json()
    assert body["ok"] is True
    assert body["symbol"] == "BTCUSD"
    assert body["candle_count"] == 1
    assert "postgresql" not in json_text(body)


def json_text(body) -> str:
    import json

    return json.dumps(body).lower()


def select_candles(session):
    from app.database.models import Candle

    return select(Candle)
