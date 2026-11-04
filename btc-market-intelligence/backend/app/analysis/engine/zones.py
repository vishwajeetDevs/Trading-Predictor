"""L4. Support and resistance zones, trendlines, fibonacci, pivot points, anchored VWAP, volume profile."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.analysis.definitions import PIVOT_WIDTH, Bar
from app.analysis.engine.types import (
    CAT_LEVELS,
    GATE_ANY,
    GATE_MEAN_REVERSION,
    STATUS_INSUFFICIENT,
    Evidence,
    LayerResult,
    MarketData,
    clamp,
    iso,
)
from app.analysis.indicators import atr, linear_slope, vwap_utc_day
from app.analysis.structure import Pivot, walk

ZONE_BANDWIDTH_ATR = 0.3
ZONE_TIMEFRAMES = ("15m", "1h", "4h", "1d")
TF_WEIGHT = {"15m": 1.0, "1h": 1.5, "4h": 2.5, "1d": 4.0, "1w": 6.0}
FIB_RETRACEMENTS = (0.382, 0.5, 0.618, 0.786)
FIB_EXTENSIONS = (1.272, 1.618)
VALUE_AREA = 0.70


def cluster_zones(pivots: list[tuple[Pivot, str]], bandwidth: float, now_index_by_tf: dict[str, int]) -> list[dict]:
    """Group pivots across timeframes by price. Score = touches x timeframe weight x recency."""
    ordered = sorted(pivots, key=lambda item: item[0].price)
    zones: list[dict] = []
    for pivot, timeframe in ordered:
        if zones and pivot.price - zones[-1]["top"] <= bandwidth:
            zone = zones[-1]
            zone["top"] = max(zone["top"], pivot.price)
            zone["bottom"] = min(zone["bottom"], pivot.price)
            zone["members"].append((pivot, timeframe))
        else:
            zones.append({"top": pivot.price, "bottom": pivot.price, "members": [(pivot, timeframe)]})
    out = []
    for zone in zones:
        score = 0.0
        kinds = set()
        latest = None
        for pivot, timeframe in zone["members"]:
            total = now_index_by_tf.get(timeframe, 1)
            recency = clamp(1.0 - (total - 1 - pivot.index) / max(total, 1), 0.3, 1.0)
            score += TF_WEIGHT.get(timeframe, 1.0) * recency
            kinds.add(pivot.kind)
            if latest is None or pivot.open_time > latest:
                latest = pivot.open_time
        out.append(
            {
                "top": zone["top"],
                "bottom": zone["bottom"],
                "mid": (zone["top"] + zone["bottom"]) / 2.0,
                "touches": len(zone["members"]),
                "timeframes": sorted({tf for _p, tf in zone["members"]}),
                "kinds": sorted(kinds),
                "score": round(score, 3),
                "latest_touch": latest,
            }
        )
    return out


def trendline(pivots: list[Pivot], bars: list[Bar], kind: str, atr_value: float) -> dict | None:
    """Least-squares line through the last 3+ pivots of one kind, validated by no close-through."""
    if len(pivots) < 3 or not atr_value:
        return None
    chosen = pivots[-4:] if len(pivots) >= 4 else pivots[-3:]
    xs = [item.index for item in chosen]
    ys = [item.price for item in chosen]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    violations = 0
    for index in range(chosen[0].index, len(bars)):
        value = slope * index + intercept
        if kind == "low" and bars[index].close < value - atr_value * 0.2:
            violations += 1
        if kind == "high" and bars[index].close > value + atr_value * 0.2:
            violations += 1
    touches = sum(1 for x, y in zip(xs, ys) if abs(y - (slope * x + intercept)) <= atr_value * 0.3)
    current = slope * (len(bars) - 1) + intercept
    return {
        "kind": "support" if kind == "low" else "resistance",
        "slope_per_bar": slope,
        "slope_atr": slope / atr_value,
        "current_value": current,
        "touches": touches,
        "violations": violations,
        "valid": violations == 0 and touches >= 3,
        "from": iso(chosen[0].open_time),
    }


def fibonacci(bars: list[Bar], width: int) -> dict | None:
    structure = walk(bars, width)[0] if len(bars) > width * 2 else None
    if structure is None or not structure.confirmed_highs or not structure.confirmed_lows:
        return None
    high = structure.confirmed_highs[-1]
    low = structure.confirmed_lows[-1]
    bullish = low.index < high.index
    span = high.price - low.price
    if span <= 0:
        return None
    levels = {}
    for ratio in FIB_RETRACEMENTS:
        levels["retrace_%.3f" % ratio] = high.price - span * ratio if bullish else low.price + span * ratio
    for ratio in FIB_EXTENSIONS:
        levels["extension_%.3f" % ratio] = low.price + span * ratio if bullish else high.price - span * ratio
    return {"leg": "bullish" if bullish else "bearish", "high": high.price, "low": low.price, "levels": levels}


def pivot_points(day_bars: list[Bar]) -> dict | None:
    if len(day_bars) < 2:
        return None
    previous = day_bars[-1]
    pivot = (previous.high + previous.low + previous.close) / 3.0
    span = previous.high - previous.low
    return {
        "pivot": pivot,
        "r1": 2 * pivot - previous.low,
        "s1": 2 * pivot - previous.high,
        "r2": pivot + span,
        "s2": pivot - span,
        "r3": previous.high + 2 * (pivot - previous.low),
        "s3": previous.low - 2 * (previous.high - pivot),
        "based_on": iso(previous.open_time),
    }


def anchored_vwap(minute_bars: list[Bar], anchor: datetime) -> float | None:
    weighted = 0.0
    volume = 0.0
    for bar in minute_bars:
        if bar.open_time < anchor:
            continue
        typical = (bar.high + bar.low + bar.close) / 3.0
        weighted += typical * bar.volume
        volume += bar.volume
    return None if volume <= 0 else weighted / volume


def vwap_bands(minute_bars: list[Bar]) -> dict | None:
    if not minute_bars:
        return None
    day = minute_bars[-1].open_time.date()
    rows = [bar for bar in minute_bars if bar.open_time.date() == day]
    vwap = vwap_utc_day(rows)
    if vwap is None:
        return None
    total = sum(bar.volume for bar in rows)
    if total <= 0:
        return None
    variance = sum(bar.volume * (((bar.high + bar.low + bar.close) / 3.0) - vwap) ** 2 for bar in rows) / total
    sigma = variance ** 0.5
    return {"vwap": vwap, "upper_1": vwap + sigma, "lower_1": vwap - sigma, "upper_2": vwap + 2 * sigma, "lower_2": vwap - 2 * sigma}


def volume_profile(minute_bars: list[Bar], low: float, high: float, bins: int = 24) -> dict | None:
    if high <= low or not minute_bars:
        return None
    rows = [bar for bar in minute_bars if low <= bar.close <= high]
    if len(rows) < bins:
        return None
    step = (high - low) / bins
    histogram = [0.0] * bins
    for bar in rows:
        index = min(bins - 1, int((bar.close - low) / step))
        histogram[index] += bar.volume
    total = sum(histogram)
    if total <= 0:
        return None
    poc_index = max(range(bins), key=lambda i: histogram[i])
    covered = histogram[poc_index]
    lower = upper = poc_index
    while covered < total * VALUE_AREA and (lower > 0 or upper < bins - 1):
        left = histogram[lower - 1] if lower > 0 else -1
        right = histogram[upper + 1] if upper < bins - 1 else -1
        if right > left:
            upper += 1
            covered += histogram[upper]
        else:
            lower -= 1
            covered += histogram[lower]
    mean = total / bins
    return {
        "poc": low + (poc_index + 0.5) * step,
        "value_area_high": low + (upper + 1) * step,
        "value_area_low": low + lower * step,
        "high_volume_nodes": [low + (i + 0.5) * step for i in range(bins) if histogram[i] >= mean * 1.5],
        "low_volume_nodes": [low + (i + 0.5) * step for i in range(bins) if histogram[i] <= mean * 0.4],
        "bins": bins,
    }


def run(data: MarketData, focus: str) -> LayerResult:
    result = LayerResult(name="support_resistance")
    bars = data.series(focus)
    close = data.close()
    if not bars or close is None:
        result.status = STATUS_INSUFFICIENT
        result.summary = "No closed candles for %s." % focus
        return result
    atr_value = atr(bars) or close * 0.005
    pivots: list[tuple[Pivot, str]] = []
    counts: dict[str, int] = {}
    for timeframe in ZONE_TIMEFRAMES:
        series = data.series(timeframe)
        width = PIVOT_WIDTH.get(timeframe, 3)
        if len(series) <= width * 2:
            continue
        structure = walk(series, width)[0]
        counts[timeframe] = len(series)
        for pivot in structure.confirmed_highs[-20:]:
            pivots.append((pivot, timeframe))
        for pivot in structure.confirmed_lows[-20:]:
            pivots.append((pivot, timeframe))
    zones = cluster_zones(pivots, atr_value * ZONE_BANDWIDTH_ATR, counts)
    zones.sort(key=lambda item: -item["score"])
    strong = [zone for zone in zones if zone["touches"] >= 2][:12]
    above = sorted((zone for zone in strong if zone["bottom"] > close), key=lambda item: item["bottom"])
    below = sorted((zone for zone in strong if zone["top"] < close), key=lambda item: -item["top"])
    inside = [zone for zone in strong if zone["bottom"] <= close <= zone["top"]]

    width = PIVOT_WIDTH.get(focus, 3)
    focus_structure = walk(bars, width)[0] if len(bars) > width * 2 else None
    support_line = resistance_line = None
    if focus_structure is not None:
        support_line = trendline(list(focus_structure.confirmed_lows), bars, "low", atr_value)
        resistance_line = trendline(list(focus_structure.confirmed_highs), bars, "high", atr_value)
    fib = fibonacci(bars, width)
    day_bars = data.series("1d")
    closed_days = day_bars if not day_bars or day_bars[-1].open_time.date() < data.now.date() else day_bars[:-1]
    pivots_daily = pivot_points(closed_days)
    minute = data.series("1m") or data.minute_bars
    bands = vwap_bands(minute)
    anchor_vwap = None
    if focus_structure is not None and focus_structure.confirmed_lows and focus_structure.confirmed_highs:
        latest_pivot = max(focus_structure.confirmed_lows[-1], focus_structure.confirmed_highs[-1], key=lambda item: item.index)
        anchor_vwap = anchored_vwap(minute, latest_pivot.open_time)
    profile = None
    if fib is not None:
        profile = volume_profile(minute, fib["low"], fib["high"])

    for zone in inside[:1]:
        kinds = zone["kinds"]
        direction = 0.0
        if kinds == ["low"]:
            direction = 0.5
        elif kinds == ["high"]:
            direction = -0.5
        if direction:
            result.add(Evidence(CAT_LEVELS, "inside_zone", direction, clamp(zone["score"] / 10.0, 0.2, 0.6), focus, "Price is inside a %s zone %.1f-%.1f (%d touches on %s)." % ("support" if direction > 0 else "resistance", zone["bottom"], zone["top"], zone["touches"], ", ".join(zone["timeframes"])), GATE_MEAN_REVERSION))
    if below and (close - below[0]["top"]) <= atr_value * 0.75:
        zone = below[0]
        result.add(Evidence(CAT_LEVELS, "near_support", 0.6, clamp(zone["score"] / 10.0, 0.2, 0.6), focus, "Support zone %.1f-%.1f is within 0.75 ATR below price (%d touches)." % (zone["bottom"], zone["top"], zone["touches"]), GATE_MEAN_REVERSION))
    if above and (above[0]["bottom"] - close) <= atr_value * 0.75:
        zone = above[0]
        result.add(Evidence(CAT_LEVELS, "near_resistance", -0.6, clamp(zone["score"] / 10.0, 0.2, 0.6), focus, "Resistance zone %.1f-%.1f is within 0.75 ATR above price (%d touches)." % (zone["bottom"], zone["top"], zone["touches"]), GATE_MEAN_REVERSION))
    for line, sign in ((support_line, 1.0), (resistance_line, -1.0)):
        if line and line["valid"]:
            result.add(Evidence(CAT_LEVELS, "trendline_%s" % line["kind"], sign if abs(close - line["current_value"]) <= atr_value else sign * 0.3, 0.35, focus, "Valid %s trendline (%d touches, slope %.2f ATR/bar) currently at %.1f." % (line["kind"], line["touches"], line["slope_atr"], line["current_value"]), GATE_ANY))
    if bands is not None:
        if close > bands["upper_2"]:
            result.add(Evidence(CAT_LEVELS, "vwap_stretched_high", -0.4, 0.3, "1m", "Price is above the +2 sigma VWAP band.", GATE_MEAN_REVERSION))
        elif close < bands["lower_2"]:
            result.add(Evidence(CAT_LEVELS, "vwap_stretched_low", 0.4, 0.3, "1m", "Price is below the -2 sigma VWAP band.", GATE_MEAN_REVERSION))
        elif close > bands["vwap"]:
            result.add(Evidence(CAT_LEVELS, "above_vwap", 0.3, 0.25, "1m", "Price is above the UTC-day VWAP.", GATE_ANY))
        else:
            result.add(Evidence(CAT_LEVELS, "below_vwap", -0.3, 0.25, "1m", "Price is below the UTC-day VWAP.", GATE_ANY))
    if fib is not None:
        for name, level in fib["levels"].items():
            if name.startswith("retrace") and abs(close - level) <= atr_value * 0.5:
                direction = 0.5 if fib["leg"] == "bullish" else -0.5
                result.add(Evidence(CAT_LEVELS, "fib_%s" % name, direction, 0.3, focus, "Price is at the %s retracement of the last %s leg (%.1f)." % (name.split("_")[1], fib["leg"], level), GATE_ANY))
                break

    def _zone_public(zone: dict) -> dict:
        return {**zone, "latest_touch": iso(zone["latest_touch"])}

    result.findings = {
        "zones_above": [_zone_public(zone) for zone in above[:6]],
        "zones_below": [_zone_public(zone) for zone in below[:6]],
        "zones_inside": [_zone_public(zone) for zone in inside[:2]],
        "trendlines": {"support": support_line, "resistance": resistance_line},
        "fibonacci": fib,
        "pivot_points": pivots_daily,
        "vwap_bands": bands,
        "anchored_vwap": anchor_vwap,
        "volume_profile": profile,
    }
    result.summary = "%d clustered zones (%d above, %d below%s)." % (len(strong), len(above), len(below), ", price inside one" if inside else "")
    return result
