"""L1. Regime classification and higher-timeframe bias.

Each timeframe gets its own regime. The bias is a weighted vote of the higher timeframes.
"""

from __future__ import annotations

from datetime import timedelta

from app.analysis.definitions import PIVOT_WIDTH, Bar
from app.analysis.engine.types import (
    CAT_STRUCTURE,
    GATE_ANY,
    REGIME_COMPRESSION,
    REGIME_HIGH_VOL,
    REGIME_RANGE,
    REGIME_TREND_DOWN,
    REGIME_TREND_UP,
    REGIME_UNKNOWN,
    STATUS_INSUFFICIENT,
    Evidence,
    LayerResult,
    MarketData,
    clamp,
)
from app.analysis.indicators import (
    atr,
    atr_percent_series,
    bollinger_width_series,
    directional_movement,
    efficiency_ratio,
    ema,
    ema_series,
    hurst_variance_ratio,
    percentile_rank,
)
from app.analysis.structure import walk

ADX_TREND = 25.0
ADX_WEAK = 18.0
SLOPE_SIGNIFICANCE = 0.08
BBW_COMPRESSION_PCT = 15.0
ATR_HIGH_VOL_PCT = 95.0
HTF_WEIGHTS = {"1w": 0.15, "1d": 0.35, "4h": 0.30, "1h": 0.20}
PIVOT_WIDTH_1W = 2


def synthesize_weekly(day_bars: list[Bar]) -> list[Bar]:
    """Build closed ISO-week bars from closed daily bars. The forming week is excluded."""
    if not day_bars:
        return []
    buckets: dict = {}
    order: list = []
    for bar in day_bars:
        key = bar.open_time.isocalendar()[:2]
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(bar)
    out: list[Bar] = []
    latest_key = order[-1]
    latest_day = day_bars[-1].open_time
    week_complete = latest_day.weekday() == 6  # Sunday closed means the week closed
    for key in order:
        days = buckets[key]
        if key == latest_key and not week_complete:
            continue
        monday = days[0].open_time - timedelta(days=days[0].open_time.weekday())
        out.append(
            Bar(
                monday,
                days[0].open,
                max(item.high for item in days),
                min(item.low for item in days),
                days[-1].close,
                sum(item.volume for item in days),
            )
        )
    return out


def classify_timeframe(timeframe: str, bars: list[Bar]) -> dict:
    """Describe one timeframe. Returns regime, direction, and the measurements behind it."""
    closes = [bar.close for bar in bars]
    width = PIVOT_WIDTH.get(timeframe, PIVOT_WIDTH_1W)
    structure = walk(bars, width)[0] if len(bars) > width * 2 else None
    label = "INSUFFICIENT_DATA" if structure is None else structure.label
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    dm = directional_movement(bars)
    atr_value = atr(bars)
    slope_atr = None
    series20 = ema_series(closes, 20)
    if atr_value and len(series20) >= 6 and series20[-1] is not None and series20[-6] is not None:
        slope_atr = (series20[-1] - series20[-6]) / 5.0 / atr_value
    ratio = efficiency_ratio(closes)
    hurst = hurst_variance_ratio(closes)
    bbw = [item for item in bollinger_width_series(closes) if item is not None]
    bbw_pct = percentile_rank(bbw, 100) if len(bbw) >= 100 else None
    atr_pcts = [item for item in atr_percent_series(bars) if item is not None]
    atr_pct = percentile_rank(atr_pcts, 100) if len(atr_pcts) >= 100 else None
    close = closes[-1] if closes else None

    stack = None
    if ema20 is not None and ema50 is not None:
        if ema200 is not None:
            if ema20 > ema50 > ema200:
                stack = "bullish"
            elif ema20 < ema50 < ema200:
                stack = "bearish"
            else:
                stack = "mixed"
        else:
            stack = "bullish" if ema20 > ema50 else "bearish" if ema20 < ema50 else "mixed"

    direction = 0.0
    votes = 0.0
    if label == "BULLISH":
        direction += 1.0
        votes += 1
    elif label == "BEARISH":
        direction -= 1.0
        votes += 1
    elif label == "SIDEWAYS":
        votes += 1
    if stack == "bullish":
        direction += 1.0
        votes += 1
    elif stack == "bearish":
        direction -= 1.0
        votes += 1
    elif stack == "mixed":
        votes += 1
    if dm is not None:
        direction += 1.0 if dm["plus_di"] > dm["minus_di"] else -1.0
        votes += 1
    if slope_atr is not None and abs(slope_atr) >= SLOPE_SIGNIFICANCE:
        direction += 1.0 if slope_atr > 0 else -1.0
        votes += 1
    direction = direction / votes if votes else 0.0

    regime = REGIME_UNKNOWN
    reasons: list[str] = []
    if structure is None or close is None:
        regime = REGIME_UNKNOWN
        reasons.append("not enough closed candles to confirm structure")
    elif atr_pct is not None and atr_pct >= ATR_HIGH_VOL_PCT:
        regime = REGIME_HIGH_VOL
        reasons.append("ATR percent is at or above the 95th percentile of its window")
    elif bbw_pct is not None and bbw_pct <= BBW_COMPRESSION_PCT:
        regime = REGIME_COMPRESSION
        reasons.append("Bollinger width is in the lowest 15 percent of its window")
    else:
        trending = dm is not None and dm["adx"] >= ADX_TREND
        aligned = stack in ("bullish", "bearish")
        structural = label in ("BULLISH", "BEARISH")
        if (trending and (aligned or structural)) or (structural and aligned and dm is not None and dm["adx"] >= ADX_WEAK):
            regime = REGIME_TREND_UP if direction > 0 else REGIME_TREND_DOWN
            reasons.append("ADX %.1f with %s structure and %s EMA stack" % (dm["adx"] if dm else 0.0, label.lower(), stack or "unknown"))
        else:
            regime = REGIME_RANGE
            reasons.append("ADX %s, structure %s, EMA stack %s" % ("%.1f" % dm["adx"] if dm else "unavailable", label.lower(), stack or "unknown"))
    if hurst is not None:
        reasons.append("Hurst %.2f (%s)" % (hurst, "persistent" if hurst > 0.55 else "mean-reverting" if hurst < 0.45 else "random-walk"))

    return {
        "timeframe": timeframe,
        "regime": regime,
        "direction": round(direction, 4),
        "structure_label": label,
        "ema_stack": stack,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "adx": None if dm is None else dm["adx"],
        "plus_di": None if dm is None else dm["plus_di"],
        "minus_di": None if dm is None else dm["minus_di"],
        "ema_slope_atr": slope_atr,
        "efficiency_ratio": ratio,
        "hurst": hurst,
        "bollinger_width_percentile": bbw_pct,
        "atr_percent_percentile": atr_pct,
        "bars": len(bars),
        "reasons": reasons,
    }


