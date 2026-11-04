"""Indicator fixtures. Expected values are hand-calculated from the documented formulas."""

from datetime import datetime, timezone

import pytest

from app.analysis.definitions import Bar, valid_bar
from app.analysis.indicators import (
    atr,
    atr_percent,
    bollinger,
    ema,
    macd,
    percentile_rank,
    realized_volatility,
    rsi,
    sma,
    vwap_utc_day,
)


TEXTBOOK_RSI = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
]


def _bar(day: int, hour: int, high: float, low: float, close: float, volume: float) -> Bar:
    return Bar(datetime(2026, 1, day, hour, tzinfo=timezone.utc), close, high, low, close, volume)


def test_rsi_matches_the_textbook_wilder_series():
    assert rsi(TEXTBOOK_RSI) == pytest.approx(70.464, abs=0.001)
    assert rsi(TEXTBOOK_RSI[:14]) is None


def test_sma_and_ema_match_a_hand_calculation():
    assert sma([1, 2, 3], 2) == pytest.approx(2.5)
    assert sma([1, 2], 3) is None
    # Period 3, seed SMA(1,2,3)=2, multiplier 1/2.
    # Next: 2 + (4-2)/2 = 3. Then 3 + (5-3)/2 = 4.
    assert ema([1, 2, 3, 4, 5], 3) == pytest.approx(4)
    assert ema([1, 2], 3) is None


def test_macd_on_an_arithmetic_series_keeps_a_constant_line():
    # After both EMAs are seeded, every close rises by 1 and both EMAs rise by 1.
    # The slow SMA of 1..6 is 3.5 and the fast EMA at that bar is 5, so MACD stays 1.5.
    # A constant MACD line has a signal equal to itself.
    result = macd([float(value) for value in range(1, 11)], fast=3, slow=6, signal=3)
    assert result == {"macd": pytest.approx(1.5), "signal": pytest.approx(1.5), "histogram": pytest.approx(0)}
    assert macd([float(value) for value in range(1, 6)], fast=3, slow=6, signal=3) is None


def test_vwap_resets_on_the_utc_date():
    bars = [
        _bar(1, 23, 3, 1, 2, 100),
        _bar(2, 0, 5, 1, 3, 10),
        _bar(2, 1, 8, 2, 5, 30),
    ]
    assert vwap_utc_day(bars) == pytest.approx((3 * 10 + 5 * 30) / 40)
    assert vwap_utc_day([]) is None


def test_bollinger_uses_the_population_standard_deviation():
    # Window 1 and 3. Middle 2. Population variance 1. Bands at 2 +/- 2.
    bands = bollinger([1, 3], period=2, mult=2)
    assert bands["middle"] == pytest.approx(2)
    assert bands["stdev"] == pytest.approx(1)
    assert bands["upper"] == pytest.approx(4)
    assert bands["lower"] == pytest.approx(0)
    assert bollinger([1], period=2) is None


def test_atr_and_its_percentile_use_completed_true_ranges():
    bars = [
        _bar(1, 0, 10, 8, 9, 1),
        _bar(1, 1, 12, 10, 11, 1),
        _bar(1, 2, 11, 9, 10, 1),
    ]
    # True ranges after the first bar are 3 and 2. The first ATR is their mean.
    assert atr(bars, period=2) == pytest.approx(2.5)
    assert atr_percent(bars, period=2) == pytest.approx(25)
    assert atr(bars[:2], period=2) is None
    assert percentile_rank([1, 2, 3, 4], window=4) == pytest.approx(100)
    assert percentile_rank([1, 3, 2, 2], window=4) == pytest.approx(75)
    assert percentile_rank([1, 2, 3], window=4) is None


def test_realized_volatility_is_the_sample_log_return_deviation():
    # Log returns 1 and 0. Sample variance is 0.5.
    value = realized_volatility([1.0, 2.718281828459045, 2.718281828459045], window=2)
    assert value == pytest.approx(0.5 ** 0.5)
    assert realized_volatility([1, 2], window=2) is None


def test_invalid_bars_are_rejected():
    bar = _bar(1, 0, 10, 8, 9, 1)
    assert valid_bar(bar) is True
    assert valid_bar(Bar(bar.open_time, 9, 8, 10, 9, 1)) is False
    assert valid_bar(Bar(bar.open_time, 0, 10, 8, 9, 1)) is False
    assert valid_bar(Bar(bar.open_time, 9, 10, 8, 9, -1)) is False
