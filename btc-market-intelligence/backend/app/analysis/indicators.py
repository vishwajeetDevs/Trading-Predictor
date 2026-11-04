"""Closed-bar indicators. The latest value is None when the lookback is incomplete.

Wilder RSI follows the same recurrence as day-trader-skill trader-scanner `rsi`
(MIT). The body here is a fresh implementation of that published recurrence.
"""

from __future__ import annotations

import math

from app.analysis.definitions import ATR_PERCENTILE_WINDOW, ATR_PERIOD, BB_MULT, BB_PERIOD, RSI_PERIOD, RV_WINDOW, Bar


def rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    if period <= 0 or len(closes) < period + 1:
        return None
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for index in range(period, len(gains)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
    if average_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def sma(closes: list[float], period: int) -> float | None:
    if period <= 0 or len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def ema_series(values: list[float], period: int) -> list[float | None]:
    """EMA seeded with the SMA of the first `period` values. Multiplier is 2/(period+1)."""
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    previous = sum(values[:period]) / period
    out[period - 1] = previous
    weight = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        previous = (values[index] - previous) * weight + previous
        out[index] = previous
    return out


def ema(closes: list[float], period: int) -> float | None:
    series = ema_series(closes, period)
    return series[-1] if series else None


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict | None:
    if fast <= 0 or slow <= fast or signal <= 0:
        return None
    fast_line = ema_series(closes, fast)
    slow_line = ema_series(closes, slow)
    macd_line = [
        None if fast_line[i] is None or slow_line[i] is None else fast_line[i] - slow_line[i]
        for i in range(len(closes))
    ]
    defined = [value for value in macd_line if value is not None]
    signal_on_defined = ema_series(defined, signal)
    if not signal_on_defined or signal_on_defined[-1] is None or macd_line[-1] is None:
        return None
    signal_value = signal_on_defined[-1]
    line_value = macd_line[-1]
    return {"macd": line_value, "signal": signal_value, "histogram": line_value - signal_value}


def vwap_utc_day(bars: list[Bar]) -> float | None:
    """Volume-weighted typical price for the UTC date of the last bar. Earlier dates are ignored."""
    if not bars:
        return None
    day = bars[-1].open_time.date()
    weighted = 0.0
    volume = 0.0
    for bar in bars:
        if bar.open_time.date() != day:
            continue
        typical = (bar.high + bar.low + bar.close) / 3.0
        weighted += typical * bar.volume
        volume += bar.volume
    if volume <= 0:
        return None
    return weighted / volume


def bollinger(closes: list[float], period: int = BB_PERIOD, mult: float = BB_MULT) -> dict | None:
    """Middle band is the SMA. Band width uses the population standard deviation (divisor n)."""
    if period <= 1 or len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((value - middle) ** 2 for value in window) / period
    sigma = math.sqrt(variance)
    return {"middle": middle, "upper": middle + mult * sigma, "lower": middle - mult * sigma, "stdev": sigma}


def true_ranges(bars: list[Bar]) -> list[float]:
    ranges = []
    for index, bar in enumerate(bars):
        span = bar.high - bar.low
        if index == 0:
            ranges.append(span)
            continue
        previous = bars[index - 1].close
        ranges.append(max(span, abs(bar.high - previous), abs(bar.low - previous)))
    return ranges


def atr_series(bars: list[Bar], period: int = ATR_PERIOD) -> list[float | None]:
    """Wilder ATR. The first value is the mean of the first `period` true ranges that have a previous close."""
    out: list[float | None] = [None] * len(bars)
    if period <= 0 or len(bars) < period + 1:
        return out
    ranges = true_ranges(bars)
    current = sum(ranges[1 : period + 1]) / period
    out[period] = current
    for index in range(period + 1, len(bars)):
        current = (current * (period - 1) + ranges[index]) / period
        out[index] = current
    return out


def atr(bars: list[Bar], period: int = ATR_PERIOD) -> float | None:
    series = atr_series(bars, period)
    return series[-1] if series else None


def atr_percent(bars: list[Bar], period: int = ATR_PERIOD) -> float | None:
    value = atr(bars, period)
    close = bars[-1].close if bars else 0
    if value is None or close <= 0:
        return None
    return value / close * 100.0


def atr_percent_series(bars: list[Bar], period: int = ATR_PERIOD) -> list[float | None]:
    series = atr_series(bars, period)
    out: list[float | None] = []
    for bar, value in zip(bars, series):
        if value is None or bar.close <= 0:
            out.append(None)
        else:
            out.append(value / bar.close * 100.0)
    return out


def percentile_rank(values: list[float], window: int = ATR_PERCENTILE_WINDOW) -> float | None:
    """Percent of the last `window` observations that are less than or equal to the latest one."""
    if window <= 0 or len(values) < window:
        return None
    sample = values[-window:]
    latest = sample[-1]
    return sum(1 for item in sample if item <= latest) / window * 100.0


def realized_volatility(closes: list[float], window: int = RV_WINDOW) -> float | None:
    """Sample standard deviation of the last `window` log returns. Not annualized."""
    if window < 2 or len(closes) < window + 1:
        return None
    returns = []
    start = len(closes) - window
    for index in range(start, len(closes)):
        previous = closes[index - 1]
        if previous <= 0 or closes[index] <= 0:
            return None
        returns.append(math.log(closes[index] / previous))
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    return math.sqrt(variance)


def latest_indicators(bars: list[Bar]) -> dict:
    closes = [bar.close for bar in bars]
    atr_value = atr(bars)
    atr_pct = atr_percent(bars)
    pct_series = [item for item in atr_percent_series(bars) if item is not None]
    bands = bollinger(closes)
    macd_value = macd(closes)
    return {
        "rsi_14": rsi(closes),
        "sma_20": sma(closes, 20),
        "ema_20": ema(closes, 20),
        "macd_12_26_9": macd_value,
        "vwap_utc_day": vwap_utc_day(bars),
        "bollinger_20_2": bands,
        "atr_14": atr_value,
        "atr_percent_14": atr_pct,
        "atr_percent_percentile_100": percentile_rank(pct_series),
        "realized_volatility_20": realized_volatility(closes),
        "bars_used": len(bars),
    }
