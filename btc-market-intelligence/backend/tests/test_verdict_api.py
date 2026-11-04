"""HTTP contract of /analysis/verdict. No exchange calls."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.analysis.engine.types import DISCLAIMER
from app.core.timeutil import from_epoch_seconds
from app.data.normalizer import draft_from_ohlc
from app.database.models import VerdictRun
from app.database.repositories import MarketRepository
from app.main import create_app
from app.services import verdict_service
from tests.conftest import btc_product, make_settings

START = 1_700_000_000


def _draft(epoch: int, close: float, high: float, low: float, timeframe: str = "1m"):
    opened = from_epoch_seconds(epoch)
    step = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[timeframe]
    return draft_from_ohlc(
        symbol="BTCUSD",
        timeframe=timeframe,
        open_time=opened,
        timestamp_unit="s",
        exchange_time_raw=str(epoch),
        open_=str(close),
        high=str(high),
        low=str(low),
        close=str(close),
        volume="2",
        source="delta_rest",
        as_of=opened + timedelta(seconds=step),
    )


def _seed(session, count=80):
    repo = MarketRepository(session)
    product = repo.upsert_product(btc_product())
    for timeframe, step in (("1m", 60), ("15m", 900), ("1h", 3600), ("4h", 14400), ("1d", 86400)):
        drafts = []
        for index in range(count):
            close = 100 + (index % 7) - 3
            drafts.append(_draft(START + index * step, close, close + 2, close - 2, timeframe))
        repo.upsert_candles(product.id, drafts)
    session.commit()
    return product


def _client(session_factory, **overrides):
    verdict_service.clear_cache()
    app = create_app(make_settings(**overrides), session_factory, engine=None, start_socket=False)
    return TestClient(app)


def test_post_verdict_returns_the_documented_shape(session_factory, session):
    _seed(session)
    with _client(session_factory) as client:
        response = client.post("/analysis/verdict", json={"focus_timeframe": "15m", "horizon": "4h", "account_size": 1000, "risk_percent": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["engine_version"]
    assert body["verdict"]["verdict"] in {"ENTER_NOW", "WAIT_FOR_BETTER_ENTRY", "AVOID_HIGH_RISK", "HOLD", "CONSIDER_EXIT"}
    assert body["disclaimer"] == DISCLAIMER
    assert body["verdict"]["disclaimer"] == DISCLAIMER
    assert abs(body["probabilities"]["p_bull"] + body["probabilities"]["p_bear"] + body["probabilities"]["p_range"] - 1.0) < 1e-3
    assert set(body["probabilities"]["by_horizon"]) == {"1h", "4h", "24h"}
    assert isinstance(body["why_bullish"], list) and isinstance(body["why_bearish"], list)
    assert isinstance(body["risks"], list)
    assert body["trade_plan"]["status"] in {"OK", "NO_TARGET", "NO_DIRECTION"}
    assert {layer["name"] for layer in body["layer_summaries"]} >= {"regime", "liquidity", "smc_ict"}
    assert body["cached"] is False
    assert body["run_id"] is not None
    assert "guarantee" not in body["market_read"]["sentence"].lower()


def test_get_verdict_is_cached_and_persisted(session_factory, session):
    product = _seed(session)
    with _client(session_factory) as client:
        first = client.get("/analysis/verdict?focus_timeframe=15m&horizon=4h")
        second = client.get("/analysis/verdict?focus_timeframe=15m&horizon=4h")
        forced = client.get("/analysis/verdict?focus_timeframe=15m&horizon=4h&force_refresh=true")
        history = client.get("/analysis/verdict/history")
    assert first.status_code == 200 and second.status_code == 200 and forced.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert forced.json()["cached"] is False
    rows = session.scalars(select(VerdictRun).where(VerdictRun.product_id == product.id)).all()
    assert len(rows) == 2
    assert rows[0].payload["verdict"]["verdict"] == rows[0].verdict
    assert history.status_code == 200 and len(history.json()) == 2


def test_concurrent_pollers_share_one_computation(session_factory, session, monkeypatch):
    """While one request recomputes, others reuse the latest result instead of stacking loads."""
    import threading
    import time

    _seed(session)
    calls = {"analyze": 0}
    real_analyze = verdict_service.pipeline.analyze

    def slow_analyze(data, request):
        calls["analyze"] += 1
        time.sleep(0.4)
        return real_analyze(data, request)

    monkeypatch.setattr(verdict_service.pipeline, "analyze", slow_analyze)
    with _client(session_factory) as client:
        first = client.get("/analysis/decision?focus_timeframe=15m&horizon=4h")
        assert first.status_code == 200 and first.json()["cached"] is False
        forced = threading.Thread(target=client.get, args=("/analysis/decision?focus_timeframe=15m&horizon=4h&force_refresh=true",))
        forced.start()
        time.sleep(0.1)
        follower = client.get("/analysis/decision?focus_timeframe=15m&horizon=4h")
        forced.join()
    assert follower.status_code == 200
    assert follower.json()["cached"] is True
    assert calls["analyze"] == 2


def test_new_candle_serves_previous_result_and_refreshes_in_background(session_factory, session):
    import time

    product = _seed(session)
    url = "/analysis/decision?focus_timeframe=15m&horizon=4h"
    with _client(session_factory) as client:
        first = client.get(url).json()
        assert first["cached"] is False and first["refreshing"] is False
        old_watermark = first["candle_watermark"]
        # A new 1m candle closes -> the cached key is invalidated.
        repo = MarketRepository(session)
        repo.upsert_candles(product.id, [_draft(START + 80 * 60, 101, 103, 99, "1m")])
        session.commit()
        started = time.monotonic()
        second = client.get(url).json()
        assert second["cached"] is True and second["refreshing"] is True
        assert second["candle_watermark"] == old_watermark
        # The worker finishes shortly after; the next poll sees the new candle.
        deadline = time.monotonic() + 10
        third = None
        while time.monotonic() < deadline:
            third = client.get(url).json()
            if third["candle_watermark"] != old_watermark:
                break
            time.sleep(0.1)
        assert third is not None and third["candle_watermark"] != old_watermark
        assert third["cached"] is True and third["refreshing"] is False
        assert third["timings_ms"] and set(third["timings_ms"]) == {"load", "engine", "persist"}
        assert time.monotonic() - started < 10
    rows = session.scalars(select(VerdictRun).where(VerdictRun.product_id == product.id)).all()
    assert len(rows) == 2


def test_open_position_yields_hold_or_exit(session_factory, session):
    _seed(session)
    with _client(session_factory) as client:
        response = client.post("/analysis/verdict", json={"position": {"side": "long", "entry_price": 95.0}})
    assert response.status_code == 200
    assert response.json()["verdict"]["verdict"] in {"HOLD", "CONSIDER_EXIT"}
    assert response.json()["verdict"]["position"]["side"] == "long"


def test_validation_rejects_unknown_timeframe_and_bad_risk(session_factory, session):
    _seed(session)
    with _client(session_factory) as client:
        assert client.post("/analysis/verdict", json={"focus_timeframe": "3m"}).status_code == 422
        assert client.post("/analysis/verdict", json={"risk_percent": 50}).status_code == 422
        assert client.get("/analysis/verdict?horizon=2d").status_code == 422


def test_missing_product_is_404_and_disabled_engine_is_404(session_factory):
    with _client(session_factory) as client:
        assert client.post("/analysis/verdict", json={}).status_code == 404
    with _client(session_factory, enable_verdict=False) as client:
        response = client.post("/analysis/verdict", json={})
    assert response.status_code == 404
    assert "disabled" in response.json()["detail"]