def run(data: MarketData) -> LayerResult:
    result = LayerResult(name="regime")
    views: dict[str, dict] = {}
    for timeframe in ("1w", "1d", "4h", "1h", "15m", "5m"):
        bars = data.series(timeframe)
        if not bars:
            continue
        views[timeframe] = classify_timeframe(timeframe, bars)
    if not views:
        result.status = STATUS_INSUFFICIENT
        result.summary = "No closed candles are stored."
        return result

    weight_total = 0.0
    score = 0.0
    for timeframe, weight in HTF_WEIGHTS.items():
        view = views.get(timeframe)
        if view is None or view["regime"] == REGIME_UNKNOWN:
            continue
        weight_total += weight
        score += weight * view["direction"]
    bias_score = score / weight_total if weight_total else 0.0
    if weight_total == 0:
        bias = "UNKNOWN"
    elif bias_score >= 0.34:
        bias = "BULLISH"
    elif bias_score <= -0.34:
        bias = "BEARISH"
    else:
        bias = "RANGING"
    conflicts = {
        name: view["structure_label"]
        for name, view in views.items()
        if name in HTF_WEIGHTS and view["structure_label"] in ("BULLISH", "BEARISH")
    }
    conflicting = len(set(conflicts.values())) > 1

    for timeframe, view in views.items():
        if view["regime"] in (REGIME_TREND_UP, REGIME_TREND_DOWN):
            strength = clamp(abs(view["direction"]) * (0.6 + 0.4 * clamp(((view["adx"] or ADX_TREND) - ADX_WEAK) / 30.0)))
            result.add(
                Evidence(
                    CAT_STRUCTURE,
                    "regime_%s" % timeframe,
                    1.0 if view["regime"] == REGIME_TREND_UP else -1.0,
                    strength,
                    timeframe,
                    "%s is %s: %s." % (timeframe, view["regime"].replace("_", " ").lower(), "; ".join(view["reasons"])),
                    GATE_ANY,
                )
            )
        elif view["structure_label"] in ("BULLISH", "BEARISH"):
            result.add(
                Evidence(
                    CAT_STRUCTURE,
                    "structure_%s" % timeframe,
                    1.0 if view["structure_label"] == "BULLISH" else -1.0,
                    0.35,
                    timeframe,
                    "%s swing structure is %s but the regime reads %s." % (timeframe, view["structure_label"].lower(), view["regime"].lower()),
                    GATE_ANY,
                )
            )

    focus_regime = REGIME_UNKNOWN
    for name in ("1h", "4h", "15m", "1d"):
        if name in views and views[name]["regime"] != REGIME_UNKNOWN:
            focus_regime = views[name]["regime"]
            break
    result.findings = {
        "htf_bias": bias,
        "htf_bias_score": round(bias_score, 4),
        "htf_conflict": conflicting,
        "htf_labels": conflicts,
        "dominant_regime": focus_regime,
        "timeframes": views,
    }
    result.summary = "Higher-timeframe bias is %s (score %.2f)%s. Dominant regime %s." % (
        bias.lower(),
        bias_score,
        " with conflicting daily/4h labels" if conflicting else "",
        focus_regime.replace("_", " ").lower(),
    )
    if conflicting:
        result.warnings.append("Daily and 4h structure labels disagree. They are reported separately and not averaged away.")
    return result
