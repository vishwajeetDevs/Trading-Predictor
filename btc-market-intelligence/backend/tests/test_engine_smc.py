"""Synthetic cases for the SMC / liquidity / zone / pattern layers of the verdict engine."""

from datetime import datetime, timedelta, timezone

from app.analysis.definitions import Bar
from app.analysis.engine import liquidity, patterns, smc, zones
from app.analysis.structure import Pivot

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bars_from(rows, minutes=15):
    """rows: (open, high, low, close)."""
    return [Bar(START + timedelta(minutes=minutes * index), o, h, l, c, 1.0) for index, (o, h, l, c) in enumerate(rows)]


def flat(count, price=100.0, wick=0.5):
    return [(price, price + wick, price - wick, price)] * count


def test_bullish_fvg_is_detected_and_tracked_through_fill():
    rows = flat(5) + [(100, 101, 99, 100), (100, 110, 100, 110), (110, 112, 105, 111)]
    found = smc.detect_fvgs(bars_from(rows), atr_value=2.0)
    assert len(found) == 1
    gap = found[0]
    assert gap.direction == 1 and gap.kind == "fvg"
    assert gap.bottom == 101 and gap.top == 105
    assert gap.state == "fresh"
    # A later bar trading into the gap marks it partial, and a close below inverts it.
    partial = smc.detect_fvgs(bars_from(rows + [(111, 111, 104, 108)]), atr_value=2.0)[0]
    assert partial.state == "partial"
    inverted = smc.detect_fvgs(bars_from(rows + [(111, 111, 104, 108), (108, 108, 98, 99)]), atr_value=2.0)[0]
    assert inverted.state == "inverted"


def test_tiny_gap_below_atr_threshold_is_ignored():
    rows = flat(5) + [(100, 100.5, 99.5, 100), (100, 100.7, 100, 100.6), (100.6, 101, 100.55, 100.8)]
    assert smc.detect_fvgs(bars_from(rows), atr_value=5.0) == []


def zigzag(closes):
    """Bars whose open is the previous close; up bars carry an upper wick, down bars a lower wick."""
    rows = []
    previous = closes[0]
    for close in closes:
        if close >= previous:
            rows.append((previous, close + 0.5, previous - 0.1, close))
        else:
            rows.append((previous, previous + 0.1, close - 0.5, close))
        previous = close
    return bars_from(rows)


# Lower highs and lower lows in two-bar legs; the last lower high is 90 at index 20.
DOWNTREND = [100, 97, 94, 96, 98, 95, 92, 94, 96, 93, 90, 92, 94, 91, 88, 90, 92, 89, 86, 88, 90, 87]


def test_mss_requires_displacement_and_produces_an_order_block():
    series = zigzag(DOWNTREND + [100, 101])
    breaks = smc.structure_breaks(series, width=1)
    assert any(event["direction"] < 0 and event["kind"] == "BOS" for event in breaks)
    bullish = [event for event in breaks if event["direction"] > 0]
    assert bullish, breaks
    last = bullish[-1]
    assert last["kind"] == "MSS"
    assert last["displacement"] is True
    assert last["level"] == 90.5
    assert smc.is_displacement(series, last["index"]) is True
    blocks = smc.detect_order_blocks(series, breaks)
    block = next(zone for zone in blocks if zone.direction == 1)
    # The order block is the bearish candle just before the displacement leg (index 21).
    assert block.formed_index == 21
    assert abs(block.top - 90.1) < 1e-9 and abs(block.bottom - 86.5) < 1e-9
    assert block.state == "fresh"


def test_a_slow_break_without_displacement_is_only_a_choch():
    series = zigzag(DOWNTREND + [89, 91, 92])
    breaks = smc.structure_breaks(series, width=1)
    upward = [event for event in breaks if event["direction"] > 0]
    assert upward
    assert upward[-1]["kind"] == "CHoCH"
    assert upward[-1]["displacement"] is False
    assert smc.detect_order_blocks(series, breaks) == [] or all(zone.direction < 0 for zone in smc.detect_order_blocks(series, breaks))


