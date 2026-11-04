"""L3. Smart-money / ICT constructs on closed candles.

Displacement, break of structure, change of character, market structure shift, fair value
gaps, order blocks, dealing range premium/discount, power of three, and the playbook
sequence state machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

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
from app.analysis.structure import Pivot, _strict_extreme, walk

DISPLACEMENT_BASELINE = 20
DISPLACEMENT_MULT = 1.5
DISPLACEMENT_BODY_FRACTION = 0.6
FVG_MIN_ATR = 0.1
ZONE_AGE_LIMIT = 200
OTE_LOW = 0.618
OTE_HIGH = 0.79

SEQUENCE = (
    "HTF_BIAS",
    "LIQUIDITY_IDENTIFIED",
    "SWEEP",
    "MSS",
    "DISPLACEMENT",
    "IN_ENTRY_ZONE",
    "CONFLUENCE_CHECKED",
)


@dataclass
class Zone:
    kind: str  # fvg | order_block | breaker
    direction: int  # 1 bullish, -1 bearish
    top: float
    bottom: float
    formed_index: int
    formed_at: datetime
    state: str = "fresh"  # fresh | partial | mitigated | filled | inverted | invalidated
    touched_at: datetime | None = None
    extra: dict = field(default_factory=dict)

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def public(self, close: float) -> dict:
        return {
            "kind": self.kind,
            "direction": "bullish" if self.direction > 0 else "bearish",
            "top": self.top,
            "bottom": self.bottom,
            "midpoint": self.midpoint,
            "state": self.state,
            "formed_at": iso(self.formed_at),
            "touched_at": iso(self.touched_at),
            "distance_percent": None if close <= 0 else (self.midpoint - close) / close * 100.0,
            **self.extra,
        }


def is_displacement(bars: list[Bar], index: int, baseline: int = DISPLACEMENT_BASELINE, mult: float = DISPLACEMENT_MULT, fraction: float = DISPLACEMENT_BODY_FRACTION) -> bool:
    if index < baseline or index >= len(bars):
        return False
    bar = bars[index]
    body = abs(bar.close - bar.open)
    span = bar.high - bar.low
    if body <= 0 or span <= 0:
        return False
    mean_body = sum(abs(item.close - item.open) for item in bars[index - baseline : index]) / baseline
    if mean_body <= 0:
        return False
    return body >= mult * mean_body and body / span >= fraction


def detect_fvgs(bars: list[Bar], atr_value: float, min_atr: float = FVG_MIN_ATR, age_limit: int = ZONE_AGE_LIMIT) -> list[Zone]:
    zones: list[Zone] = []
    start = max(2, len(bars) - age_limit)
    for index in range(start, len(bars)):
        first, third = bars[index - 2], bars[index]
        if third.low > first.high and third.low - first.high >= atr_value * min_atr:
            zones.append(Zone("fvg", 1, third.low, first.high, index, bars[index - 1].open_time))
        elif third.high < first.low and first.low - third.high >= atr_value * min_atr:
            zones.append(Zone("fvg", -1, first.low, third.high, index, bars[index - 1].open_time))
    for zone in zones:
        _track_fvg(zone, bars)
    return zones


def _track_fvg(zone: Zone, bars: list[Bar]) -> None:
    for index in range(zone.formed_index + 1, len(bars)):
        bar = bars[index]
        if zone.direction > 0:
            if bar.close < zone.bottom:
                zone.state = "inverted"
                zone.touched_at = bar.open_time
                return
            if bar.low <= zone.bottom:
                zone.state = "filled"
                zone.touched_at = bar.open_time
            elif bar.low <= zone.top and zone.state == "fresh":
                zone.state = "partial"
                zone.touched_at = bar.open_time
        else:
            if bar.close > zone.top:
                zone.state = "inverted"
                zone.touched_at = bar.open_time
                return
            if bar.high >= zone.top:
                zone.state = "filled"
                zone.touched_at = bar.open_time
            elif bar.high >= zone.bottom and zone.state == "fresh":
                zone.state = "partial"
                zone.touched_at = bar.open_time


def structure_breaks(bars: list[Bar], width: int) -> list[dict]:
    """BOS / CHoCH / MSS events from closes through confirmed pivots."""
    events: list[dict] = []
    highs: list[Pivot] = []
    lows: list[Pivot] = []
    trend = 0
    for index in range(len(bars)):
        bar = bars[index]
        if index > 0:
            previous = bars[index - 1]
            last_high = highs[-1] if highs else None
            last_low = lows[-1] if lows else None
            if last_high is not None and previous.close <= last_high.price < bar.close:
                displaced = is_displacement(bars, index)
                kind = "BOS" if trend >= 0 else ("MSS" if displaced else "CHoCH")
                events.append({"kind": kind, "direction": 1, "index": index, "open_time": bar.open_time, "level": last_high.price, "displacement": displaced, "pivot_index": last_high.index})
                trend = 1
                highs = [item for item in highs if item.price > last_high.price] or []
            if last_low is not None and previous.close >= last_low.price > bar.close:
                displaced = is_displacement(bars, index)
                kind = "BOS" if trend <= 0 else ("MSS" if displaced else "CHoCH")
                events.append({"kind": kind, "direction": -1, "index": index, "open_time": bar.open_time, "level": last_low.price, "displacement": displaced, "pivot_index": last_low.index})
                trend = -1
                lows = [item for item in lows if item.price < last_low.price] or []
        pivot_at = index - width
        if pivot_at >= width:
            if _strict_extreme(bars, pivot_at, width, "high"):
                highs.append(Pivot("high", pivot_at, bars[pivot_at].high, bars[pivot_at].open_time, index, bar.open_time))
            if _strict_extreme(bars, pivot_at, width, "low"):
                lows.append(Pivot("low", pivot_at, bars[pivot_at].low, bars[pivot_at].open_time, index, bar.open_time))
    return events


def detect_order_blocks(bars: list[Bar], breaks: list[dict], age_limit: int = ZONE_AGE_LIMIT) -> list[Zone]:
    """Last opposite-close candle before the displacement leg that broke structure."""
    zones: list[Zone] = []
    for event in breaks:
        if not event["displacement"]:
            continue
        index = event["index"]
        if len(bars) - index > age_limit:
            continue
        direction = event["direction"]
        candidate = None
        for back in range(index - 1, max(index - 15, event["pivot_index"], 0) - 1, -1):
            bar = bars[back]
            if direction > 0 and bar.close < bar.open:
                candidate = back
                break
            if direction < 0 and bar.close > bar.open:
                candidate = back
                break
        if candidate is None:
            continue
        bar = bars[candidate]
        body_mid = (bar.open + bar.close) / 2.0
        zone = Zone("order_block", direction, bar.high, bar.low, candidate, bar.open_time, extra={"mean_threshold": body_mid, "break_kind": event["kind"]})
        _track_block(zone, bars, index)
        zones.append(zone)
    return zones


def _track_block(zone: Zone, bars: list[Bar], from_index: int) -> None:
    for index in range(from_index + 1, len(bars)):
        bar = bars[index]
        if zone.direction > 0:
            if bar.close < zone.bottom:
                zone.state = "invalidated"
                zone.kind = "breaker"
                zone.direction = -1
                zone.touched_at = bar.open_time
                return
            if bar.low <= zone.top:
                zone.state = "mitigated"
                zone.touched_at = bar.open_time
        else:
            if bar.close > zone.top:
                zone.state = "invalidated"
                zone.kind = "breaker"
                zone.direction = 1
                zone.touched_at = bar.open_time
                return
            if bar.high >= zone.bottom:
                zone.state = "mitigated"
                zone.touched_at = bar.open_time


def dealing_range(bars: list[Bar], width: int) -> dict | None:
    structure = walk(bars, width)[0] if len(bars) > width * 2 else None
    if structure is None or not structure.confirmed_highs or not structure.confirmed_lows:
        return None
    high = max(structure.confirmed_highs[-3:], key=lambda item: item.price)
    low = min(structure.confirmed_lows[-3:], key=lambda item: item.price)
    if high.price <= low.price:
        return None
    close = bars[-1].close
    span = high.price - low.price
    position = (close - low.price) / span
    equilibrium = low.price + span * 0.5
    bullish_leg = low.index < high.index
    if bullish_leg:
        ote_top = high.price - span * OTE_LOW
        ote_bottom = high.price - span * OTE_HIGH
    else:
        ote_bottom = low.price + span * OTE_LOW
        ote_top = low.price + span * OTE_HIGH
    return {
        "high": high.price,
        "low": low.price,
        "high_time": high.open_time,
        "low_time": low.open_time,
        "equilibrium": equilibrium,
        "position": position,
        "zone": "premium" if position > 0.5 else "discount",
        "leg": "bullish" if bullish_leg else "bearish",
        "ote_top": max(ote_top, ote_bottom),
        "ote_bottom": min(ote_top, ote_bottom),
        "in_ote": min(ote_top, ote_bottom) <= close <= max(ote_top, ote_bottom),
    }


def power_of_three(minute_bars: list[Bar], session_range: dict | None) -> dict | None:
    """Accumulation (inside range), manipulation (sweep one side), distribution (close through other side)."""
    if session_range is None or not minute_bars:
        return None
    high = session_range["high"]
    low = session_range["low"]
    after = [bar for bar in minute_bars if bar.open_time >= session_range["end"]]
    if not after:
        return {"stage": "accumulation", "range_high": high, "range_low": low}
    swept_low = any(bar.low < low for bar in after)
    swept_high = any(bar.high > high for bar in after)
    last = after[-1]
    if swept_low and last.close > high:
        return {"stage": "distribution", "direction": 1, "range_high": high, "range_low": low}
    if swept_high and last.close < low:
        return {"stage": "distribution", "direction": -1, "range_high": high, "range_low": low}
    if swept_low and last.close > low:
        return {"stage": "manipulation", "direction": 1, "range_high": high, "range_low": low}
    if swept_high and last.close < high:
        return {"stage": "manipulation", "direction": -1, "range_high": high, "range_low": low}
    return {"stage": "accumulation", "range_high": high, "range_low": low}


def playbook_sequence(bias: str, sweeps: list[dict], breaks: list[dict], zones: list[Zone], close: float, dealing: dict | None, confluence_ok: bool) -> dict:
    """Walk the user's playbook and report the furthest stage reached in the bias direction."""
    direction = 1 if bias == "BULLISH" else -1 if bias == "BEARISH" else 0
    reached = []
    missing = []
    if direction == 0:
        return {"stage": None, "reached": [], "missing": list(SEQUENCE), "direction": "none", "note": "Higher-timeframe bias is ranging or unknown, so the directional playbook is not armed."}
    reached.append("HTF_BIAS")
    reached.append("LIQUIDITY_IDENTIFIED")
    recent_sweep = next((item for item in reversed(sweeps) if item["direction"] == direction and item["bars_ago"] <= 40), None)
    if recent_sweep:
        reached.append("SWEEP")
    else:
        missing.append("SWEEP")
    sweep_index_cut = None
    shift = None
    for event in reversed(breaks):
        if event["direction"] == direction and event["kind"] in ("MSS", "CHoCH", "BOS"):
            shift = event
            break
    if shift is not None and (recent_sweep is None or shift["open_time"] >= recent_sweep["swept_at"]):
        reached.append("MSS")
        if shift["displacement"]:
            reached.append("DISPLACEMENT")
        else:
            missing.append("DISPLACEMENT")
    else:
        missing.extend(["MSS", "DISPLACEMENT"])
    entry_zone = None
    for zone in zones:
        if zone.direction == direction and zone.state in ("fresh", "partial", "mitigated") and zone.contains(close):
            entry_zone = zone
            break
    if entry_zone is not None and "MSS" in reached:
        reached.append("IN_ENTRY_ZONE")
    else:
        missing.append("IN_ENTRY_ZONE")
    side_ok = dealing is not None and ((direction > 0 and dealing["zone"] == "discount") or (direction < 0 and dealing["zone"] == "premium"))
    if "IN_ENTRY_ZONE" in reached and confluence_ok and side_ok:
        reached.append("CONFLUENCE_CHECKED")
    else:
        missing.append("CONFLUENCE_CHECKED")
    stage = reached[-1]
    return {
        "stage": stage,
        "stage_index": SEQUENCE.index(stage),
        "reached": reached,
        "missing": missing,
        "direction": "long" if direction > 0 else "short",
        "sweep": None if recent_sweep is None else {**recent_sweep, "swept_at": iso(recent_sweep["swept_at"]), "reclaimed_at": iso(recent_sweep["reclaimed_at"])},
        "shift": None if shift is None else {"kind": shift["kind"], "level": shift["level"], "at": iso(shift["open_time"]), "displacement": shift["displacement"]},
        "entry_zone": None if entry_zone is None else entry_zone.public(close),
        "price_side_ok": side_ok,
    }


