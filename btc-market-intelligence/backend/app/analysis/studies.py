"""Historical condition scans. Each condition is collected on its own. Future bars are not inputs."""

from __future__ import annotations

import bisect
import math

from app.analysis.definitions import (
    ATR_PERCENTILE_WINDOW,
    EXTREME_VOL_PERCENTILE,
    HIGH_VOL_PERCENTILE,
    HORIZONS,
    TAIL_PERCENTILE,
    Bar,
)
from app.analysis.events import breakout_events
from app.analysis.indicators import atr_percent_series, percentile_rank
from app.analysis.stats import quantile
from app.analysis.structure import walk


def structure_and_breakout_events(bars: list[Bar], width: int) -> list[dict]:
    label, _structure_only = walk(bars, width)
    events = breakout_events(bars, width)
    _ = label
    return events


def volatility_events(bars: list[Bar], threshold: float = HIGH_VOL_PERCENTILE) -> list[dict]:
    series = atr_percent_series(bars)
    values: list[float] = []
    armed = True
    events = []
    for bar, value in zip(bars, series):
        if value is None:
            continue
        values.append(value)
        rank = percentile_rank(values)
        if rank is None:
            continue
        if rank >= threshold and armed:
            events.append(
                {
                    "condition": "high_volatility",
                    "open_time": bar.open_time,
                    "close": bar.close,
                    "direction": 0,
                    "atr_percent_percentile": rank,
                }
            )
            armed = False
        elif rank < threshold:
            armed = True
    return events


def extreme_downside_events(bars: list[Bar], lookback: int, percentile: float = TAIL_PERCENTILE) -> list[dict]:
    """An event is the first bar of a run whose log return is at or below the prior window's percentile."""
    history: list[float] = []
    armed = True
    events = []
    for index in range(1, len(bars)):
        previous = bars[index - 1].close
        close = bars[index].close
        if previous <= 0 or close <= 0:
            continue
        current = math.log(close / previous)
        if len(history) >= lookback:
            threshold = quantile(history[-lookback:], percentile)
            matched = threshold is not None and current <= threshold
            if matched and armed:
                events.append(
                    {
                        "condition": "extreme_downside",
                        "open_time": bars[index].open_time,
                        "close": close,
                        "direction": -1,
                        "log_return": current,
                        "threshold": threshold,
                    }
                )
                armed = False
            elif not matched:
                armed = True
        history.append(current)
    return events


def latest_tail_match(bars: list[Bar], lookback: int, percentile: float = TAIL_PERCENTILE) -> dict:
    """Whether the latest closed bar meets the tail rule. The threshold uses only earlier returns."""
    if len(bars) < lookback + 2:
        return {"matched": False, "status": "INSUFFICIENT_HISTORY", "sentence": "Insufficient history to evaluate the tail definition."}
    history = []
    for index in range(1, len(bars) - 1):
        if bars[index - 1].close <= 0 or bars[index].close <= 0:
            continue
        history.append(math.log(bars[index].close / bars[index - 1].close))
    if len(history) < lookback or bars[-2].close <= 0 or bars[-1].close <= 0:
        return {"matched": False, "status": "INSUFFICIENT_HISTORY", "sentence": "Insufficient history to evaluate the tail definition."}
    current = math.log(bars[-1].close / bars[-2].close)
    threshold = quantile(history[-lookback:], percentile)
    matched = threshold is not None and current <= threshold
    if matched:
        sentence = "Current conditions match the historical tail definition."
    else:
        sentence = "Current conditions do not match the historical tail definition."
    return {"matched": matched, "status": "OK", "threshold": threshold, "log_return": current, "sentence": sentence}


def forward_return(event: dict, minutes: dict[int, Bar], step_seconds: int, horizon_seconds: int) -> float | None:
    """Return from the signal close to the 1-minute close exactly one horizon later.

    The forward bar opens at signal_close + horizon - 60 seconds. A later bar is not a substitute.
    """
    if event["close"] <= 0:
        return None
    signal_close = int(event["open_time"].timestamp()) + step_seconds
    target_open = signal_close + horizon_seconds - 60
    forward = minutes.get(target_open)
    if forward is None:
        return None
    return forward.close / event["close"] - 1.0


