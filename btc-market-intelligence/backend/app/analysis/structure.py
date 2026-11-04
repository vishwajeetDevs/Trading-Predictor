"""Confirmed swing structure. A pivot exists only after the right-side bars have closed."""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.definitions import RANGE_BARS, Bar


@dataclass(frozen=True)
class Pivot:
    kind: str
    index: int
    price: float
    open_time: object
    confirmed_index: int
    confirmed_at: object


@dataclass(frozen=True)
class Structure:
    label: str
    higher_high: bool | None
    higher_low: bool | None
    lower_high: bool | None
    lower_low: bool | None
    overlapping: bool
    range_bound: bool
    swing_highs: tuple[Pivot, ...]
    swing_lows: tuple[Pivot, ...]
    warning: str | None
    confirmed_highs: tuple[Pivot, ...] = ()
    confirmed_lows: tuple[Pivot, ...] = ()


def _strict_extreme(bars: list[Bar], index: int, width: int, kind: str) -> bool:
    if index < width or index + width >= len(bars):
        return False
    if kind == "high":
        price = bars[index].high
        others = [bars[pos].high for pos in range(index - width, index + width + 1) if pos != index]
        return all(price > other for other in others)
    price = bars[index].low
    others = [bars[pos].low for pos in range(index - width, index + width + 1) if pos != index]
    return all(price < other for other in others)


def label_from_pivots(highs: list[Pivot], lows: list[Pivot], bars: list[Bar]) -> Structure:
    if len(highs) < 2 or len(lows) < 2:
        warning = "Fewer than two confirmed swing highs and two confirmed swing lows."
        return Structure(
            "INSUFFICIENT_DATA",
            None,
            None,
            None,
            None,
            False,
            False,
            tuple(highs[-2:]),
            tuple(lows[-2:]),
            warning,
            tuple(highs),
            tuple(lows),
        )
    previous_high, last_high = highs[-2], highs[-1]
    previous_low, last_low = lows[-2], lows[-1]
    higher_high = last_high.price > previous_high.price
    lower_high = last_high.price < previous_high.price
    higher_low = last_low.price > previous_low.price
    lower_low = last_low.price < previous_low.price
    if higher_high and higher_low:
        label = "BULLISH"
        overlapping = False
    elif lower_high and lower_low:
        label = "BEARISH"
        overlapping = False
    else:
        label = "SIDEWAYS"
        overlapping = True
    recent = bars[-RANGE_BARS:] if bars else []
    range_bound = bool(recent) and label == "SIDEWAYS" and all(last_low.price <= bar.close <= last_high.price for bar in recent)
    return Structure(
        label,
        higher_high,
        higher_low,
        lower_high,
        lower_low,
        overlapping,
        range_bound,
        (previous_high, last_high),
        (previous_low, last_low),
        None,
        tuple(highs),
        tuple(lows),
    )


def walk(bars: list[Bar], width: int) -> tuple[Structure, list[dict]]:
    """One forward pass. Events use only pivots whose right side has already closed."""
    if width <= 0:
        raise ValueError("pivot width must be positive")
    highs: list[Pivot] = []
    lows: list[Pivot] = []
    events: list[dict] = []
    last_label = "INSUFFICIENT_DATA"
    for index in range(len(bars)):
        pivot_at = index - width
        if pivot_at >= width and _strict_extreme(bars, pivot_at, width, "high"):
            highs.append(Pivot("high", pivot_at, bars[pivot_at].high, bars[pivot_at].open_time, index, bars[index].open_time))
        if pivot_at >= width and _strict_extreme(bars, pivot_at, width, "low"):
            lows.append(Pivot("low", pivot_at, bars[pivot_at].low, bars[pivot_at].open_time, index, bars[index].open_time))
        tail = bars[max(0, index + 1 - RANGE_BARS) : index + 1]
        current = label_from_pivots(highs, lows, tail)
        if current.label != last_label and current.label != "INSUFFICIENT_DATA":
            events.append(
                {
                    "condition": "%s_structure" % current.label.lower(),
                    "open_time": bars[index].open_time,
                    "close": bars[index].close,
                    "direction": {"BULLISH": 1, "BEARISH": -1}.get(current.label, 0),
                    "label": current.label,
                }
            )
        last_label = current.label
    return label_from_pivots(highs, lows, bars), events


def structure_at(bars: list[Bar], width: int, end: int | None = None) -> Structure:
    view = bars if end is None else bars[:end]
    label, _events = walk(view, width)
    return label
