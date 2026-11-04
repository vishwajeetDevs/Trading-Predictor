"""L10. Evidence fusion in log-odds space with category caps, timeframe weights, and regime gates.

p_bull is a sigmoid of the capped, weighted evidence sum. An empirical prior from the stored
scenario studies is shrunk toward the base rate with a Beta posterior before it joins the sum.
Confidence is agreement x sufficiency x freshness and is reported separately from direction.
"""

from __future__ import annotations

import math

from app.analysis.definitions import MIN_SAMPLE
from app.analysis.engine.types import (
    CAT_EMPIRICAL,
    CAT_LEVELS,
    CAT_LIQUIDITY,
    CAT_MOMENTUM,
    CAT_PATTERNS,
    CAT_SENTIMENT,
    CAT_SESSIONS,
    CAT_STRUCTURE,
    CAT_VOLUME,
    GATE_ANY,
    GATE_MEAN_REVERSION,
    GATE_TREND,
    REGIME_COMPRESSION,
    REGIME_HIGH_VOL,
    REGIME_RANGE,
    REGIME_TREND_DOWN,
    REGIME_TREND_UP,
    STATUS_OK,
    Evidence,
    LayerResult,
    clamp,
)

K_SLOPE = 0.9
CATEGORY_CAPS = {
    CAT_STRUCTURE: 0.25,
    CAT_LIQUIDITY: 0.25,
    CAT_LEVELS: 0.15,
    CAT_MOMENTUM: 0.15,
    CAT_VOLUME: 0.10,
    CAT_PATTERNS: 0.10,
    CAT_SENTIMENT: 0.05,
    CAT_SESSIONS: 0.05,
    CAT_EMPIRICAL: 0.15,
}
TIMEFRAME_WEIGHTS = {"1w": 0.10, "1d": 0.25, "4h": 0.25, "1h": 0.20, "15m": 0.12, "5m": 0.08, "1m": 0.05, "derivatives": 0.15, "clock": 0.05, "empirical": 0.15}
# Multiplier applied to an evidence item by (gate, regime).
REGIME_GATES = {
    (GATE_TREND, REGIME_TREND_UP): 1.0,
    (GATE_TREND, REGIME_TREND_DOWN): 1.0,
    (GATE_TREND, REGIME_RANGE): 0.35,
    (GATE_TREND, REGIME_COMPRESSION): 0.5,
    (GATE_TREND, REGIME_HIGH_VOL): 0.6,
    (GATE_MEAN_REVERSION, REGIME_TREND_UP): 0.4,
    (GATE_MEAN_REVERSION, REGIME_TREND_DOWN): 0.4,
    (GATE_MEAN_REVERSION, REGIME_RANGE): 1.0,
    (GATE_MEAN_REVERSION, REGIME_COMPRESSION): 0.8,
    (GATE_MEAN_REVERSION, REGIME_HIGH_VOL): 0.5,
}
# Scales the capped evidence sum (roughly -1.4..1.4) into a logit. Full agreement maps to about 97%.
LOGIT_SCALE = 3.0
P_FLOOR = 0.03


def sigmoid(value: float) -> float:
    if value >= 0:
        exp = math.exp(-value)
        return 1.0 / (1.0 + exp)
    exp = math.exp(value)
    return exp / (1.0 + exp)


def wilson_interval(successes: float, total: int, z: float = 1.96) -> tuple[float, float] | None:
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return ((centre - margin) / denominator, (centre + margin) / denominator)


def shrink_rate(rate: float | None, sample: int, base_rate: float | None, alpha: int = MIN_SAMPLE) -> dict | None:
    """Beta-posterior shrinkage of a conditional positive rate toward the base rate."""
    if rate is None or sample <= 0:
        return None
    prior = 0.5 if base_rate is None else base_rate
    successes = rate * sample
    posterior = (successes + alpha * prior) / (sample + alpha)
    interval = wilson_interval(successes, sample)
    return {
        "raw_rate": rate,
        "sample_size": sample,
        "base_rate": base_rate,
        "shrunk_rate": posterior,
        "shrinkage_weight": sample / (sample + alpha),
        "wilson_95": None if interval is None else {"low": interval[0], "high": interval[1]},
    }


def empirical_evidence(empirical: dict | None, conditions: list[str], horizon: str, timeframe: str) -> tuple[Evidence | None, dict | None]:
    """Turn the matching stored scenario rows into one prior evidence item."""
    if not empirical:
        return None, None
    chosen = None
    for condition in conditions:
        rows = empirical.get(condition)
        if not rows:
            continue
        row = rows.get(horizon)
        if row and row.get("status") == "OK" and row.get("positive_rate") is not None:
            chosen = (condition, row)
            break
    if chosen is None:
        return None, None
    condition, row = chosen
    shrunk = shrink_rate(float(row["positive_rate"]), int(row.get("sample_size") or 0), None if row.get("base_positive_rate") is None else float(row["base_positive_rate"]))
    if shrunk is None:
        return None, None
    edge = shrunk["shrunk_rate"] - (0.5 if shrunk["base_rate"] is None else shrunk["base_rate"])
    detail = {"condition": condition, "horizon": horizon, "timeframe": timeframe, **shrunk, "edge_vs_base": edge}
    if abs(edge) < 0.02:
        return None, detail
    direction = 1.0 if edge > 0 else -1.0
    strength = clamp(abs(edge) / 0.15) * shrunk["shrinkage_weight"]
    return (
        Evidence(
            CAT_EMPIRICAL,
            "empirical_%s" % condition,
            direction,
            strength,
            "empirical",
            "Stored %s scenarios on %s had a %.0f%% positive rate over %s (n=%d, shrunk to %.0f%% against a %s base)."
            % (condition.replace("_", " "), timeframe, shrunk["raw_rate"] * 100, horizon, shrunk["sample_size"], shrunk["shrunk_rate"] * 100, "%.0f%%" % (shrunk["base_rate"] * 100) if shrunk["base_rate"] is not None else "50%"),
            GATE_ANY,
        ),
        detail,
    )


