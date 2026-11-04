"""Compare an aggregated candle with the exchange's own candle. Differences are recorded, not averaged."""

from __future__ import annotations

from decimal import Decimal

from app.data.normalizer import CandleDraft


def differences(native: CandleDraft, derived: CandleDraft, tick_size: Decimal | None = None) -> dict | None:
    if native.timeframe != derived.timeframe or native.open_time != derived.open_time:
        raise ValueError("candles are not the same slot")
    fields = {}
    for name in ("open", "high", "low", "close", "volume"):
        left = getattr(native, name)
        right = getattr(derived, name)
        if left == right:
            continue
        gap = left - right
        item = {"exchange": format(left, "f"), "aggregate": format(right, "f"), "delta": format(gap, "f")}
        if tick_size is not None and tick_size > 0 and name != "volume":
            item["exceeds_one_tick"] = abs(gap) > tick_size
        fields[name] = item
    if not fields:
        return None
    return {
        "reason": "The exchange candle differs from the aggregate of closed 1-minute candles. The exchange candle was kept.",
        "kept": "exchange",
        "tick_size": None if tick_size is None else format(tick_size, "f"),
        "fields": fields,
    }
