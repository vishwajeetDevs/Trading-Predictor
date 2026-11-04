"""Close-confirmed breakouts, failures, pullbacks, and reversals.

A wick that crosses a level while the close stays on the near side is not a breakout.
"""

from __future__ import annotations

from app.analysis.definitions import FAIL_BARS, Bar
from app.analysis.structure import Pivot, Structure, _strict_extreme, walk


def breakout_events(bars: list[Bar], width: int, fail_bars: int = FAIL_BARS) -> list[dict]:
    """Levels are swing pivots confirmed on an earlier bar. Samples do not repeat while price stays beyond the level."""
    if not bars:
        return []
    _label, structure_events = walk(bars, width)
    highs: list[Pivot] = []
    lows: list[Pivot] = []
    events: list[dict] = []
    watch: dict | None = None
    armed_high = True
    armed_low = True
    for index in range(len(bars)):
        known_high = highs[-1] if highs else None
        known_low = lows[-1] if lows else None
        if watch is not None:
            age = index - watch["index"]
            close = bars[index].close
            crossed_back = close < watch["level"] if watch["direction"] == 1 else close > watch["level"]
            if 0 < age <= fail_bars and crossed_back:
                events.append(
                    {
                        "condition": "failed_breakout",
                        "open_time": bars[index].open_time,
                        "close": close,
                        "direction": -watch["direction"],
                        "level": watch["level"],
                        "breakout_open_time": watch["open_time"],
                    }
                )
                watch = None
                armed_high = True
                armed_low = True
            elif age > fail_bars:
                watch = None
        close = bars[index].close
        if watch is None and known_high is not None and close <= known_high.price:
            armed_high = True
        if watch is None and known_low is not None and close >= known_low.price:
            armed_low = True
        if index > 0 and watch is None:
            previous = bars[index - 1].close
            if known_high is not None and armed_high and previous <= known_high.price < close:
                level = known_high.price
                events.append(
                    {
                        "condition": "confirmed_breakout",
                        "open_time": bars[index].open_time,
                        "close": close,
                        "direction": 1,
                        "level": level,
                    }
                )
                watch = {"index": index, "level": level, "direction": 1, "open_time": bars[index].open_time}
                armed_high = False
            elif known_low is not None and armed_low and previous >= known_low.price > close:
                level = known_low.price
                events.append(
                    {
                        "condition": "confirmed_breakout",
                        "open_time": bars[index].open_time,
                        "close": close,
                        "direction": -1,
                        "level": level,
                    }
                )
                watch = {"index": index, "level": level, "direction": -1, "open_time": bars[index].open_time}
                armed_low = False
        pivot_at = index - width
        if pivot_at >= width and _strict_extreme(bars, pivot_at, width, "high"):
            highs.append(Pivot("high", pivot_at, bars[pivot_at].high, bars[pivot_at].open_time, index, bars[index].open_time))
        if pivot_at >= width and _strict_extreme(bars, pivot_at, width, "low"):
            lows.append(Pivot("low", pivot_at, bars[pivot_at].low, bars[pivot_at].open_time, index, bars[index].open_time))
    events.extend(structure_events)
    return events


def pullback_state(higher: Structure, close: float, previous_close: float | None) -> str:
    """A pullback moves against the higher-timeframe structure without crossing its last confirmed swing."""
    if higher.label == "INSUFFICIENT_DATA" or not higher.swing_highs or not higher.swing_lows or previous_close is None:
        return "NONE"
    last_high = higher.swing_highs[-1].price
    last_low = higher.swing_lows[-1].price
    if higher.label == "BULLISH" and close > last_low and close < previous_close:
        return "PULLBACK"
    if higher.label == "BEARISH" and close < last_high and close > previous_close:
        return "PULLBACK"
    return "NONE"


def reversal_state(events: list[dict]) -> str:
    """A reversal is a confirmed structure label changing between BULLISH and BEARISH."""
    labels = [item["label"] for item in events if item.get("label") in ("BULLISH", "BEARISH")]
    if len(labels) < 2:
        return "NONE"
    if labels[-1] != labels[-2]:
        return "REVERSAL"
    return "NONE"
