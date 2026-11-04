"""Orchestrates the layers. A failing layer degrades to UNAVAILABLE and the verdict still forms."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable

from app.analysis.engine import decision as decision_rules
from app.analysis.engine import derivatives_evidence, fusion, liquidity, momentum, patterns, regime, risk, sessions, smc, verdict, zones
from app.analysis.engine.types import (
    ENGINE_VERSION,
    REGIME_UNKNOWN,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    AnalyzeRequest,
    LayerResult,
    MarketData,
    clamp,
)
from app.analysis.indicators import atr

log = logging.getLogger(__name__)

VALID_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
VALID_HORIZONS = ("15m", "30m", "1h", "4h", "24h")
HORIZON_SECONDS = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "24h": 86400}


def _guard(name: str, fn: Callable[[], LayerResult]) -> LayerResult:
    started = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - every layer must fail soft
        log.exception("verdict layer %s failed", name)
        result = LayerResult(name=name, status=STATUS_UNAVAILABLE, summary="Layer failed: %s" % type(exc).__name__)
        result.warnings.append(str(exc)[:200])
    result.elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
    return result


def _conditions_for(regime_view: dict | None, smc_findings: dict) -> list[str]:
    conditions: list[str] = []
    for item in smc_findings.get("breaks", [])[-3:]:
        if item.get("bars_ago", 99) <= 10:
            conditions.append("confirmed_breakout")
            break
    label = (regime_view or {}).get("structure_label")
    if label in ("BULLISH", "BEARISH", "SIDEWAYS"):
        conditions.append("%s_structure" % label.lower())
    return conditions


def analyze(data: MarketData, request: AnalyzeRequest) -> dict:
    started = time.perf_counter()
    focus = request.focus_timeframe if request.focus_timeframe in VALID_TIMEFRAMES else "15m"
    horizon = request.horizon if request.horizon in VALID_HORIZONS else "4h"
    close = data.close()
    layers: list[LayerResult] = []

    regime_result = _guard("regime", lambda: regime.run(data))
    layers.append(regime_result)
    bias = regime_result.findings.get("htf_bias", "UNKNOWN")
    htf_conflict = bool(regime_result.findings.get("htf_conflict"))
    regime_views = regime_result.findings.get("timeframes", {})
    focus_regime_view = regime_views.get(focus) or regime_views.get("1h") or regime_views.get("4h")
    dominant_regime = regime_result.findings.get("dominant_regime", REGIME_UNKNOWN)

    liquidity_result = _guard("liquidity", lambda: liquidity.run(data, focus))
    layers.append(liquidity_result)
    sweeps_raw = liquidity_result.findings.get("sweeps", [])
    session_ranges = liquidity_result.findings.get("sessions", {}).get("ranges", {})

    zones_result = _guard("support_resistance", lambda: zones.run(data, focus))
    layers.append(zones_result)

    sessions_result = _guard("sessions", lambda: sessions.run(data))
    layers.append(sessions_result)

    momentum_result = _guard("momentum_volatility_volume", lambda: momentum.run(data, focus))
    layers.append(momentum_result)

    derivatives_result = _guard("derivatives", lambda: derivatives_evidence.run(data))
    layers.append(derivatives_result)

    # SMC needs sweeps (as datetimes) and a session range; rebuild the raw sweep list for the state machine.
    focus_bars = data.series(focus)
    pools, _detail = liquidity.build_pools(data, focus) if focus_bars else ([], {})
    sweeps = liquidity.detect_sweeps(focus_bars, pools) if focus_bars else []
    latest_session = None
    completed = []
    for rng in session_ranges.values():
        if rng and rng.get("end"):
            try:
                completed.append({"high": rng["high"], "low": rng["low"], "end": datetime.fromisoformat(rng["end"])})
            except (TypeError, ValueError):
                continue
    if completed:
        latest_session = max(completed, key=lambda item: item["end"])
    confluence_ok = sessions_result.findings.get("low_liquidity") is False and not htf_conflict
    smc_result = _guard("smc_ict", lambda: smc.run(data, focus, bias, sweeps, latest_session, confluence_ok))
    layers.append(smc_result)

    locations: list[tuple[float, float, str]] = []
    for zone in zones_result.findings.get("zones_above", []) + zones_result.findings.get("zones_below", []) + zones_result.findings.get("zones_inside", []):
        locations.append((zone["bottom"], zone["top"], "support/resistance zone"))
    for zone in smc_result.findings.get("fair_value_gaps", []) + smc_result.findings.get("order_blocks", []):
        if zone.get("state") in ("fresh", "partial", "mitigated"):
            locations.append((zone["bottom"], zone["top"], zone["kind"].replace("_", " ")))
    if close is not None and focus_bars:
        atr_value = atr(focus_bars) or close * 0.005
        for pool in liquidity_result.findings.get("pools_above", [])[:3] + liquidity_result.findings.get("pools_below", [])[:3]:
            locations.append((pool["price"] - atr_value * 0.2, pool["price"] + atr_value * 0.2, pool["name"]))
    patterns_result = _guard("patterns", lambda: patterns.run(data, focus, locations))
    layers.append(patterns_result)

    evidence = [item for layer in layers for item in layer.evidence]
    momentum_focus = momentum_result.findings.get("timeframes", {}).get(focus)
    conditions = _conditions_for(focus_regime_view, smc_result.findings)
    empirical_item, empirical_detail = fusion.empirical_evidence(data.empirical, conditions, horizon, focus)
    if empirical_item is not None:
        evidence.append(empirical_item)
    freshness = 1.0
    stale = False
    if data.health:
        stale = bool(data.health.get("stale"))
        seconds = data.health.get("freshness_seconds")
        if seconds is not None:
            freshness = clamp(1.0 - max(0.0, float(seconds) - 120.0) / 1800.0, 0.2, 1.0)
    range_signal = fusion.range_signal_from(focus_regime_view, momentum_focus)
    fused = fusion.fuse(evidence, dominant_regime, range_signal, layers, freshness)

    probabilities_by_horizon = {}
    for name in ("1h", "4h", "24h"):
        # Longer horizons lean more on higher timeframes and regress toward range.
        tilt = {"1h": -0.03, "4h": 0.0, "24h": 0.04}[name]
        p_range = clamp(fused["p_range"] + tilt, 0.05, 0.6)
        p_dir = fused["p_directional_bull"]
        probabilities_by_horizon[name] = {"p_bull": round((1 - p_range) * p_dir, 4), "p_bear": round((1 - p_range) * (1 - p_dir), 4), "p_range": round(p_range, 4)}

    direction = 1 if bias == "BULLISH" else -1 if bias == "BEARISH" else 0
    sequence = smc_result.findings.get("sequence") or {}
    dealing = smc_result.findings.get("dealing_range")
    entry_zone = sequence.get("entry_zone")
    if entry_zone is None and direction != 0:
        for zone in smc_result.findings.get("fair_value_gaps", []) + smc_result.findings.get("order_blocks", []):
            wanted = "bullish" if direction > 0 else "bearish"
            if zone.get("direction") == wanted and zone.get("state") in ("fresh", "partial"):
                entry_zone = zone
                break
    latest_sweep = liquidity_result.findings.get("latest_sweep")
    sweep_extreme = None
    if latest_sweep and latest_sweep.get("direction") == direction:
        sweep_extreme = latest_sweep.get("extreme")
    opposite_pools = [pool["price"] for pool in (liquidity_result.findings.get("pools_above" if direction > 0 else "pools_below", []))]
    opposite_zones = [zone["mid"] for zone in (zones_result.findings.get("zones_above" if direction > 0 else "zones_below", []))]
    p_direction = fused["p_bull"] if direction > 0 else fused["p_bear"]
    plan = risk.build_plan(
        direction,
        close or 0.0,
        atr(focus_bars) if focus_bars else None,
        entry_zone,
        sweep_extreme,
        opposite_pools,
        opposite_zones,
        p_direction / max(1e-9, (1.0 - fused["p_range"])) if direction else 0.5,
        request.account_size,
        request.risk_percent,
        data.product,
        horizon,
    )

    latest_shift = None
    breaks = smc_result.findings.get("breaks", [])
    if breaks:
        latest_shift = breaks[-1]
    decision = verdict.decide(
        bias=bias,
        htf_conflict=htf_conflict,
        regime=dominant_regime,
        atr_percentile=(focus_regime_view or {}).get("atr_percent_percentile"),
        probabilities=fused,
        confidence=fused["confidence"],
        sequence=sequence,
        plan=plan,
        dealing=dealing,
        stale=stale,
        event_flag=sessions_result.findings.get("high_impact_event_within_2h"),
        position=request.position,
        close=close or 0.0,
        latest_shift=latest_shift,
        latest_sweep=latest_sweep,
    )

    market_read = _market_read(bias, dominant_regime, fused, sequence)
    result = {
        "engine_version": ENGINE_VERSION,
        "focus_timeframe": focus,
        "horizon": horizon,
        "computed_at": data.now.isoformat(),
        "close": close,
        "market_read": market_read,
        "verdict": decision,
        "probabilities": {"primary_horizon": horizon, **fused, "by_horizon": probabilities_by_horizon},
        "confidence": fused["confidence"],
        "why_bullish": fused["why_bullish"],
        "why_bearish": fused["why_bearish"],
        "risks": decision["risks"],
        "what_changes_this_view": decision["what_changes_this_view"],
        "trade_plan": plan,
        "htf_bias": {"bias": bias, "score": regime_result.findings.get("htf_bias_score"), "conflict": htf_conflict, "labels": regime_result.findings.get("htf_labels")},
        "regime": dominant_regime,
        "sequence": sequence,
        "empirical_prior": empirical_detail,
        "layers": [layer.public() for layer in layers],
        "layer_summaries": [{"name": layer.name, "status": layer.status, "summary": layer.summary} for layer in layers],
        "data_freshness": {"stale": stale, "freshness_multiplier": round(freshness, 4), "health": data.health},
        "disclaimer": decision["disclaimer"],
    }
    result["decision"] = decision_rules.build_decision(result, momentum_focus, (focus_regime_view or {}).get("atr_percent_percentile"))
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
    return result


def _market_read(bias: str, regime_name: str, fused: dict, sequence: dict) -> dict:
    p_bull, p_bear, p_range = fused["p_bull"], fused["p_bear"], fused["p_range"]
    if p_range >= max(p_bull, p_bear):
        lean = "SIDEWAYS"
    elif p_bull > p_bear:
        lean = "BULLISH"
    else:
        lean = "BEARISH"
    strength = abs(p_bull - p_bear)
    word = "strong" if strength >= 0.3 else "moderate" if strength >= 0.15 else "slight"
    phrase = "%s %s lean" % (word, lean.lower()) if lean != "SIDEWAYS" else "range-bound"
    seq_direction = sequence.get("direction") if sequence else None
    if sequence and sequence.get("stage") in ("SWEEP", "MSS", "DISPLACEMENT"):
        agrees = (seq_direction == "long" and lean == "BULLISH") or (seq_direction == "short" and lean == "BEARISH")
        if agrees:
            phrase += "; a %s reversal sequence is in progress after a liquidity sweep" % seq_direction
        else:
            phrase += ", but the higher-timeframe playbook is armed %s after a liquidity sweep (conflict)" % seq_direction
    return {
        "lean": lean,
        "strength": word,
        "regime": regime_name,
        "htf_bias": bias,
        "sentence": "Market reads as %s with %.0f%% bullish, %.0f%% bearish and %.0f%% range probability in the %s regime." % (phrase, p_bull * 100, p_bear * 100, p_range * 100, regime_name.replace("_", " ").lower()),
    }
