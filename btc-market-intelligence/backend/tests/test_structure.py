"""Synthetic structure, breakout, pullback, and reversal cases."""

from datetime import datetime, timedelta, timezone

from app.analysis.definitions import Bar
from app.analysis.events import breakout_events, pullback_state, reversal_state
from app.analysis.levels import day_levels, level_row
from app.analysis.structure import Pivot, label_from_pivots, structure_at, walk

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bars(rows: list[tuple[float, float, float]]) -> list[Bar]:
    output = []
    for index, (high, low, close) in enumerate(rows):
        output.append(Bar(START + timedelta(minutes=index), close, high, low, close, 1))
    return output


BULL = [(10, 8, 9), (14, 11, 12), (11, 7, 9), (13, 10, 11), (16, 12, 15), (12, 8, 10), (15, 11, 13)]
BEAR = [(10, 8, 9), (14, 9, 12), (11, 6, 8), (13, 7, 12), (9, 5, 7), (10, 3, 6), (8, 4, 6)]
SIDE = [(10, 5, 8), (14, 6, 12), (11, 4, 8), (13, 6, 10), (15, 7, 14), (12, 3, 8), (13, 5, 9)]


def test_bullish_structure_is_a_higher_high_and_higher_low():
    found = structure_at(bars(BULL), 1)
    assert found.label == "BULLISH"
    assert found.higher_high is True
    assert found.higher_low is True
    assert [item.price for item in found.swing_highs] == [14, 16]
    assert [item.price for item in found.swing_lows] == [7, 8]


def test_bearish_structure_is_a_lower_high_and_lower_low():
    found = structure_at(bars(BEAR), 1)
    assert found.label == "BEARISH"
    assert found.lower_high is True
    assert found.lower_low is True


def test_overlapping_swings_are_sideways():
    found = structure_at(bars(SIDE), 1)
    assert found.label == "SIDEWAYS"
    assert found.overlapping is True
    assert found.higher_high is True
    assert found.lower_low is True


def test_range_bound_requires_closes_inside_the_last_swings():
    moment = START
    highs = [
        Pivot("high", 1, 10, moment, 1, moment),
        Pivot("high", 3, 10, moment, 3, moment),
    ]
    lows = [
        Pivot("low", 2, 5, moment, 2, moment),
        Pivot("low", 4, 5, moment, 4, moment),
    ]
    inside = [Bar(START + timedelta(minutes=index), 7, 8, 6, 7, 1) for index in range(10)]
    found = label_from_pivots(highs, lows, inside)
    assert found.label == "SIDEWAYS"
    assert found.range_bound is True
    outside = inside[:-1] + [Bar(inside[-1].open_time, 11, 12, 10, 11, 1)]
    assert label_from_pivots(highs, lows, outside).range_bound is False


def test_a_pivot_is_absent_until_the_right_side_bar_closes():
    series = bars(BULL)
    before = structure_at(series, 1, end=2)
    after = structure_at(series, 1, end=3)
    assert [item.price for item in before.swing_highs] == []
    assert [item.price for item in after.swing_highs] == [14]


def test_changing_a_future_bar_does_not_change_the_earlier_structure():
    series = bars(BULL)
    original = structure_at(series, 1, end=4)
    changed = list(series)
    last = changed[-1]
    changed[-1] = Bar(last.open_time, 1, 50, 1, 1, 1)
    assert structure_at(changed, 1, end=4) == original


def test_short_history_is_insufficient_data():
    assert structure_at(bars(BULL)[:3], 1).label == "INSUFFICIENT_DATA"


def test_failed_breakout_requires_a_close_back_through_the_level():
    series = bars([(10, 8, 9), (15, 9, 14), (12, 11, 11.5), (16, 12, 16), (14, 10, 10)])
    names = [item["condition"] for item in breakout_events(series, 1)]
    assert names == ["confirmed_breakout", "failed_breakout"]


def test_a_wick_through_a_level_is_not_a_breakout():
    series = bars([(10, 8, 9), (15, 9, 14), (12, 11, 11.5), (20, 12, 14)])
    assert breakout_events(series, 1) == []


def test_pullback_and_reversal_are_separate():
    found = structure_at(bars(BULL), 1)
    assert pullback_state(found, close=10, previous_close=13) == "PULLBACK"
    assert pullback_state(found, close=6, previous_close=13) == "NONE"
    _label, events = walk(bars(BULL), 1)
    assert reversal_state(events) == "NONE"
    flipped = list(events) + [{"label": "BEARISH", "condition": "bearish_structure"}]
    assert reversal_state(flipped) == "REVERSAL"


def test_day_levels_use_utc_dates_and_report_distance():
    series = [
        Bar(datetime(2026, 1, 1, 23, tzinfo=timezone.utc), 10, 12, 9, 11, 1),
        Bar(datetime(2026, 1, 2, 0, tzinfo=timezone.utc), 11, 15, 10, 14, 1),
    ]
    levels = day_levels(series)
    assert levels["previous_utc_day_high"]["price"] == 12
    assert levels["current_utc_day_low"]["price"] == 10
    row = level_row("previous_utc_day_high", 12, "previous day", "1m", 14, 2, series[0].open_time)
    assert row["distance"] == -2
    assert row["distance_percent"] == -2 / 14 * 100
    assert row["distance_atr"] == -1
    assert "stop" not in row["source"]
