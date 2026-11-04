"""Rule-based trade decision and live tape view. Deterministic, offline."""

from datetime import timedelta

from fastapi.testclient import TestClient

from app.analysis.engine.decision import ACTION_EXIT, ACTION_HOLD, ACTION_TAKE, ACTION_WAIT, build_decision, indicator_votes
from app.core.timeutil import from_epoch_seconds, utcnow
from app.data.normalizer import draft_from_ohlc
from app.database.repositories import MarketRepository
from app.main import create_app
from app.services import verdict_service
from app.services.live_service import bucket_open, live_view
from tests.conftest import btc_product, make_settings

START = 1_700_000_000


def _view(**overrides):
    base = {
        "rsi": 58.0,
        "macd": {"histogram": 12.0},
        "macd_histogram_change": 1.0,
        "ema_20": 101.0,
        "ema_50": 100.0,
        "ema_200": 95.0,
        "adx": {"adx": 30.0, "plus_di": 25.0, "minus_di": 15.0},
        "bollinger": {"middle": 100.0, "upper": 104.0, "lower": 96.0},
        "volume_zscore": 0.8,
        "obv_slope": 0.4,
    }
    base.update(overrides)
    return base


def _result(verdict="ENTER_NOW", bias="BULLISH", conflict=False, stale=False, p_bull=0.66, confidence=0.62, stage_index=5, rr=2.4, position=None, reasons=None):
    return {
        "focus_timeframe": "15m",
        "close": 102.0,
        "confidence": confidence,
        "verdict": {"verdict": verdict, "reasons": reasons or ["reason"], "risks": ["risk"], "directional_probability": p_bull, "position": position},
        "probabilities": {"p_bull": p_bull, "p_bear": round(0.9 - p_bull, 4), "p_range": 0.1},
        "sequence": {"stage": "IN_ENTRY_ZONE", "stage_index": stage_index, "direction": "long" if bias == "BULLISH" else "short"},
        "htf_bias": {"bias": bias, "conflict": conflict},
        "data_freshness": {"stale": stale},
        "trade_plan": {
            "status": "OK",
            "direction": "long" if bias == "BULLISH" else "short",
            "entry": 101.5,
            "entry_note": "Midpoint of the bullish fvg.",
            "invalidation": 99.0,
            "invalidation_note": "Structural.",
            "targets": [{"price": 107.5, "r_multiple": rr}, {"price": 110.0, "r_multiple": 3.4}],
            "risk_reward_first_target": rr,
            "expected_value_r": 1.1,
            "time_stop": "Exit if no progress in 2 x 4h.",
            "sizing": {"risk_amount": 10.0, "contracts": 4},
        },
    }


def test_indicator_votes_count_agreement_for_a_long():
    votes = indicator_votes(_view(), 1, 102.0)
    assert votes["available"] > 0
    assert votes["agree"] >= 6
    assert votes["disagree"] == 0
    names = {item["indicator"] for item in votes["votes"]}
    assert {"RSI(14)", "MACD histogram", "EMA stack", "EMA200", "ADX / DI", "Bollinger position", "OBV slope"} <= names


def test_indicator_votes_flag_overbought_against_a_long():
    votes = indicator_votes(_view(rsi=74.0), 1, 102.0)
    rsi = next(item for item in votes["votes"] if item["indicator"] == "RSI(14)")
    assert rsi["state"] == "disagree"


def test_enter_now_with_aligned_indicators_is_a_bullish_take_trade():
    decision = build_decision(_result(), _view(), 40.0)
    assert decision["action"] == ACTION_TAKE
    assert decision["direction"] == "BULLISH"
    assert decision["side"] == "BUY"
    assert decision["entry"] == 101.5
    assert decision["stop_loss"] == 99.0
    assert decision["take_profit"][0]["price"] == 107.5
    assert decision["risk_reward"] == 2.4
    assert decision["confidence_label"] == "MEDIUM"
    assert all(item["passed"] for item in decision["checklist"])
    assert "No AI" in decision["note"]


def test_bearish_take_trade_is_labelled_sell():
    decision = build_decision(_result(bias="BEARISH", p_bull=0.24), _view(rsi=42.0, macd={"histogram": -5.0}, ema_20=99.0, ema_50=100.0, ema_200=105.0, adx={"adx": 28.0, "plus_di": 12.0, "minus_di": 26.0}, obv_slope=-0.2), 40.0)
    assert decision["action"] == ACTION_TAKE
    assert decision["direction"] == "BEARISH"
    assert decision["side"] == "SELL"


def test_enter_now_is_blocked_when_indicators_are_outvoted():
    against = _view(rsi=40.0, macd={"histogram": -3.0}, ema_20=99.0, ema_50=100.0, ema_200=105.0, adx={"adx": 28.0, "plus_di": 12.0, "minus_di": 26.0}, obv_slope=-0.2)
    decision = build_decision(_result(), against, 40.0)
    assert decision["action"] == ACTION_WAIT
    assert decision["direction"] == "NONE"
    assert decision["entry"] is None and decision["stop_loss"] is None and decision["take_profit"] == []
    failed = [item["rule"] for item in decision["checklist"] if item["passed"] is False]
    assert "Indicator agreement on the focus timeframe" in failed
    assert any("block" in line for line in decision["explanation"])


def test_extreme_volatility_blocks_the_trade():
    decision = build_decision(_result(), _view(), 97.0)
    assert decision["action"] == ACTION_WAIT
    failed = [item["rule"] for item in decision["checklist"] if item["passed"] is False]
    assert "Volatility is not extreme" in failed


