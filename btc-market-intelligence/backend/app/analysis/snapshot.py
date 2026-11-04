"""Live descriptive snapshot. It reads stored study rows and does not resimulate history."""

from __future__ import annotations

from datetime import timedelta

from app.analysis.definitions import HIGHER_TIMEFRAME, PIVOT_WIDTH, STRUCTURE_GROUPS, TIMEFRAMES, Bar
from app.analysis.events import pullback_state, reversal_state
from app.analysis.indicators import atr, latest_indicators
from app.analysis.levels import day_levels, level_row, nearest_swings
from app.analysis.structure import walk
from app.analysis.studies import atr_percentile_now, structure_and_breakout_events, volatility_bucket
from app.core.timeutil import RESOLUTIONS, as_utc


def _iso(value) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat()


def _round(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: _round(item) for key, item in value.items()}
    if isinstance(value, float):
        return round(value, 8)
    return value


def timeframe_view(timeframe: str, bars: list[Bar], higher: dict | None) -> dict:
    warnings = []
    if len(bars) < 2:
        warnings.append("%s does not have enough closed candles." % timeframe)
    indicators = latest_indicators(bars) if bars else {}
    width = PIVOT_WIDTH[timeframe]
    structure, events = walk(bars, width) if len(bars) > width * 2 else (None, [])
    if structure is None:
        label = "INSUFFICIENT_DATA"
        warning = "Not enough closed candles to confirm two swings."
        swings = {"swing_highs": [], "swing_lows": []}
        overlapping = False
        range_bound = False
        flags = {}
    else:
        label = structure.label
        warning = structure.warning
        swings = {
            "swing_highs": [_pivot(item) for item in structure.swing_highs],
            "swing_lows": [_pivot(item) for item in structure.swing_lows],
        }
        overlapping = structure.overlapping
        range_bound = structure.range_bound
        flags = {
            "higher_high": structure.higher_high,
            "higher_low": structure.higher_low,
            "lower_high": structure.lower_high,
            "lower_low": structure.lower_low,
        }
        if warning:
            warnings.append(warning)
    close = bars[-1].close if bars else None
    previous = bars[-2].close if len(bars) > 1 else None
    atr_value = atr(bars) if bars else None
    higher_name = HIGHER_TIMEFRAME.get(timeframe)
    higher_structure = None if higher is None else higher.get("structure")
    pullback = "NONE"
    if higher_structure is not None and close is not None:
        pullback = pullback_state(higher_structure, close, previous)
    recent_events = structure_and_breakout_events(bars, width) if len(bars) > width * 2 else []
    breakout = next((item for item in reversed(recent_events) if item["condition"] == "confirmed_breakout"), None)
    failure = next((item for item in reversed(recent_events) if item["condition"] == "failed_breakout"), None)
    return {
        "timeframe": timeframe,
        "label": label,
        "flags": flags,
        "overlapping": overlapping,
        "range_bound": range_bound,
        "warning": warning,
        "warnings": warnings,
        "swings": swings,
        "swings_note": "The swing list is the last two confirmed highs and lows that define this label. Nearest levels use every confirmed swing in the same closed-bar window.",
        "indicators": _round(indicators),
        "volatility": volatility_bucket(atr_percentile_now(bars) if bars else None),
        "pullback": pullback,
        "reversal": reversal_state(events),
        "latest_breakout": _event(breakout),
        "latest_failed_breakout": _event(failure),
        "higher_timeframe": higher_name,
        "close": close,
        "atr": atr_value,
        "structure_object": structure,
    }


def _pivot(item) -> dict:
    return {"price": item.price, "open_time": _iso(item.open_time), "confirmed_at": _iso(item.confirmed_at)}


def _event(item) -> dict | None:
    if item is None:
        return None
    return {
        "condition": item["condition"],
        "open_time": _iso(item["open_time"]),
        "close": item["close"],
        "direction": "up" if item.get("direction", 0) > 0 else "down" if item.get("direction", 0) < 0 else "none",
        "level": item.get("level"),
    }


def build_levels(timeframe: str, view: dict, day_bars: list[Bar]) -> list[dict]:
    close = view["close"]
    atr_value = view["atr"]
    rows = []
    days = day_levels(day_bars)
    for name, source in (
        ("previous_utc_day_high", "max high of closed 1-minute candles on the previous UTC date"),
        ("previous_utc_day_low", "min low of closed 1-minute candles on the previous UTC date"),
        ("current_utc_day_high", "max high of closed 1-minute candles on the current UTC date"),
        ("current_utc_day_low", "min low of closed 1-minute candles on the current UTC date"),
    ):
        found = days.get(name)
        rows.append(
            level_row(
                name,
                None if found is None else found["price"],
                source,
                "1m" if day_bars else timeframe,
                close or 0,
                atr_value,
                None if found is None else found["open_time"],
            )
        )
    midpoint = days.get("range_midpoint")
    rows.append(
        level_row(
            "range_midpoint",
            None if midpoint is None else midpoint["price"],
            "midpoint of the current UTC day high and low",
            "1m" if day_bars else timeframe,
            close or 0,
            atr_value,
            None if midpoint is None else midpoint["open_time"],
        )
    )
    structure = view["structure_object"]
    if structure is not None and close is not None:
        swings = nearest_swings(structure, close)
        for key, source in (
            ("nearest_swing_high", "lowest confirmed swing high at or above the latest close, from every confirmed swing in this closed-bar window"),
            ("nearest_swing_low", "highest confirmed swing low at or below the latest close, from every confirmed swing in this closed-bar window"),
        ):
            pivot = swings[key]
            rows.append(
                level_row(
                    key,
                    None if pivot is None else pivot.price,
                    source,
                    timeframe,
                    close,
                    atr_value,
                    None if pivot is None else pivot.confirmed_at,
                    None if pivot is None else pivot.open_time,
                )
            )
        high = swings["nearest_swing_high"]
        low = swings["nearest_swing_low"]
        if high is not None and high.price > close:
            rows.append(level_row("breakout_above", high.price, "confirmed swing high above the latest close", timeframe, close, atr_value, high.confirmed_at, high.open_time))
        if low is not None and low.price < close:
            rows.append(level_row("breakout_below", low.price, "confirmed swing low below the latest close", timeframe, close, atr_value, low.confirmed_at, low.open_time))
    for row in rows:
        row.pop("structure_object", None)
    return rows


def comparison(views: dict[str, dict]) -> dict:
    def pack(names):
        return {name: views[name]["label"] if name in views else "INSUFFICIENT_DATA" for name in names}

    return {
        "short_term": pack(STRUCTURE_GROUPS["short_term"]),
        "intraday": pack(STRUCTURE_GROUPS["intraday"]),
        "higher": pack(STRUCTURE_GROUPS["higher"]),
        "note": "Each timeframe keeps its own label. Conflicting labels are not averaged into one direction.",
    }


def public_view(view: dict) -> dict:
    body = dict(view)
    body.pop("structure_object", None)
    body.pop("close", None)
    body.pop("atr", None)
    return body


def empty_snapshot_times() -> dict:
    return {name: [] for name in TIMEFRAMES}


def step_for(timeframe: str) -> timedelta:
    return timedelta(seconds=RESOLUTIONS[timeframe])