def test_sweep_is_a_wick_through_the_pool_with_a_close_back_inside():
    pool = liquidity.Pool(name="swing_low_90", price=90.0, side="below", kind="swing", formed_at=START)
    swept = bars_from(flat(5, 95) + [(95, 96, 88, 94)])
    events = liquidity.detect_sweeps(swept, [pool])
    assert len(events) == 1
    assert events[0]["side"] == "sell_side" and events[0]["direction"] == 1
    assert events[0]["extreme"] == 88 and events[0]["bars_ago"] == 0
    # A genuine breakout closes beyond the level and stays there: no sweep.
    breakout = bars_from(flat(5, 95) + [(95, 96, 88, 89), (89, 89.5, 86, 87), (87, 88, 85, 86), (86, 87, 84, 85)])
    assert liquidity.detect_sweeps(breakout, [pool]) == []


def test_multi_bar_sweep_reclaims_within_three_bars():
    pool = liquidity.Pool(name="pdl", price=90.0, side="below", kind="period", formed_at=START)
    rows = flat(5, 95) + [(95, 95, 88, 89), (89, 92, 88, 91.5)]
    events = liquidity.detect_sweeps(bars_from(rows), [pool])
    assert len(events) == 1
    assert events[0]["swept_at"] < events[0]["reclaimed_at"]


def test_equal_highs_cluster_within_tolerance():
    pivots = [
        Pivot("high", 2, 100.0, START, 3, START),
        Pivot("high", 10, 100.3, START + timedelta(hours=1), 11, START),
        Pivot("high", 20, 104.0, START + timedelta(hours=2), 21, START),
    ]
    found = liquidity.equal_levels(pivots, tolerance=0.5)
    assert len(found) == 1
    assert found[0]["touches"] == 2
    assert found[0]["anchor"] == 100.0


def test_zone_clustering_merges_nearby_pivots_and_weights_higher_timeframes():
    pivots = [
        (Pivot("high", 50, 100.0, START, 51, START), "15m"),
        (Pivot("low", 60, 100.2, START, 61, START), "1h"),
        (Pivot("high", 70, 100.4, START, 71, START), "4h"),
        (Pivot("high", 10, 120.0, START, 11, START), "15m"),
    ]
    out = zones.cluster_zones(pivots, bandwidth=0.5, now_index_by_tf={"15m": 100, "1h": 100, "4h": 100})
    assert len(out) == 2
    merged = next(zone for zone in out if zone["touches"] == 3)
    assert merged["bottom"] == 100.0 and merged["top"] == 100.4
    assert merged["timeframes"] == ["15m", "1h", "4h"]
    assert merged["score"] > out[1 - out.index(merged)]["score"]


def test_candlestick_patterns_on_the_last_bar():
    hammer = bars_from(flat(4) + [(100, 100.5, 99, 99.2), (99.2, 99.7, 95, 99.6)])
    names = {item["name"] for item in patterns.candle_patterns(hammer)}
    assert "hammer" in names
    engulf = bars_from(flat(4) + [(100, 100.5, 99, 99.2), (99.0, 101.5, 98.9, 101.2)])
    assert "bullish_engulfing" in {item["name"] for item in patterns.candle_patterns(engulf)}
    doji = bars_from(flat(4) + [(100, 102, 98, 100.05)])
    assert "doji" in {item["name"] for item in patterns.candle_patterns(doji)}


def test_regular_bullish_divergence_lower_low_with_higher_rsi():
    closes = [100.0] * 10
    closes += [100 - 2 * step for step in range(1, 11)]  # fast drop to 80
    closes += [80 + 1.5 * step for step in range(1, 11)]  # rally to 95
    closes += [95 - 0.8 * step for step in range(1, 21)]  # slow drop to 79
    closes += [79 + 1.5 * step for step in range(1, 5)]  # rally to confirm the pivot
    rows = [(c, c + 0.5, c - 0.5, c) for c in closes]
    found = patterns.divergences(bars_from(rows), width=2)
    assert any(item["name"] == "regular_bullish_divergence" for item in found), found
