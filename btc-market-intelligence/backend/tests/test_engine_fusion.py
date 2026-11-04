"""Fusion math, the verdict table, and pipeline fault isolation."""

import math
import random
from datetime import datetime, timedelta, timezone

from app.analysis.engine import fusion, pipeline, verdict
from app.analysis.engine.types import (
    CAT_LIQUIDITY,
    CAT_MOMENTUM,
    CAT_STRUCTURE,
    DISCLAIMER,
    GATE_ANY,
    GATE_MEAN_REVERSION,
    GATE_TREND,
    REGIME_RANGE,
    REGIME_TREND_UP,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    AnalyzeRequest,
    Bar,
    Evidence,
    LayerResult,
    MarketData,
    Position,
)

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


def _layers(ok=3, unavailable=0):
    out = [LayerResult(name="l%d" % i, status=STATUS_OK) for i in range(ok)]
    out += [LayerResult(name="u%d" % i, status=STATUS_UNAVAILABLE) for i in range(unavailable)]
    return out


def test_sigmoid_and_wilson_interval():
    assert fusion.sigmoid(0) == 0.5
    assert fusion.sigmoid(50) > 0.999 and fusion.sigmoid(-50) < 0.001
    low, high = fusion.wilson_interval(60, 100)
    assert 0.50 < low < 0.60 < high < 0.70
    assert fusion.wilson_interval(0, 0) is None


def test_shrinkage_pulls_a_small_sample_toward_the_base_rate():
    small = fusion.shrink_rate(0.9, sample=10, base_rate=0.5)
    large = fusion.shrink_rate(0.9, sample=300, base_rate=0.5)
    assert 0.5 < small["shrunk_rate"] < 0.65
    assert large["shrunk_rate"] > 0.85
    assert small["wilson_95"]["low"] < 0.9 < small["wilson_95"]["high"]


def test_probabilities_sum_to_one_and_respect_category_caps():
    evidence = [Evidence(CAT_STRUCTURE, "e%d" % i, 1.0, 1.0, "4h", "test") for i in range(20)]
    fused = fusion.fuse(evidence, REGIME_TREND_UP, range_signal=0.2, layers=_layers(), freshness=1.0)
    total = fused["p_bull"] + fused["p_bear"] + fused["p_range"]
    assert math.isclose(total, 1.0, abs_tol=1e-3)
    assert fused["category_contributions"][CAT_STRUCTURE] == fusion.CATEGORY_CAPS[CAT_STRUCTURE]
    assert fused["category_uncapped"][CAT_STRUCTURE] > fusion.CATEGORY_CAPS[CAT_STRUCTURE]
    assert fused["p_bull"] > fused["p_bear"]
    assert fused["p_directional_bull"] <= 1.0 - fusion.P_FLOOR


def test_regime_gate_suppresses_mean_reversion_in_a_trend():
    item = Evidence(CAT_MOMENTUM, "rsi", -1.0, 1.0, "1h", "test", GATE_MEAN_REVERSION)
    trend = fusion.fuse([item], REGIME_TREND_UP, 0.2, _layers(), 1.0)
    rng = fusion.fuse([item], REGIME_RANGE, 0.2, _layers(), 1.0)
    assert abs(trend["category_uncapped"][CAT_MOMENTUM]) < abs(rng["category_uncapped"][CAT_MOMENTUM])
    trend_item = Evidence(CAT_STRUCTURE, "bos", 1.0, 1.0, "1h", "test", GATE_TREND)
    assert fusion.fuse([trend_item], REGIME_RANGE, 0.2, _layers(), 1.0)["category_uncapped"][CAT_STRUCTURE] < fusion.fuse([trend_item], REGIME_TREND_UP, 0.2, _layers(), 1.0)["category_uncapped"][CAT_STRUCTURE]


