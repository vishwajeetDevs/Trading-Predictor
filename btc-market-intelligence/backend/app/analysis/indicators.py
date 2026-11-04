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


# ---------------------------------------------------------------------------
# Verdict-engine indicators. These are additive. They are not part of the frozen
# study definition and do not change `definitions.parameters()` or its hash.
# ---------------------------------------------------------------------------


def _wilder_series(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    current = sum(values[:period]) / period
    out[period - 1] = current
    for index in range(period, len(values)):
        current = (current * (period - 1) + values[index]) / period
        out[index] = current
    return out


def directional_movement(bars: list[Bar], period: int = 14) -> dict | None:
    """Wilder ADX with +DI and -DI. Needs 2 * period bars."""
    if period <= 0 or len(bars) < period * 2 + 1:
        return None
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    ranges = true_ranges(bars)[1:]
    for index in range(1, len(bars)):
        up = bars[index].high - bars[index - 1].high
        down = bars[index - 1].low - bars[index].low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    smooth_tr = _wilder_series(ranges, period)
    smooth_plus = _wilder_series(plus_dm, period)
    smooth_minus = _wilder_series(minus_dm, period)
    plus_di: list[float] = []
    minus_di: list[float] = []
    dx: list[float] = []
    for tr, plus, minus in zip(smooth_tr, smooth_plus, smooth_minus):
        if tr is None or plus is None or minus is None or tr <= 0:
            continue
        p = plus / tr * 100.0
        m = minus / tr * 100.0
        plus_di.append(p)
        minus_di.append(m)
        total = p + m
        dx.append(0.0 if total == 0 else abs(p - m) / total * 100.0)
    adx_series = _wilder_series(dx, period)
    adx_value = adx_series[-1] if adx_series else None
    if adx_value is None or not plus_di:
        return None
    previous_adx = adx_series[-2] if len(adx_series) > 1 else None
    return {
        "adx": adx_value,
        "plus_di": plus_di[-1],
        "minus_di": minus_di[-1],
        "adx_slope": None if previous_adx is None else adx_value - previous_adx,
    }


def stochastic(bars: list[Bar], period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> dict | None:
    """Slow stochastic. %K is the SMA of raw %K, %D is the SMA of %K."""
    needed = period + smooth_k + smooth_d - 2
    if period <= 0 or len(bars) < needed:
        return None
    raw: list[float] = []
    for index in range(period - 1, len(bars)):
        window = bars[index - period + 1 : index + 1]
        high = max(bar.high for bar in window)
        low = min(bar.low for bar in window)
        span = high - low
        raw.append(50.0 if span <= 0 else (bars[index].close - low) / span * 100.0)
    k_values = [sum(raw[i - smooth_k + 1 : i + 1]) / smooth_k for i in range(smooth_k - 1, len(raw))]
    if len(k_values) < smooth_d:
        return None
    d_values = [sum(k_values[i - smooth_d + 1 : i + 1]) / smooth_d for i in range(smooth_d - 1, len(k_values))]
    return {"k": k_values[-1], "d": d_values[-1]}


def rate_of_change(closes: list[float], period: int = 12) -> float | None:
    if period <= 0 or len(closes) < period + 1 or closes[-period - 1] <= 0:
        return None
    return (closes[-1] / closes[-period - 1] - 1.0) * 100.0


def cci(bars: list[Bar], period: int = 20) -> float | None:
    if period <= 1 or len(bars) < period:
        return None
    typical = [(bar.high + bar.low + bar.close) / 3.0 for bar in bars[-period:]]
    mean = sum(typical) / period
    deviation = sum(abs(value - mean) for value in typical) / period
    if deviation == 0:
        return 0.0
    return (typical[-1] - mean) / (0.015 * deviation)


def obv_series(bars: list[Bar]) -> list[float]:
    out: list[float] = []
    total = 0.0
    for index, bar in enumerate(bars):
        if index > 0:
            previous = bars[index - 1].close
            if bar.close > previous:
                total += bar.volume
            elif bar.close < previous:
                total -= bar.volume
        out.append(total)
    return out


def obv_slope(bars: list[Bar], window: int = 20) -> float | None:
    """Least-squares slope of OBV over the last `window` bars, normalized by mean volume."""
    if window < 3 or len(bars) < window:
        return None
    series = obv_series(bars)[-window:]
    mean_volume = sum(bar.volume for bar in bars[-window:]) / window
    if mean_volume <= 0:
        return None
    slope = linear_slope(series)
    return None if slope is None else slope / mean_volume


def linear_slope(values: list[float]) -> float | None:
    count = len(values)
    if count < 2:
        return None
    mean_x = (count - 1) / 2.0
    mean_y = sum(values) / count
    numerator = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
    denominator = sum((index - mean_x) ** 2 for index in range(count))
    if denominator == 0:
        return None
    return numerator / denominator


def keltner(bars: list[Bar], period: int = 20, mult: float = 1.5, atr_period: int = ATR_PERIOD) -> dict | None:
    closes = [bar.close for bar in bars]
    middle = ema(closes, period)
    width = atr(bars, atr_period)
    if middle is None or width is None:
        return None
    return {"middle": middle, "upper": middle + mult * width, "lower": middle - mult * width}


def bollinger_width_series(closes: list[float], period: int = BB_PERIOD, mult: float = BB_MULT) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    for index in range(period - 1, len(closes)):
        bands = bollinger(closes[: index + 1], period, mult)
        if bands is None or bands["middle"] <= 0:
            continue
        out[index] = (bands["upper"] - bands["lower"]) / bands["middle"] * 100.0
    return out


def volume_zscore(bars: list[Bar], window: int = 20) -> float | None:
    """Z-score of the latest volume against the prior `window` volumes."""
    if window < 3 or len(bars) < window + 1:
        return None
    history = [bar.volume for bar in bars[-window - 1 : -1]]
    mean = sum(history) / window
    variance = sum((value - mean) ** 2 for value in history) / (window - 1)
    sigma = math.sqrt(variance)
    if sigma <= 0:
        return None
    return (bars[-1].volume - mean) / sigma


def efficiency_ratio(closes: list[float], period: int = 10) -> float | None:
    """Kaufman efficiency ratio: net change over the sum of absolute changes."""
    if period <= 0 or len(closes) < period + 1:
        return None
    window = closes[-period - 1 :]
    path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    if path == 0:
        return 0.0
    return abs(window[-1] - window[0]) / path


def hurst_variance_ratio(closes: list[float], lag: int = 8, window: int = 128) -> float | None:
    """Hurst estimate from the variance ratio of `lag`-step and 1-step log returns.

    H = 0.5 is a random walk; above favours persistence, below favours mean reversion.
    """
    if lag < 2 or len(closes) < window + lag or window < lag * 4:
        return None
    sample = closes[-window - lag :]
    if any(value <= 0 for value in sample):
        return None
    logs = [math.log(value) for value in sample]
    short = [logs[i] - logs[i - 1] for i in range(1, len(logs))]
    long = [logs[i] - logs[i - lag] for i in range(lag, len(logs))]

    def _variance(values: list[float]) -> float:
        mean = sum(values) / len(values)
        return sum((item - mean) ** 2 for item in values) / (len(values) - 1)

    short_var = _variance(short)
    long_var = _variance(long)
    if short_var <= 0 or long_var <= 0:
        return None
    return 0.5 * math.log(long_var / short_var) / math.log(lag)


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
