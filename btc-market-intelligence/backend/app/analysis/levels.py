"""Price levels measured from completed candles. These are not orders or exits."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.analysis.definitions import Bar
from app.analysis.structure import Structure


def _day_extreme(bars: list[Bar], day, pick: str) -> tuple[float, datetime] | None:
    chosen = [bar for bar in bars if bar.open_time.date() == day]
    if not chosen:
        return None
    bar = max(chosen, key=lambda item: item.high) if pick == "high" else min(chosen, key=lambda item: item.low)
    price = bar.high if pick == "high" else bar.low
    return price, bar.open_time


def day_levels(bars: list[Bar]) -> dict:
    if not bars:
        return {}
    current = bars[-1].open_time.date()
    previous = current - timedelta(days=1)
    out = {}
    for name, day, pick in (
        ("previous_utc_day_high", previous, "high"),
        ("previous_utc_day_low", previous, "low"),
        ("current_utc_day_high", current, "high"),
        ("current_utc_day_low", current, "low"),
    ):
        found = _day_extreme(bars, day, pick)
        out[name] = None if found is None else {"price": found[0], "open_time": found[1]}
    high = out.get("current_utc_day_high")
    low = out.get("current_utc_day_low")
    if high and low:
        out["range_midpoint"] = {"price": (high["price"] + low["price"]) / 2.0, "open_time": bars[-1].open_time, "source": "current UTC day high and low"}
    else:
        out["range_midpoint"] = None
    return out


def nearest_swings(structure: Structure, close: float) -> dict:
    highs = structure.confirmed_highs or structure.swing_highs
    lows = structure.confirmed_lows or structure.swing_lows
    above = [item for item in highs if item.price >= close]
    below = [item for item in lows if item.price <= close]
    high = min(above, key=lambda item: item.price) if above else (highs[-1] if highs else None)
    low = max(below, key=lambda item: item.price) if below else (lows[-1] if lows else None)
    return {"nearest_swing_high": high, "nearest_swing_low": low}


def level_row(name: str, price: float | None, source: str, timeframe: str, close: float, atr_value: float | None, confirmed_at, price_time=None) -> dict:
    if price is None or close <= 0:
        return {"name": name, "price": None, "source": source, "timeframe": timeframe, "warning": "Level is not available."}
    distance = price - close
    return {
        "name": name,
        "price": price,
        "source": source,
        "timeframe": timeframe,
        "distance": distance,
        "distance_percent": distance / close * 100.0,
        "distance_atr": None if not atr_value else distance / atr_value,
        "confirmed_at": confirmed_at.isoformat() if isinstance(confirmed_at, datetime) else confirmed_at,
        "price_time": price_time.isoformat() if isinstance(price_time, datetime) else price_time,
    }


def utc_midnight(day) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