def fuse(evidence: list[Evidence], regime: str, range_signal: float, layers: list[LayerResult], freshness: float) -> dict:
    """Return probabilities, confidence, and the contribution ledger."""
    per_category: dict[str, float] = {}
    per_category_raw: dict[str, list[dict]] = {}
    bullish: list[dict] = []
    bearish: list[dict] = []
    signed_values: list[float] = []
    for item in evidence:
        if item.strength <= 0:
            continue
        gate = REGIME_GATES.get((item.regime_gate, regime), 1.0)
        weight = TIMEFRAME_WEIGHTS.get(item.timeframe, 0.1)
        contribution = item.signed() * weight * gate
        per_category[item.category] = per_category.get(item.category, 0.0) + contribution
        per_category_raw.setdefault(item.category, []).append(
            {"name": item.name, "timeframe": item.timeframe, "direction": item.direction, "strength": item.strength, "gate_multiplier": gate, "weight": weight, "contribution": contribution, "rationale": item.rationale}
        )
        signed_values.append(item.signed() * gate)
        entry = {"name": item.name, "timeframe": item.timeframe, "contribution": round(contribution, 4), "rationale": item.rationale}
        (bullish if contribution > 0 else bearish).append(entry)
    capped_total = 0.0
    capped: dict[str, float] = {}
    for category, value in per_category.items():
        cap = CATEGORY_CAPS.get(category, 0.05)
        capped[category] = max(-cap, min(cap, value))
        capped_total += capped[category]
    logit = capped_total * LOGIT_SCALE
    p_directional_bull = clamp(sigmoid(K_SLOPE * logit), P_FLOOR, 1.0 - P_FLOOR)
    p_range = clamp(range_signal, 0.05, 0.6)
    p_bull = (1.0 - p_range) * p_directional_bull
    p_bear = (1.0 - p_range) * (1.0 - p_directional_bull)

    if signed_values:
        mean = sum(signed_values) / len(signed_values)
        variance = sum((value - mean) ** 2 for value in signed_values) / len(signed_values)
        dispersion = math.sqrt(variance)
        agreement = clamp(1.0 - dispersion / 0.75)
        magnitude = clamp(abs(mean) / 0.4)
        agreement = clamp(0.5 * agreement + 0.5 * magnitude)
    else:
        agreement = 0.0
    sufficiency = sum(1 for layer in layers if layer.status == STATUS_OK) / max(len(layers), 1)
    confidence = clamp(agreement * sufficiency * clamp(freshness))
    bullish.sort(key=lambda item: -item["contribution"])
    bearish.sort(key=lambda item: item["contribution"])
    return {
        "p_bull": round(p_bull, 4),
        "p_bear": round(p_bear, 4),
        "p_range": round(p_range, 4),
        "p_directional_bull": round(p_directional_bull, 4),
        "logit": round(logit, 4),
        "confidence": round(confidence, 4),
        "confidence_components": {"agreement": round(agreement, 4), "sufficiency": round(sufficiency, 4), "freshness": round(clamp(freshness), 4)},
        "category_contributions": {key: round(value, 4) for key, value in capped.items()},
        "category_uncapped": {key: round(value, 4) for key, value in per_category.items()},
        "category_caps": CATEGORY_CAPS,
        "ledger": per_category_raw,
        "why_bullish": bullish[:8],
        "why_bearish": bearish[:8],
        "evidence_count": len(signed_values),
    }


def range_signal_from(regime_view: dict | None, momentum_view: dict | None) -> float:
    """Estimate how much probability mass belongs to 'range / no resolution'."""
    score = 0.15
    if regime_view:
        regime = regime_view.get("regime")
        if regime == REGIME_RANGE:
            score += 0.25
        elif regime == REGIME_COMPRESSION:
            score += 0.3
        elif regime in (REGIME_TREND_UP, REGIME_TREND_DOWN):
            score -= 0.05
        adx = regime_view.get("adx")
        if adx is not None:
            score += clamp((22.0 - adx) / 40.0, -0.1, 0.15)
        bbw = regime_view.get("bollinger_width_percentile")
        if bbw is not None and bbw <= 20:
            score += 0.1
    if momentum_view and momentum_view.get("squeeze"):
        score += 0.1
    return clamp(score, 0.05, 0.6)