def partition_events(events: list[dict], minutes: dict[int, Bar], step_seconds: int, horizon_seconds: int) -> dict:
    """Split raw matches into a non-overlapping sample and the counts around it.

    An event with no exact forward minute is excluded and does not consume the cooldown.
    Eligible events keep the earliest one, then the next whose close is at least one horizon later.
    """
    ordered = sorted(events, key=lambda item: item["open_time"])
    eligible = []
    excluded = 0
    for event in ordered:
        if forward_return(event, minutes, step_seconds, horizon_seconds) is None:
            excluded += 1
            continue
        eligible.append(event)
    kept = []
    last_close = None
    for event in eligible:
        close_at = int(event["open_time"].timestamp()) + step_seconds
        if last_close is None or close_at >= last_close + horizon_seconds:
            kept.append(event)
            last_close = close_at
    return {
        "raw_count": len(ordered),
        "independent": kept,
        "independent_count": len(kept),
        "clustered_out": len(eligible) - len(kept),
        "excluded": excluded,
        "method": "cooldown equal to the forward horizon, after dropping matches whose exact forward minute is missing",
    }


def unconditional_returns(bars: list[Bar], minutes: dict[int, Bar], step_seconds: int, horizon_seconds: int) -> dict:
    """Base rate on closed bars of one timeframe. Sampling matches `partition_events`."""
    events = [
        {"condition": "base", "open_time": bar.open_time, "close": bar.close, "direction": 0}
        for bar in bars
    ]
    parts = partition_events(events, minutes, step_seconds, horizon_seconds)
    returns = [forward_return(event, minutes, step_seconds, horizon_seconds) for event in parts["independent"]]
    parts["return"] = [item for item in returns if item is not None]
    return parts


def measure_forward(events: list[dict], minutes: dict[int, Bar], step_seconds: int, horizons=None) -> dict[str, dict]:
    chosen = HORIZONS if horizons is None else horizons
    epochs = sorted(minutes)
    grouped: dict[str, dict[str, dict]] = {}
    for event in events:
        signal_open = int(event["open_time"].timestamp())
        signal_close = signal_open + step_seconds
        entry = event["close"]
        condition = event["condition"]
        bucket = grouped.setdefault(
            condition, {name: {"return": [], "mfe": [], "mae": [], "excluded": 0} for name, _ in chosen}
        )
        for name, seconds in chosen:
            target_open = signal_close + seconds - 60
            forward = minutes.get(target_open)
            if forward is None or entry <= 0:
                bucket[name]["excluded"] += 1
                continue
            start = bisect.bisect_left(epochs, signal_close)
            end = bisect.bisect_right(epochs, target_open)
            path = [minutes[epochs[pos]] for pos in range(start, end)]
            if not path:
                bucket[name]["excluded"] += 1
                continue
            upside = max(bar.high for bar in path) / entry - 1.0
            downside = 1.0 - min(bar.low for bar in path) / entry
            direction = event.get("direction") or 0
            if direction > 0:
                favorable, adverse = upside, downside
            elif direction < 0:
                favorable, adverse = downside, upside
            else:
                favorable, adverse = max(upside, downside), min(upside, downside)
            returned = forward_return(event, minutes, step_seconds, seconds)
            if returned is None:
                bucket[name]["excluded"] += 1
                continue
            bucket[name]["return"].append(returned)
            bucket[name]["mfe"].append(favorable)
            bucket[name]["mae"].append(adverse)
    return grouped


def base_rate_returns(minutes: dict[int, Bar], horizon_seconds: int) -> list[float]:
    """Non-overlapping 1-minute base rate. Prefer `unconditional_returns` for other timeframes."""
    bars = [minutes[key] for key in sorted(minutes)]
    return unconditional_returns(bars, minutes, 60, horizon_seconds)["return"]


def atr_percentile_now(bars: list[Bar]) -> float | None:
    values = [item for item in atr_percent_series(bars) if item is not None]
    if len(values) < ATR_PERCENTILE_WINDOW:
        return None
    return percentile_rank(values)


def volatility_bucket(rank: float | None) -> str:
    if rank is None:
        return "INSUFFICIENT_DATA"
    if rank >= EXTREME_VOL_PERCENTILE:
        return "EXTREME"
    if rank >= HIGH_VOL_PERCENTILE:
        return "HIGH"
    if rank <= 20:
        return "LOW"
    return "NORMAL"
