"""L5. Candlestick patterns, chart patterns from pivot sequences, and oscillator divergence.

A candle pattern is weighted only when it forms at a location of interest (zone, liquidity pool,
FVG, order block, fib level). Without location confluence it is reported but carries little weight.
"""

from __future__ import annotations

from app.analysis.definitions import PIVOT_WIDTH, Bar
from app.analysis.engine.types import (
    CAT_PATTERNS,
    GATE_ANY,
    GATE_MEAN_REVERSION,
    STATUS_INSUFFICIENT,
    Evidence,
    LayerResult,
    MarketData,
    clamp,
    iso,
)
from app.analysis.indicators import atr, ema_series, rsi
from app.analysis.structure import walk

PIN_WICK_RATIO = 2.0
DOJI_BODY_FRACTION = 0.1
PATTERN_LOOKBACK = 5


def _body(bar: Bar) -> float:
    return abs(bar.close - bar.open)


def _range(bar: Bar) -> float:
    return max(bar.high - bar.low, 1e-12)


def _upper_wick(bar: Bar) -> float:
    return bar.high - max(bar.open, bar.close)


def _lower_wick(bar: Bar) -> float:
    return min(bar.open, bar.close) - bar.low


def candle_patterns(bars: list[Bar], lookback: int = PATTERN_LOOKBACK) -> list[dict]:
    """Patterns completed on one of the last `lookback` closed bars."""
    found: list[dict] = []
    if len(bars) < 4:
        return found
    start = max(3, len(bars) - lookback)
    for index in range(start, len(bars)):
        bar = bars[index]
        prev = bars[index - 1]
        prev2 = bars[index - 2]
        body = _body(bar)
        span = _range(bar)
        bars_ago = len(bars) - 1 - index
        if body <= span * DOJI_BODY_FRACTION:
            found.append({"name": "doji", "direction": 0, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
        if _lower_wick(bar) >= body * PIN_WICK_RATIO and _upper_wick(bar) <= body and body > 0:
            found.append({"name": "hammer" if prev.close < prev.open else "bullish_pin_bar", "direction": 1, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
        if _upper_wick(bar) >= body * PIN_WICK_RATIO and _lower_wick(bar) <= body and body > 0:
            found.append({"name": "shooting_star" if prev.close > prev.open else "bearish_pin_bar", "direction": -1, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
        if bar.close > bar.open and prev.close < prev.open and bar.close >= prev.open and bar.open <= prev.close and body > _body(prev):
            found.append({"name": "bullish_engulfing", "direction": 1, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
        if bar.close < bar.open and prev.close > prev.open and bar.close <= prev.open and bar.open >= prev.close and body > _body(prev):
            found.append({"name": "bearish_engulfing", "direction": -1, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
        if bar.high <= prev.high and bar.low >= prev.low:
            found.append({"name": "inside_bar", "direction": 0, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
        if bar.high > prev.high and bar.low < prev.low:
            found.append({"name": "outside_bar", "direction": 1 if bar.close > bar.open else -1, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
        if prev2.close < prev2.open and _body(prev) <= _range(prev) * 0.3 and bar.close > bar.open and bar.close > (prev2.open + prev2.close) / 2:
            found.append({"name": "morning_star", "direction": 1, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
        if prev2.close > prev2.open and _body(prev) <= _range(prev) * 0.3 and bar.close < bar.open and bar.close < (prev2.open + prev2.close) / 2:
            found.append({"name": "evening_star", "direction": -1, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
        if all(item.close > item.open for item in (prev2, prev, bar)) and prev.close > prev2.close and bar.close > prev.close:
            found.append({"name": "three_white_soldiers", "direction": 1, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
        if all(item.close < item.open for item in (prev2, prev, bar)) and prev.close < prev2.close and bar.close < prev.close:
            found.append({"name": "three_black_crows", "direction": -1, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
        tolerance = span * 0.1
        if abs(bar.low - prev.low) <= tolerance and prev.close < prev.open and bar.close > bar.open:
            found.append({"name": "tweezer_bottom", "direction": 1, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
        if abs(bar.high - prev.high) <= tolerance and prev.close > prev.open and bar.close < bar.open:
            found.append({"name": "tweezer_top", "direction": -1, "index": index, "bars_ago": bars_ago, "at": bar.open_time})
    return found


def chart_patterns(bars: list[Bar], width: int, atr_value: float) -> list[dict]:
    """Patterns from the confirmed pivot sequence."""
    found: list[dict] = []
    if len(bars) <= width * 2 or not atr_value:
        return found
    structure = walk(bars, width)[0]
    highs = list(structure.confirmed_highs)
    lows = list(structure.confirmed_lows)
    close = bars[-1].close
    tolerance = atr_value * 0.5
    if len(highs) >= 2 and abs(highs[-1].price - highs[-2].price) <= tolerance and highs[-1].index > highs[-2].index:
        between = [item for item in lows if highs[-2].index < item.index < highs[-1].index]
        if between:
            neck = min(between, key=lambda item: item.price).price
            found.append({"name": "double_top", "direction": -1, "neckline": neck, "target": neck - (highs[-1].price - neck), "confirmed": close < neck, "at": highs[-1].open_time})
    if len(lows) >= 2 and abs(lows[-1].price - lows[-2].price) <= tolerance and lows[-1].index > lows[-2].index:
        between = [item for item in highs if lows[-2].index < item.index < lows[-1].index]
        if between:
            neck = max(between, key=lambda item: item.price).price
            found.append({"name": "double_bottom", "direction": 1, "neckline": neck, "target": neck + (neck - lows[-1].price), "confirmed": close > neck, "at": lows[-1].open_time})
    if len(highs) >= 3:
        left, head, right = highs[-3], highs[-2], highs[-1]
        if head.price > left.price and head.price > right.price and abs(left.price - right.price) <= tolerance * 2:
            between = [item for item in lows if left.index < item.index < right.index]
            if len(between) >= 2:
                neck = (between[0].price + between[-1].price) / 2.0
                found.append({"name": "head_and_shoulders", "direction": -1, "neckline": neck, "target": neck - (head.price - neck), "confirmed": close < neck, "at": right.open_time})
    if len(lows) >= 3:
        left, head, right = lows[-3], lows[-2], lows[-1]
        if head.price < left.price and head.price < right.price and abs(left.price - right.price) <= tolerance * 2:
            between = [item for item in highs if left.index < item.index < right.index]
            if len(between) >= 2:
                neck = (between[0].price + between[-1].price) / 2.0
                found.append({"name": "inverse_head_and_shoulders", "direction": 1, "neckline": neck, "target": neck + (neck - head.price), "confirmed": close > neck, "at": right.open_time})
    if len(highs) >= 3 and len(lows) >= 3:
        high_slope = (highs[-1].price - highs[-3].price) / max(highs[-1].index - highs[-3].index, 1) / atr_value
        low_slope = (lows[-1].price - lows[-3].price) / max(lows[-1].index - lows[-3].index, 1) / atr_value
        flat = 0.02
        if abs(high_slope) <= flat and low_slope > flat:
            found.append({"name": "ascending_triangle", "direction": 1, "resistance": highs[-1].price, "at": max(highs[-1].open_time, lows[-1].open_time)})
        elif abs(low_slope) <= flat and high_slope < -flat:
            found.append({"name": "descending_triangle", "direction": -1, "support": lows[-1].price, "at": max(highs[-1].open_time, lows[-1].open_time)})
        elif high_slope < -flat and low_slope > flat:
            found.append({"name": "symmetrical_triangle", "direction": 0, "at": max(highs[-1].open_time, lows[-1].open_time)})
        elif high_slope > flat and low_slope > flat and low_slope > high_slope:
            found.append({"name": "rising_wedge", "direction": -1, "at": max(highs[-1].open_time, lows[-1].open_time)})
        elif high_slope < -flat and low_slope < -flat and high_slope < low_slope:
            found.append({"name": "falling_wedge", "direction": 1, "at": max(highs[-1].open_time, lows[-1].open_time)})
        elif abs(high_slope) <= flat and abs(low_slope) <= flat and highs[-1].price > lows[-1].price:
            found.append({"name": "range_box", "direction": 0, "top": highs[-1].price, "bottom": lows[-1].price, "at": max(highs[-1].open_time, lows[-1].open_time)})
    return found


def _rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    for index in range(period, len(closes)):
        out[index] = rsi(closes[: index + 1], period)
    return out


def divergences(bars: list[Bar], width: int) -> list[dict]:
    """Regular and hidden RSI divergence between the last two confirmed pivots of a kind."""
    found: list[dict] = []
    if len(bars) <= max(width * 2, 30):
        return found
    structure = walk(bars, width)[0]
    closes = [bar.close for bar in bars]
    osc = _rsi_series(closes[-300:])
    offset = len(closes) - len(osc)

    def value_at(index: int) -> float | None:
        position = index - offset
        return osc[position] if 0 <= position < len(osc) else None

    highs = structure.confirmed_highs[-2:]
    lows = structure.confirmed_lows[-2:]
    if len(highs) == 2:
        a, b = highs
        ra, rb = value_at(a.index), value_at(b.index)
        if ra is not None and rb is not None:
            if b.price > a.price and rb < ra:
                found.append({"name": "regular_bearish_divergence", "direction": -1, "oscillator": "rsi", "at": b.open_time, "bars_ago": len(bars) - 1 - b.index})
            if b.price < a.price and rb > ra:
                found.append({"name": "hidden_bearish_divergence", "direction": -1, "oscillator": "rsi", "at": b.open_time, "bars_ago": len(bars) - 1 - b.index})
    if len(lows) == 2:
        a, b = lows
        ra, rb = value_at(a.index), value_at(b.index)
        if ra is not None and rb is not None:
            if b.price < a.price and rb > ra:
                found.append({"name": "regular_bullish_divergence", "direction": 1, "oscillator": "rsi", "at": b.open_time, "bars_ago": len(bars) - 1 - b.index})
            if b.price > a.price and rb < ra:
                found.append({"name": "hidden_bullish_divergence", "direction": 1, "oscillator": "rsi", "at": b.open_time, "bars_ago": len(bars) - 1 - b.index})
    return found


def run(data: MarketData, focus: str, locations: list[tuple[float, float, str]]) -> LayerResult:
    """`locations` is a list of (bottom, top, label) price bands where a pattern gains confluence."""
    result = LayerResult(name="patterns")
    bars = data.series(focus)
    if len(bars) < 10:
        result.status = STATUS_INSUFFICIENT
        result.summary = "Fewer than 10 closed %s candles." % focus
        return result
    atr_value = atr(bars) or bars[-1].close * 0.005
    width = PIVOT_WIDTH.get(focus, 3)
    candles = candle_patterns(bars)
    charts = chart_patterns(bars, width, atr_value)
    divs = divergences(bars, width)

    def located(bar: Bar) -> str | None:
        for bottom, top, label in locations:
            if bar.low <= top and bar.high >= bottom:
                return label
        return None

    for pattern in candles:
        if pattern["direction"] == 0:
            continue
        bar = bars[pattern["index"]]
        where = located(bar)
        strength = (0.45 if where else 0.12) * clamp(1.0 - pattern["bars_ago"] / PATTERN_LOOKBACK, 0.3, 1.0)
        result.add(
            Evidence(
                CAT_PATTERNS,
                pattern["name"],
                float(pattern["direction"]),
                strength,
                focus,
                "%s %d bar(s) ago%s." % (pattern["name"].replace("_", " ").capitalize(), pattern["bars_ago"], " at %s" % where if where else " without location confluence"),
                GATE_MEAN_REVERSION if "pin" in pattern["name"] or "hammer" in pattern["name"] or "star" in pattern["name"] or "tweezer" in pattern["name"] else GATE_ANY,
            )
        )
    for pattern in charts:
        if pattern["direction"] == 0:
            continue
        confirmed = pattern.get("confirmed", False)
        result.add(
            Evidence(
                CAT_PATTERNS,
                pattern["name"],
                float(pattern["direction"]),
                0.5 if confirmed else 0.25,
                focus,
                "%s%s." % (pattern["name"].replace("_", " ").capitalize(), " confirmed by a close through the neckline" if confirmed else " forming, not yet confirmed"),
                GATE_ANY,
            )
        )
    for item in divs:
        result.add(
            Evidence(
                CAT_PATTERNS,
                item["name"],
                float(item["direction"]),
                0.35 * clamp(1.0 - item["bars_ago"] / 40.0, 0.3, 1.0),
                focus,
                "%s on RSI between the last two confirmed pivots." % item["name"].replace("_", " ").capitalize(),
                GATE_MEAN_REVERSION if item["name"].startswith("regular") else GATE_ANY,
            )
        )
    result.findings = {
        "candlestick": [{**item, "at": iso(item["at"]), "location": located(bars[item["index"]])} for item in candles],
        "chart": [{**item, "at": iso(item["at"])} for item in charts],
        "divergences": [{**item, "at": iso(item["at"])} for item in divs],
    }
    result.summary = "%d candle pattern(s), %d chart pattern(s), %d divergence(s)." % (len(candles), len(charts), len(divs))
    return result
