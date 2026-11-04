"""L2. Liquidity map and sweep detection.

Liquidity pools are places where resting stop orders are likely: previous period extremes,
session extremes, equal highs/lows, confirmed swings, and round numbers. A sweep is a wick
through a pool followed by a close back inside. A breakout is a close that holds beyond.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.analysis.definitions import PIVOT_WIDTH, Bar
from app.analysis.engine.types import (
    CAT_LIQUIDITY,
    GATE_ANY,
    STATUS_INSUFFICIENT,
    Evidence,
    LayerResult,
    MarketData,
    clamp,
    iso,
)
from app.analysis.indicators import atr
from app.analysis.structure import Pivot, walk

EQUAL_TOLERANCE_ATR = 0.25
SWEEP_RECLAIM_BARS = 3
SWEEP_LOOKBACK_BARS = 60
ROUND_STEPS = (1000.0, 500.0)

# Session boundaries in UTC. Overlaps are intentional; a bar can belong to two sessions.
SESSIONS = {
    "asia": (0, 8),
    "london": (7, 16),
    "new_york": (12, 21),
}


@dataclass
class Pool:
    name: str
    price: float
    side: str  # "above" or "below" current price
    kind: str  # period | session | equal | swing | round
    touches: int = 1
    formed_at: datetime | None = None
    timeframe: str | None = None

    def public(self, close: float, atr_value: float | None) -> dict:
        distance = self.price - close
        return {
            "name": self.name,
            "price": self.price,
            "side": self.side,
            "kind": self.kind,
            "touches": self.touches,
            "timeframe": self.timeframe,
            "formed_at": iso(self.formed_at),
            "distance": distance,
            "distance_percent": None if close <= 0 else distance / close * 100.0,
            "distance_atr": None if not atr_value else distance / atr_value,
        }


def _period_extremes(bars: list[Bar], start: datetime, end: datetime) -> tuple[Bar | None, Bar | None]:
    chosen = [bar for bar in bars if start <= bar.open_time < end]
    if not chosen:
        return None, None
    return max(chosen, key=lambda item: item.high), min(chosen, key=lambda item: item.low)


def period_levels(minute_bars: list[Bar], day_bars: list[Bar], now: datetime) -> dict:
    """Previous day, previous week, previous month highs and lows."""
    out: dict = {}
    if not minute_bars and not day_bars:
        return out
    latest = (minute_bars or day_bars)[-1].open_time
    today = datetime(latest.year, latest.month, latest.day, tzinfo=timezone.utc)
    yesterday = today - timedelta(days=1)
    source = minute_bars if minute_bars else day_bars
    high, low = _period_extremes(source, yesterday, today)
    out["previous_day_high"] = None if high is None else {"price": high.high, "time": high.open_time}
    out["previous_day_low"] = None if low is None else {"price": low.low, "time": low.open_time}
    if day_bars:
        this_monday = today - timedelta(days=today.weekday())
        last_monday = this_monday - timedelta(days=7)
        high, low = _period_extremes(day_bars, last_monday, this_monday)
        out["previous_week_high"] = None if high is None else {"price": high.high, "time": high.open_time}
        out["previous_week_low"] = None if low is None else {"price": low.low, "time": low.open_time}
        month_start = today.replace(day=1)
        last_month_start = (month_start - timedelta(days=1)).replace(day=1)
        high, low = _period_extremes(day_bars, last_month_start, month_start)
        out["previous_month_high"] = None if high is None else {"price": high.high, "time": high.open_time}
        out["previous_month_low"] = None if low is None else {"price": low.low, "time": low.open_time}
    return out


def session_levels(minute_bars: list[Bar]) -> dict:
    """Latest completed range for each session and the current session name."""
    out: dict = {"current": [], "ranges": {}}
    if not minute_bars:
        return out
    latest = minute_bars[-1].open_time
    hour = latest.hour
    for name, (start, end) in SESSIONS.items():
        if start <= hour < end:
            out["current"].append(name)
    today = datetime(latest.year, latest.month, latest.day, tzinfo=timezone.utc)
    for name, (start, end) in SESSIONS.items():
        for day_offset in (0, 1):
            day = today - timedelta(days=day_offset)
            begin = day + timedelta(hours=start)
            finish = day + timedelta(hours=end)
            if finish > latest:
                continue
            high, low = _period_extremes(minute_bars, begin, finish)
            if high is None:
                continue
            out["ranges"][name] = {
                "start": begin,
                "end": finish,
                "high": high.high,
                "low": low.low,
                "high_time": high.open_time,
                "low_time": low.open_time,
            }
            break
    return out


def equal_levels(pivots: list[Pivot], tolerance: float) -> list[dict]:
    """Clusters of pivots within tolerance. The level is anchored to the first pivot in the cluster."""
    clusters: list[dict] = []
    for pivot in sorted(pivots, key=lambda item: item.index):
        placed = False
        for cluster in clusters:
            if abs(pivot.price - cluster["anchor"]) <= tolerance:
                cluster["touches"] += 1
                cluster["last_time"] = pivot.open_time
                cluster["prices"].append(pivot.price)
                placed = True
                break
        if not placed:
            clusters.append({"anchor": pivot.price, "touches": 1, "first_time": pivot.open_time, "last_time": pivot.open_time, "prices": [pivot.price]})
    return [item for item in clusters if item["touches"] >= 2]


def detect_sweeps(bars: list[Bar], pools: list[Pool], lookback: int = SWEEP_LOOKBACK_BARS, reclaim: int = SWEEP_RECLAIM_BARS) -> list[dict]:
    """Find wicks through a pool followed by a close back inside within `reclaim` bars."""
    events: list[dict] = []
    if len(bars) < 2:
        return events
    start = max(1, len(bars) - lookback)
    for pool in pools:
        if pool.formed_at is not None:
            # A pool cannot be swept before it exists.
            first_valid = next((i for i, bar in enumerate(bars) if bar.open_time > pool.formed_at), len(bars))
            begin = max(start, first_valid)
        else:
            begin = start
        for index in range(begin, len(bars)):
            bar = bars[index]
            previous = bars[index - 1]
            # Single-bar sweep: the wick trades through, the close returns inside.
            if bar.low < pool.price <= bar.close and previous.close >= pool.price:
                events.append(_sweep(pool, bars, index, "sell_side", reclaim))
                continue
            if bar.high > pool.price >= bar.close and previous.close <= pool.price:
                events.append(_sweep(pool, bars, index, "buy_side", reclaim))
                continue
            # Multi-bar sweep: a close beyond the level that is reclaimed within `reclaim` bars.
            if bar.close < pool.price <= previous.close:
                for later in range(index + 1, min(len(bars), index + reclaim + 1)):
                    if bars[later].close > pool.price:
                        events.append(_sweep(pool, bars, later, "sell_side", reclaim, swept_index=index))
                        break
            elif bar.close > pool.price >= previous.close:
                for later in range(index + 1, min(len(bars), index + reclaim + 1)):
                    if bars[later].close < pool.price:
                        events.append(_sweep(pool, bars, later, "buy_side", reclaim, swept_index=index))
                        break
    deduped: dict = {}
    for event in events:
        key = (event["pool"], event["side"])
        if key not in deduped or event["reclaimed_at"] > deduped[key]["reclaimed_at"]:
            deduped[key] = event
    return sorted(deduped.values(), key=lambda item: item["reclaimed_at"])


def _sweep(pool: Pool, bars: list[Bar], index: int, side: str, reclaim: int, swept_index: int | None = None) -> dict:
    swept = swept_index if swept_index is not None else index
    extreme = bars[swept].low if side == "sell_side" else bars[swept].high
    return {
        "pool": pool.name,
        "level": pool.price,
        "side": side,
        "swept_at": bars[swept].open_time,
        "reclaimed_at": bars[index].open_time,
        "extreme": extreme,
        "bars_ago": len(bars) - 1 - index,
        "direction": 1 if side == "sell_side" else -1,
    }


def build_pools(data: MarketData, focus: str) -> tuple[list[Pool], dict]:
    bars = data.series(focus)
    close = data.close()
    pools: list[Pool] = []
    detail: dict = {}
    if not bars or close is None:
        return pools, detail
    atr_value = atr(bars) or (close * 0.005)
    periods = period_levels(data.series("1m") or data.minute_bars, data.series("1d"), data.now)
    detail["periods"] = {key: None if value is None else {"price": value["price"], "time": iso(value["time"])} for key, value in periods.items()}
    for name, value in periods.items():
        if value is None:
            continue
        pools.append(Pool(name, value["price"], "above" if value["price"] >= close else "below", "period", 1, value["time"], "1d"))
    sessions = session_levels(data.series("1m") or data.minute_bars)
    detail["sessions"] = {
        "current": sessions["current"],
        "ranges": {
            name: {"start": iso(item["start"]), "end": iso(item["end"]), "high": item["high"], "low": item["low"]}
            for name, item in sessions["ranges"].items()
        },
    }
    for name, item in sessions["ranges"].items():
        pools.append(Pool("%s_session_high" % name, item["high"], "above" if item["high"] >= close else "below", "session", 1, item["high_time"], "1m"))
        pools.append(Pool("%s_session_low" % name, item["low"], "above" if item["low"] >= close else "below", "session", 1, item["low_time"], "1m"))
    width = PIVOT_WIDTH.get(focus, 3)
    structure = walk(bars, width)[0] if len(bars) > width * 2 else None
    equal_detail: list[dict] = []
    if structure is not None:
        highs = list(structure.confirmed_highs)
        lows = list(structure.confirmed_lows)
        tolerance = atr_value * EQUAL_TOLERANCE_ATR
        for cluster in equal_levels(highs, tolerance):
            pools.append(Pool("equal_highs_%.0f" % cluster["anchor"], cluster["anchor"], "above" if cluster["anchor"] >= close else "below", "equal", cluster["touches"], cluster["first_time"], focus))
            equal_detail.append({"type": "equal_highs", "price": cluster["anchor"], "touches": cluster["touches"], "first": iso(cluster["first_time"]), "last": iso(cluster["last_time"])})
        for cluster in equal_levels(lows, tolerance):
            pools.append(Pool("equal_lows_%.0f" % cluster["anchor"], cluster["anchor"], "above" if cluster["anchor"] >= close else "below", "equal", cluster["touches"], cluster["first_time"], focus))
            equal_detail.append({"type": "equal_lows", "price": cluster["anchor"], "touches": cluster["touches"], "first": iso(cluster["first_time"]), "last": iso(cluster["last_time"])})
        for pivot in highs[-6:]:
            pools.append(Pool("swing_high_%.0f" % pivot.price, pivot.price, "above" if pivot.price >= close else "below", "swing", 1, pivot.open_time, focus))
        for pivot in lows[-6:]:
            pools.append(Pool("swing_low_%.0f" % pivot.price, pivot.price, "above" if pivot.price >= close else "below", "swing", 1, pivot.open_time, focus))
    detail["equal_levels"] = equal_detail
    for step in ROUND_STEPS:
        above = (int(close // step) + 1) * step
        below = int(close // step) * step
        if abs(above - close) <= atr_value * 4:
            pools.append(Pool("round_%.0f" % above, above, "above", "round"))
        if below > 0 and abs(close - below) <= atr_value * 4:
            pools.append(Pool("round_%.0f" % below, below, "below", "round"))
    unique: dict = {}
    for pool in pools:
        key = (pool.kind, round(pool.price, 2))
        if key not in unique:
            unique[key] = pool
    return list(unique.values()), detail


def run(data: MarketData, focus: str) -> LayerResult:
    result = LayerResult(name="liquidity")
    bars = data.series(focus)
    close = data.close()
    if not bars or close is None:
        result.status = STATUS_INSUFFICIENT
        result.summary = "No closed candles for %s." % focus
        return result
    atr_value = atr(bars)
    pools, detail = build_pools(data, focus)
    sweeps = detect_sweeps(bars, pools)
    above = sorted((pool for pool in pools if pool.price >= close), key=lambda item: item.price)
    below = sorted((pool for pool in pools if pool.price < close), key=lambda item: -item.price)
    recent = [item for item in sweeps if item["bars_ago"] <= 20]
    for event in recent[-3:]:
        age = clamp(1.0 - event["bars_ago"] / 20.0, 0.2, 1.0)
        result.add(
            Evidence(
                CAT_LIQUIDITY,
                "sweep_%s" % event["pool"],
                float(event["direction"]),
                0.6 * age,
                focus,
                "%s liquidity at %.1f (%s) was swept %d bars ago and price closed back inside. A reclaimed sweep is a possible reversal precursor, not a confirmation."
                % ("Sell-side" if event["side"] == "sell_side" else "Buy-side", event["level"], event["pool"], event["bars_ago"]),
                GATE_ANY,
            )
        )
    nearest_above = above[0] if above else None
    nearest_below = below[0] if below else None
    if nearest_above and nearest_below and atr_value:
        up_distance = (nearest_above.price - close) / atr_value
        down_distance = (close - nearest_below.price) / atr_value
        if up_distance < 0.5 and down_distance > 1.5:
            result.add(Evidence(CAT_LIQUIDITY, "magnet_above", -0.3, 0.3, focus, "Price sits just under %s; a run on that liquidity may precede a reaction." % nearest_above.name, GATE_ANY))
        elif down_distance < 0.5 and up_distance > 1.5:
            result.add(Evidence(CAT_LIQUIDITY, "magnet_below", 0.3, 0.3, focus, "Price sits just above %s; a run on that liquidity may precede a reaction." % nearest_below.name, GATE_ANY))
    result.findings = {
        **detail,
        "pools_above": [pool.public(close, atr_value) for pool in above[:8]],
        "pools_below": [pool.public(close, atr_value) for pool in below[:8]],
        "sweeps": [
            {**event, "swept_at": iso(event["swept_at"]), "reclaimed_at": iso(event["reclaimed_at"])}
            for event in sweeps[-6:]
        ],
        "latest_sweep": None if not sweeps else {**sweeps[-1], "swept_at": iso(sweeps[-1]["swept_at"]), "reclaimed_at": iso(sweeps[-1]["reclaimed_at"])},
    }
    result.summary = "%d pools mapped (%d above, %d below). %s" % (
        len(pools),
        len(above),
        len(below),
        "Latest sweep: %s %s, %d bars ago." % (sweeps[-1]["side"].replace("_", " "), sweeps[-1]["pool"], sweeps[-1]["bars_ago"]) if sweeps else "No recent sweep.",
    )
    return result
