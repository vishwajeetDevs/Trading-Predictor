"""L6. Trend, momentum, volatility, and volume evidence from indicators."""

from __future__ import annotations

from app.analysis.definitions import Bar
from app.analysis.engine.types import (
    CAT_MOMENTUM,
    CAT_VOLUME,
    GATE_ANY,
    GATE_MEAN_REVERSION,
    GATE_TREND,
    STATUS_INSUFFICIENT,
    Evidence,
    LayerResult,
    MarketData,
    clamp,
)
from app.analysis.indicators import (
    atr,
    atr_percent_series,
    bollinger,
    bollinger_width_series,
    cci,
    directional_movement,
    ema,
    keltner,
    macd,
    obv_slope,
    percentile_rank,
    rate_of_change,
    realized_volatility,
    rsi,
    stochastic,
    volume_zscore,
)

MOMENTUM_TIMEFRAMES = ("15m", "1h", "4h")


def describe(bars: list[Bar]) -> dict:
    closes = [bar.close for bar in bars]
    bands = bollinger(closes)
    kelt = keltner(bars)
    macd_value = macd(closes)
    dm = directional_movement(bars)
    bbw = [item for item in bollinger_width_series(closes) if item is not None]
    atr_pcts = [item for item in atr_percent_series(bars) if item is not None]
    squeeze = None
    if bands is not None and kelt is not None:
        squeeze = bands["upper"] < kelt["upper"] and bands["lower"] > kelt["lower"]
    history_macd = None
    if len(closes) > 40:
        earlier = macd(closes[:-1])
        if earlier is not None and macd_value is not None:
            history_macd = macd_value["histogram"] - earlier["histogram"]
    return {
        "rsi": rsi(closes),
        "stochastic": stochastic(bars),
        "roc_12": rate_of_change(closes),
        "cci_20": cci(bars),
        "macd": macd_value,
        "macd_histogram_change": history_macd,
        "adx": dm,
        "ema_20": ema(closes, 20),
        "ema_50": ema(closes, 50),
        "ema_200": ema(closes, 200),
        "bollinger": bands,
        "bollinger_width_percentile": percentile_rank(bbw, 100) if len(bbw) >= 100 else None,
        "keltner": kelt,
        "squeeze": squeeze,
        "atr": atr(bars),
        "atr_percent_percentile": percentile_rank(atr_pcts, 100) if len(atr_pcts) >= 100 else None,
        "realized_volatility_20": realized_volatility(closes),
        "obv_slope": obv_slope(bars),
        "volume_zscore": volume_zscore(bars),
        "close": closes[-1] if closes else None,
    }