def test_wait_verdict_never_shows_levels():
    decision = build_decision(_result(verdict="WAIT_FOR_BETTER_ENTRY", stage_index=2, p_bull=0.55), _view(), 40.0)
    assert decision["action"] == ACTION_WAIT
    assert decision["entry"] is None
    assert decision["headline"].startswith("WAIT")
    assert any("lean is bullish" in line for line in decision["explanation"])


def test_open_position_maps_to_hold_or_exit():
    position = {"side": "long", "entry_price": 95.0, "size": None, "unrealized_percent": 7.3}
    hold = build_decision(_result(verdict="HOLD", position=position), _view(), 40.0)
    assert hold["action"] == ACTION_HOLD and hold["direction"] == "BULLISH"
    leave = build_decision(_result(verdict="CONSIDER_EXIT", position=position), _view(), 40.0)
    assert leave["action"] == ACTION_EXIT


def test_missing_indicator_view_is_not_counted_as_agreement():
    decision = build_decision(_result(), None, 40.0)
    rule = next(item for item in decision["checklist"] if item["rule"].startswith("Indicator agreement"))
    assert rule["passed"] is None
    assert decision["action"] == ACTION_TAKE
    assert any("not counted as passed" in line for line in decision["explanation"])


# ---- live tape -------------------------------------------------------------------------------

def _draft(epoch: int, close: float, timeframe: str = "1m", closed=True):
    opened = from_epoch_seconds(epoch)
    as_of = opened + timedelta(seconds=60) if closed else opened + timedelta(seconds=10)
    return draft_from_ohlc(
        symbol="BTCUSD",
        timeframe=timeframe,
        open_time=opened,
        timestamp_unit="s",
        exchange_time_raw=str(epoch),
        open_=str(close - 1),
        high=str(close + 2),
        low=str(close - 3),
        close=str(close),
        volume="2",
        source="delta_ws",
        as_of=as_of,
    )


class _Runtime:
    ws_connected = True
    forming_candle = None
    forming_candle_at = None
    mark_price = "101.5"
    mark_price_at = None


def test_bucket_open_floors_to_the_timeframe():
    aligned = START // 900 * 900
    moment = from_epoch_seconds(aligned + 7 * 60 + 13)
    assert bucket_open(moment, "1m") == from_epoch_seconds(aligned + 7 * 60)
    assert bucket_open(moment, "15m") == from_epoch_seconds(aligned)


def test_live_view_builds_the_forming_bar_from_closed_minutes_and_the_socket_minute(session):
    repo = MarketRepository(session)
    product = repo.upsert_product(btc_product())
    base = START // 900 * 900
    repo.upsert_candles(product.id, [_draft(base, 100.0), _draft(base + 60, 102.0)])
    session.commit()
    runtime = _Runtime()
    runtime.forming_candle = {"symbol": "BTCUSD", "timeframe": "1m", "open_time": from_epoch_seconds(base + 120).isoformat(), "open": "102", "high": "105", "low": "101", "close": "104", "volume": "1", "is_closed": False, "source": "delta_ws_forming"}
    runtime.forming_candle_at = from_epoch_seconds(base + 130)
    body = live_view(session, runtime, "15m", now=from_epoch_seconds(base + 135))
    bar = body["forming_candle"]
    assert bar["open"] == "99" and bar["high"] == "105" and bar["low"] == "97" and bar["close"] == "104"
    assert bar["minutes_included"] == 3
    assert bar["is_closed"] is False
    assert body["last_price"] == "104"
    assert body["stale"] is False
    assert body["availability"] == "AVAILABLE"


def test_live_view_without_socket_minute_marks_the_source_and_staleness(session):
    repo = MarketRepository(session)
    product = repo.upsert_product(btc_product())
    base = START // 900 * 900
    repo.upsert_candles(product.id, [_draft(base, 100.0)])
    session.commit()
    body = live_view(session, _Runtime(), "15m", now=from_epoch_seconds(base + 600))
    assert body["last_price_source"] == "stored_closed_1m_close"
    assert body["stale"] is True
    assert body["warnings"]


def test_live_view_is_empty_without_rows(session):
    repo = MarketRepository(session)
    repo.upsert_product(btc_product())
    session.commit()
    body = live_view(session, _Runtime(), "1m", now=utcnow())
    assert body["forming_candle"] is None
    assert body["last_price"] is None
    assert body["availability"] == "NOT_AVAILABLE"


def test_live_and_decision_routes(session_factory, session):
    repo = MarketRepository(session)
    product = repo.upsert_product(btc_product())
    drafts = []
    for timeframe, step in (("1m", 60), ("15m", 900), ("1h", 3600), ("4h", 14400), ("1d", 86400)):
        for index in range(80):
            close = 100 + (index % 7) - 3
            drafts.append(_draft(START + index * step, close, timeframe))
    repo.upsert_candles(product.id, drafts)
    session.commit()
    verdict_service.clear_cache()
    app = create_app(make_settings(), session_factory, engine=None, start_socket=False)
    with TestClient(app) as client:
        live = client.get("/market/live?timeframe=15m")
        assert live.status_code == 200
        assert live.json()["read_only"] is True
        assert client.get("/market/live?timeframe=3m").status_code == 422
        decision = client.get("/analysis/decision?focus_timeframe=15m&horizon=4h")
        assert decision.status_code == 200, decision.text
        body = decision.json()
        assert body["decision"]["action"] in {"TAKE_TRADE", "WAIT", "HOLD", "CONSIDER_EXIT"}
        assert body["decision"]["direction"] in {"BULLISH", "BEARISH", "NONE"}
        assert isinstance(body["decision"]["checklist"], list) and body["decision"]["checklist"]
        assert "candle_watermark" in body
        full = client.post("/analysis/verdict", json={"focus_timeframe": "15m", "horizon": "4h"})
        assert full.status_code == 200
        assert full.json()["decision"]["action"] == body["decision"]["action"]
