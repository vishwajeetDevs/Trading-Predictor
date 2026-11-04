"""Forward studies, base rates, sample refusal, bootstrap, and the tail screen."""

import pytest
from datetime import datetime, timedelta, timezone

from app.analysis.definitions import MIN_SAMPLE, Bar, definition_hash, parameters
from app.analysis.stats import block_bootstrap, compare_base_rate, follow_through, summarize_sample
from app.analysis.studies import (
    base_rate_returns,
    extreme_downside_events,
    forward_return,
    latest_tail_match,
    measure_forward,
    partition_events,
    unconditional_returns,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def minute(index: int, close: float, high: float | None = None, low: float | None = None) -> Bar:
    high = close if high is None else high
    low = close if low is None else low
    opened = close
    return Bar(START + timedelta(minutes=index), opened, max(high, opened, close), min(low, opened, close), close, 1)


def _indexed(count: int, close_at) -> dict[int, Bar]:
    series = {}
    for index in range(count):
        bar = close_at(index)
        series[int(bar.open_time.timestamp())] = bar
    return series


def test_forward_return_uses_the_exact_future_minute():
    series = _indexed(40, lambda index: minute(index, 100, high=100, low=100))
    spike = minute(10, 100, high=120, low=90)
    series[int(spike.open_time.timestamp())] = spike
    target = minute(15, 110, high=110, low=110)
    series[int(target.open_time.timestamp())] = target
    first = minute(0, 100)
    events = [{"condition": "bullish_structure", "open_time": first.open_time, "close": 100, "direction": 1}]
    measured = measure_forward(events, series, 60)
    sample = measured["bullish_structure"]["15m"]
    assert sample["return"] == [pytest.approx(0.1)]
    assert sample["mfe"] == [pytest.approx(0.2)]
    assert sample["mae"] == [pytest.approx(0.1)]
    assert measured["bullish_structure"]["24h"]["excluded"] == 1
    assert measured["bullish_structure"]["24h"]["return"] == []


def test_base_rate_steps_do_not_overlap_and_condition_events_stay_separate():
    series = _indexed(61, lambda index: minute(index, 100 + index))
    returns = base_rate_returns(series, 15 * 60)
    assert returns
    assert len(returns) == 4
    first = minute(0, 100)
    second = minute(1, 100)
    events = [
        {"condition": "high_volatility", "open_time": first.open_time, "close": 100, "direction": 0},
        {"condition": "high_volatility", "open_time": second.open_time, "close": 100, "direction": 0},
    ]
    measured = measure_forward(events, series, 60)
    assert len(measured["high_volatility"]["15m"]["return"]) == 2


def test_minimum_sample_withholds_the_frequency():
    short = summarize_sample([0.01] * (MIN_SAMPLE - 1), [0.02] * (MIN_SAMPLE - 1), [0.01] * (MIN_SAMPLE - 1))
    assert short["status"] == "INSUFFICIENT_SAMPLE"
    assert short["sample_size"] == MIN_SAMPLE - 1
    assert short["positive_rate"] is None
    ready = summarize_sample([0.01] * MIN_SAMPLE, [0.02] * MIN_SAMPLE, [0.01] * MIN_SAMPLE)
    assert ready["status"] == "OK"
    assert ready["positive_rate"] == 1


def test_base_rate_comparison_does_not_treat_fifty_percent_as_an_edge():
    separated = compare_base_rate(0.8, 0.2, MIN_SAMPLE)
    assert "not a forecast" in separated["explanation"]
    assert "50%" in separated["explanation"]
    close = compare_base_rate(0.52, 0.50, MIN_SAMPLE)
    assert "not a meaningful separation" in close["explanation"]
    withheld = compare_base_rate(0.9, 0.4, MIN_SAMPLE - 1)
    assert withheld["status"] == "NOT_COMPARABLE"
    assert withheld["difference"] is None
    assert withheld["condition_positive_rate"] is None
    mismatched = compare_base_rate(0.8, 0.2, MIN_SAMPLE, base_sample=10)
    assert mismatched["status"] == "NOT_COMPARABLE"
    assert mismatched["difference"] is None


def test_bootstrap_is_deterministic_and_not_a_future_probability():
    values = [index / 100 for index in range(30)]
    first = block_bootstrap(values, seed=7, block_length=5, draws=20)
    second = block_bootstrap(values, seed=7, block_length=5, draws=20)
    other = block_bootstrap(values, seed=8, block_length=5, draws=20)
    assert first == second
    assert first != other
    assert first["historical_sample_size"] == 30
    assert "not a calibrated probability" in first["note"]
    spanned = block_bootstrap(values, seed=7, block_length=5, draws=20, sampling_interval_seconds=24 * 60 * 60)
    assert spanned["block_span_seconds"] == 5 * 24 * 60 * 60
    assert spanned["block_unit"] == "de-clustered observations"
    assert block_bootstrap(values[:5], seed=7, block_length=5, draws=10) is None
    assert min(values) <= first["p10_of_resampled_medians"] <= first["p90_of_resampled_medians"] <= max(values)


def _path(last_shock: float) -> list[Bar]:
    price = 100.0
    output = []
    for index in range(30):
        shock = 0.02 if index % 5 == 0 else 0.001
        if index == 29:
            shock = last_shock
        price *= 1.0 + shock
        output.append(minute(index, price, high=price, low=price * (1.0 if shock >= 0 else 1.0 + shock)))
    return output


def test_tail_screen_uses_the_declared_sentence():
    crash = _path(-0.2)
    matched = latest_tail_match(crash, lookback=20)
    assert matched["matched"] is True
    assert matched["sentence"] == "Current conditions match the historical tail definition."
    assert "crash is coming" not in matched["sentence"].lower()
    calm = latest_tail_match(_path(0.05), lookback=20)
    assert calm["matched"] is False
    assert calm["sentence"] == "Current conditions do not match the historical tail definition."
    assert latest_tail_match(_path(0.05)[:5], lookback=20)["status"] == "INSUFFICIENT_HISTORY"
    assert follow_through(-0.01) == "continued_downward"
    assert follow_through(0.01) == "reversed"
    assert follow_through(0) == "stabilized"
    assert follow_through(None) == "INSUFFICIENT_HISTORY"


def test_future_bars_do_not_change_earlier_tail_events():
    series = [minute(index, 100 * (1.001 ** index)) for index in range(40)]
    series[25] = minute(25, series[24].close * 0.7, high=series[24].close * 0.7, low=series[24].close * 0.6)
    original = extreme_downside_events(series, lookback=20)
    changed = list(series)
    changed[-1] = minute(39, 1, high=1, low=1)
    revised = extreme_downside_events(changed, lookback=20)
    cutoff = series[-1].open_time
    assert [item for item in original if item["open_time"] < cutoff] == [item for item in revised if item["open_time"] < cutoff]
    assert any(item["open_time"] == series[25].open_time for item in original)


def test_definition_hash_changes_when_a_parameter_changes():
    current = definition_hash()
    edited = parameters()
    edited["fail_bars"] = edited["fail_bars"] + 1
    assert definition_hash(edited) != current
    assert definition_hash() == current


def test_overlapping_events_are_not_counted_as_independent():
    series = _indexed(40, lambda index: minute(index, 100))
    first = minute(0, 100)
    second = minute(1, 100)
    events = [
        {"condition": "bullish_structure", "open_time": first.open_time, "close": 100, "direction": 1},
        {"condition": "bullish_structure", "open_time": second.open_time, "close": 100, "direction": 1},
    ]
    parts = partition_events(events, series, 60, 15 * 60)
    assert parts["raw_count"] == 2
    assert parts["independent_count"] == 1
    assert parts["clustered_out"] == 1
    assert parts["excluded"] == 0
    assert parts["independent"][0]["open_time"] == first.open_time


def test_base_rate_uses_the_condition_timeframe_close():
    minutes = _indexed(180, lambda index: minute(index, 100))
    minutes[int(minute(60, 110).open_time.timestamp())] = minute(60, 110)
    minutes[int(minute(119, 110).open_time.timestamp())] = minute(119, 110)
    hourly = [Bar(START, 100, 100, 100, 200, 1)]
    hourly_rate = unconditional_returns(hourly, minutes, 3600, 3600)
    minute_rate = unconditional_returns([minute(0, 100)], minutes, 60, 3600)
    assert hourly_rate["return"] == [pytest.approx(110 / 200 - 1)]
    assert minute_rate["return"] == [pytest.approx(110 / 100 - 1)]


def test_forward_return_ignores_bars_after_the_horizon_and_refuses_a_later_substitute():
    series = _indexed(20, lambda index: minute(index, 100))
    series[int(minute(15, 110).open_time.timestamp())] = minute(15, 110)
    event = {"open_time": minute(0, 100).open_time, "close": 100}
    assert forward_return(event, series, 60, 15 * 60) == pytest.approx(0.1)
    series[int(minute(16, 1).open_time.timestamp())] = minute(16, 1)
    assert forward_return(event, series, 60, 15 * 60) == pytest.approx(0.1)
    del series[int(minute(15, 110).open_time.timestamp())]
    assert forward_return(event, series, 60, 15 * 60) is None