def run(data: MarketData, focus: str) -> LayerResult:
    result = LayerResult(name="momentum_volatility_volume")
    frames = list(dict.fromkeys((focus,) + MOMENTUM_TIMEFRAMES))
    views: dict[str, dict] = {}
    for timeframe in frames:
        bars = data.series(timeframe)
        if len(bars) < 30:
            continue
        views[timeframe] = describe(bars)
    if not views:
        result.status = STATUS_INSUFFICIENT
        result.summary = "No timeframe has 30 closed candles."
        return result
    for timeframe, view in views.items():
        weight = 1.0 if timeframe == focus else 0.7
        close = view["close"]
        value = view["rsi"]
        if value is not None:
            if value >= 70:
                result.add(Evidence(CAT_MOMENTUM, "rsi_overbought", -0.6, 0.4 * weight, timeframe, "RSI(14) is %.1f, above 70." % value, GATE_MEAN_REVERSION))
            elif value <= 30:
                result.add(Evidence(CAT_MOMENTUM, "rsi_oversold", 0.6, 0.4 * weight, timeframe, "RSI(14) is %.1f, below 30." % value, GATE_MEAN_REVERSION))
            elif value >= 55:
                result.add(Evidence(CAT_MOMENTUM, "rsi_bullish_zone", 0.4, 0.25 * weight, timeframe, "RSI(14) is %.1f, in the bullish half." % value, GATE_TREND))
            elif value <= 45:
                result.add(Evidence(CAT_MOMENTUM, "rsi_bearish_zone", -0.4, 0.25 * weight, timeframe, "RSI(14) is %.1f, in the bearish half." % value, GATE_TREND))
        macd_value = view["macd"]
        if macd_value is not None:
            sign = 1.0 if macd_value["histogram"] > 0 else -1.0
            turning = view["macd_histogram_change"]
            strength = 0.3
            if turning is not None and (turning > 0) == (sign > 0):
                strength = 0.4
            result.add(Evidence(CAT_MOMENTUM, "macd_histogram", sign, strength * weight, timeframe, "MACD histogram is %s%s." % ("positive" if sign > 0 else "negative", " and expanding" if strength > 0.3 else ""), GATE_TREND))
        stoch = view["stochastic"]
        if stoch is not None:
            if stoch["k"] > 80 and stoch["k"] < stoch["d"]:
                result.add(Evidence(CAT_MOMENTUM, "stochastic_rollover", -0.5, 0.3 * weight, timeframe, "Stochastic %%K %.0f is rolling under %%D above 80." % stoch["k"], GATE_MEAN_REVERSION))
            elif stoch["k"] < 20 and stoch["k"] > stoch["d"]:
                result.add(Evidence(CAT_MOMENTUM, "stochastic_hook", 0.5, 0.3 * weight, timeframe, "Stochastic %%K %.0f is hooking above %%D below 20." % stoch["k"], GATE_MEAN_REVERSION))
        if view["cci_20"] is not None and abs(view["cci_20"]) >= 100:
            result.add(Evidence(CAT_MOMENTUM, "cci_extreme", -0.3 if view["cci_20"] > 0 else 0.3, 0.2 * weight, timeframe, "CCI(20) is %.0f." % view["cci_20"], GATE_MEAN_REVERSION))
        if view["ema_20"] is not None and view["ema_50"] is not None and close is not None:
            above = close > view["ema_20"] > view["ema_50"]
            below = close < view["ema_20"] < view["ema_50"]
            if above or below:
                result.add(Evidence(CAT_MOMENTUM, "ema_alignment", 1.0 if above else -1.0, 0.35 * weight, timeframe, "Close is %s EMA20 %s EMA50." % ("above" if above else "below", "above" if above else "below"), GATE_TREND))
        dm = view["adx"]
        if dm is not None and dm["adx"] >= 25:
            sign = 1.0 if dm["plus_di"] > dm["minus_di"] else -1.0
            result.add(Evidence(CAT_MOMENTUM, "adx_trend", sign, clamp((dm["adx"] - 20) / 40.0, 0.2, 0.6) * weight, timeframe, "ADX %.1f with %s directional index leading." % (dm["adx"], "+DI" if sign > 0 else "-DI"), GATE_TREND))
        if view["squeeze"]:
            result.add(Evidence(CAT_MOMENTUM, "squeeze", 0.0, 0.0, timeframe, "Bollinger bands are inside the Keltner channel: volatility squeeze, direction undecided.", GATE_ANY))
        z = view["volume_zscore"]
        if z is not None and z >= 2.5 and len(data.series(timeframe)) >= 2:
            bars = data.series(timeframe)
            last = bars[-1]
            direction = 1.0 if last.close > last.open else -1.0
            result.add(Evidence(CAT_VOLUME, "volume_climax", direction * 0.5, 0.35 * weight, timeframe, "Volume z-score %.1f on a %s bar; climactic volume can mark exhaustion or initiation." % (z, "up" if direction > 0 else "down"), GATE_ANY))
        slope = view["obv_slope"]
        if slope is not None and abs(slope) >= 0.1:
            result.add(Evidence(CAT_VOLUME, "obv_slope", 1.0 if slope > 0 else -1.0, clamp(abs(slope), 0.15, 0.4) * weight, timeframe, "OBV slope is %s over the last 20 bars." % ("rising" if slope > 0 else "falling"), GATE_TREND))
    result.findings = {"timeframes": views}
    focus_view = views.get(focus) or next(iter(views.values()))
    result.summary = "%s RSI %s, MACD histogram %s, ADX %s, volume z %s." % (
        focus,
        "n/a" if focus_view["rsi"] is None else "%.1f" % focus_view["rsi"],
        "n/a" if focus_view["macd"] is None else "%.2f" % focus_view["macd"]["histogram"],
        "n/a" if focus_view["adx"] is None else "%.1f" % focus_view["adx"]["adx"],
        "n/a" if focus_view["volume_zscore"] is None else "%.1f" % focus_view["volume_zscore"],
    )
    return result