def run(data: MarketData, focus: str, bias: str, sweeps: list[dict], session_range: dict | None, confluence_ok: bool) -> LayerResult:
    result = LayerResult(name="smc_ict")
    bars = data.series(focus)
    if len(bars) < DISPLACEMENT_BASELINE + 5:
        result.status = STATUS_INSUFFICIENT
        result.summary = "Fewer than %d closed %s candles." % (DISPLACEMENT_BASELINE + 5, focus)
        return result
    close = bars[-1].close
    atr_value = atr(bars) or close * 0.005
    width = PIVOT_WIDTH.get(focus, 3)
    breaks = structure_breaks(bars, width)
    fvgs = detect_fvgs(bars, atr_value)
    blocks = detect_order_blocks(bars, breaks)
    dealing = dealing_range(bars, width)
    po3 = power_of_three(data.series("1m") or data.minute_bars, session_range)
    live_zones = [zone for zone in fvgs + blocks if zone.state in ("fresh", "partial", "mitigated")]
    sequence = playbook_sequence(bias, sweeps, breaks, live_zones, close, dealing, confluence_ok)

    recent_breaks = [event for event in breaks if len(bars) - 1 - event["index"] <= 30]
    for event in recent_breaks[-3:]:
        age = clamp(1.0 - (len(bars) - 1 - event["index"]) / 30.0, 0.2, 1.0)
        base = {"BOS": 0.45, "CHoCH": 0.5, "MSS": 0.7}[event["kind"]]
        result.add(
            Evidence(
                CAT_LIQUIDITY,
                "%s_%s" % (event["kind"].lower(), focus),
                float(event["direction"]),
                base * age * (1.15 if event["displacement"] else 1.0),
                focus,
                "%s %s through %.1f%s." % (event["kind"], "upward" if event["direction"] > 0 else "downward", event["level"], " with displacement" if event["displacement"] else ""),
                GATE_ANY,
            )
        )
    nearby = [zone for zone in live_zones if abs(zone.midpoint - close) <= atr_value * 1.5]
    for zone in nearby[:4]:
        inside = zone.contains(close)
        result.add(
            Evidence(
                CAT_LIQUIDITY,
                "%s_%s" % (zone.kind, "inside" if inside else "near"),
                float(zone.direction),
                0.45 if inside else 0.25,
                focus,
                "Price is %s a %s %s (%.1f-%.1f, %s)." % ("inside" if inside else "near", "bullish" if zone.direction > 0 else "bearish", zone.kind.replace("_", " "), zone.bottom, zone.top, zone.state),
                GATE_ANY,
            )
        )
    if dealing is not None:
        if dealing["zone"] == "discount" and dealing["position"] < 0.3:
            result.add(Evidence(CAT_LIQUIDITY, "discount", 0.5, 0.35, focus, "Price is deep in the discount of the dealing range (%.0f%% of range)." % (dealing["position"] * 100), GATE_ANY))
        elif dealing["zone"] == "premium" and dealing["position"] > 0.7:
            result.add(Evidence(CAT_LIQUIDITY, "premium", -0.5, 0.35, focus, "Price is deep in the premium of the dealing range (%.0f%% of range)." % (dealing["position"] * 100), GATE_ANY))
    if po3 and po3.get("stage") in ("manipulation", "distribution"):
        result.add(Evidence(CAT_LIQUIDITY, "power_of_three", float(po3["direction"]), 0.4 if po3["stage"] == "distribution" else 0.3, "1m", "Session power-of-three is in %s toward the %s." % (po3["stage"], "upside" if po3["direction"] > 0 else "downside"), GATE_ANY))

    result.findings = {
        "breaks": [{"kind": e["kind"], "direction": e["direction"], "level": e["level"], "at": iso(e["open_time"]), "displacement": e["displacement"], "bars_ago": len(bars) - 1 - e["index"]} for e in breaks[-8:]],
        "fair_value_gaps": [zone.public(close) for zone in fvgs if zone.state != "inverted"][-8:],
        "inverted_fvgs": [zone.public(close) for zone in fvgs if zone.state == "inverted"][-4:],
        "order_blocks": [zone.public(close) for zone in blocks][-8:],
        "dealing_range": None if dealing is None else {**dealing, "high_time": iso(dealing["high_time"]), "low_time": iso(dealing["low_time"])},
        "power_of_three": po3,
        "sequence": sequence,
        "live_zone_count": len(live_zones),
    }
    result.summary = "Playbook stage %s (%s). %d live zones, %d structure breaks in window." % (
        sequence.get("stage") or "not armed",
        sequence.get("direction"),
        len(live_zones),
        len(breaks),
    )
    return result