def test_confidence_falls_with_disagreement_missing_layers_and_stale_data():
    agree = [Evidence(CAT_STRUCTURE, "a", 1.0, 0.8, "4h", "t"), Evidence(CAT_LIQUIDITY, "b", 1.0, 0.8, "1h", "t")]
    conflict = [Evidence(CAT_STRUCTURE, "a", 1.0, 0.8, "4h", "t"), Evidence(CAT_LIQUIDITY, "b", -1.0, 0.8, "1h", "t")]
    base = fusion.fuse(agree, REGIME_TREND_UP, 0.2, _layers(), 1.0)["confidence"]
    assert fusion.fuse(conflict, REGIME_TREND_UP, 0.2, _layers(), 1.0)["confidence"] < base
    assert fusion.fuse(agree, REGIME_TREND_UP, 0.2, _layers(ok=2, unavailable=2), 1.0)["confidence"] < base
    assert fusion.fuse(agree, REGIME_TREND_UP, 0.2, _layers(), 0.4)["confidence"] < base
    assert fusion.fuse([], REGIME_TREND_UP, 0.2, _layers(), 1.0)["confidence"] == 0.0


def _decide(**overrides):
    base = dict(
        bias="BULLISH",
        htf_conflict=False,
        regime=REGIME_TREND_UP,
        atr_percentile=50.0,
        probabilities={"p_bull": 0.65, "p_bear": 0.2, "p_range": 0.15},
        confidence=0.7,
        sequence={"stage": "IN_ENTRY_ZONE", "stage_index": 5, "direction": "long", "missing": ["CONFLUENCE_CHECKED"], "price_side_ok": True},
        plan={"status": "OK", "direction": "long", "invalidation": 95.0, "risk_reward_first_target": 2.5, "targets": [{"price": 110.0, "r_multiple": 2.5}]},
        dealing={"zone": "discount"},
        stale=False,
        event_flag=None,
        position=None,
        close=100.0,
        latest_shift=None,
        latest_sweep=None,
    )
    base.update(overrides)
    return verdict.decide(**base)


def test_enter_now_when_sequence_probability_confidence_and_rr_all_pass():
    out = _decide()
    assert out["verdict"] == "ENTER_NOW"
    assert out["disclaimer"] == DISCLAIMER
    assert out["reasons"] and out["what_changes_this_view"]


def test_wait_when_the_sequence_has_not_reached_the_entry_zone():
    out = _decide(sequence={"stage": "SWEEP", "stage_index": 2, "direction": "long", "missing": ["MSS", "DISPLACEMENT"], "price_side_ok": False})
    assert out["verdict"] == "WAIT_FOR_BETTER_ENTRY"
    assert any("missing" in reason for reason in out["reasons"])


def test_wait_when_rr_is_too_small_even_with_a_good_setup():
    out = _decide(plan={"status": "OK", "direction": "long", "invalidation": 95.0, "risk_reward_first_target": 1.2, "targets": []})
    assert out["verdict"] == "WAIT_FOR_BETTER_ENTRY"
    assert any("1.2" in risk for risk in out["risks"])


def test_avoid_on_extreme_volatility_or_low_confidence_or_conflict():
    assert _decide(atr_percentile=97.0)["verdict"] == "AVOID_HIGH_RISK"
    assert _decide(confidence=0.2)["verdict"] == "AVOID_HIGH_RISK"
    assert _decide(htf_conflict=True)["verdict"] == "AVOID_HIGH_RISK"
    assert _decide(stale=True)["verdict"] == "AVOID_HIGH_RISK"
    ranging = _decide(bias="RANGING", confidence=0.2)
    assert ranging["verdict"] == "AVOID_HIGH_RISK"
    assert _decide(bias="RANGING")["verdict"] == "WAIT_FOR_BETTER_ENTRY"


def test_hold_and_consider_exit_for_an_open_position():
    held = _decide(position=Position(side="long", entry_price=90.0))
    assert held["verdict"] == "HOLD"
    assert held["position"]["unrealized_percent"] > 0
    flipped = _decide(position=Position(side="long", entry_price=90.0), probabilities={"p_bull": 0.3, "p_bear": 0.55, "p_range": 0.15})
    assert flipped["verdict"] == "CONSIDER_EXIT"
    shifted = _decide(position=Position(side="long", entry_price=90.0), latest_shift={"kind": "MSS", "direction": -1, "bars_ago": 2})
    assert shifted["verdict"] == "CONSIDER_EXIT"
    swept = _decide(position=Position(side="short", entry_price=120.0), bias="BEARISH", probabilities={"p_bull": 0.2, "p_bear": 0.65, "p_range": 0.15}, latest_sweep={"direction": 1, "bars_ago": 1})
    assert swept["verdict"] == "CONSIDER_EXIT"


def _synthetic(seconds, count, seed=3):
    rng = random.Random(seed)
    out = []
    price = 100000.0
    t = NOW - timedelta(seconds=seconds * count)
    for _ in range(count):
        change = rng.gauss(0.0, 0.003)
        close = price * (1 + change)
        high = max(price, close) * (1 + abs(rng.gauss(0, 0.001)))
        low = min(price, close) * (1 - abs(rng.gauss(0, 0.001)))
        out.append(Bar(t, price, high, low, close, rng.uniform(10, 100)))
        price = close
        t += timedelta(seconds=seconds)
    return out


def test_pipeline_runs_end_to_end_on_synthetic_bars_and_serializes():
    import json

    bars = {"1m": _synthetic(60, 600), "5m": _synthetic(300, 400), "15m": _synthetic(900, 400), "1h": _synthetic(3600, 300), "4h": _synthetic(14400, 250), "1d": _synthetic(86400, 120)}
    data = MarketData(bars=bars, minute_bars=bars["1m"], now=NOW, health={"stale": False, "freshness_seconds": 30})
    out = pipeline.analyze(data, AnalyzeRequest(focus_timeframe="15m", horizon="4h", account_size=5000.0, risk_percent=1.0))
    json.dumps(out)
    assert out["verdict"]["verdict"] in {"ENTER_NOW", "WAIT_FOR_BETTER_ENTRY", "AVOID_HIGH_RISK", "HOLD", "CONSIDER_EXIT"}
    assert math.isclose(out["probabilities"]["p_bull"] + out["probabilities"]["p_bear"] + out["probabilities"]["p_range"], 1.0, abs_tol=1e-3)
    assert 0.0 <= out["confidence"] <= 1.0
    assert {layer["name"] for layer in out["layers"]} >= {"regime", "liquidity", "smc_ict", "support_resistance", "patterns", "momentum_volatility_volume", "sessions", "derivatives"}
    assert next(layer for layer in out["layers"] if layer["name"] == "derivatives")["status"] == STATUS_UNAVAILABLE
    assert out["disclaimer"] == DISCLAIMER
    assert out["trade_plan"]["status"] in {"OK", "NO_TARGET", "NO_DIRECTION"}


def test_pipeline_survives_empty_data_and_a_crashing_layer(monkeypatch):
    data = MarketData(bars={}, minute_bars=[], now=NOW)
    out = pipeline.analyze(data, AnalyzeRequest())
    assert out["verdict"]["verdict"] in {"AVOID_HIGH_RISK", "WAIT_FOR_BETTER_ENTRY"}
    assert out["confidence"] == 0.0

    def boom(*_args, **_kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(pipeline.regime, "run", boom)
    bars = {"15m": _synthetic(900, 300)}
    out = pipeline.analyze(MarketData(bars=bars, minute_bars=[], now=NOW), AnalyzeRequest())
    failed = next(layer for layer in out["layers"] if layer["name"] == "regime")
    assert failed["status"] == STATUS_UNAVAILABLE
    assert "synthetic failure" in failed["warnings"][0]
    assert out["verdict"]["verdict"] in {"AVOID_HIGH_RISK", "WAIT_FOR_BETTER_ENTRY"}
