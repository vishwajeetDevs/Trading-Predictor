"""Frozen Phase 2 and Phase 3 parameters. A change here changes the definition hash."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

DEFINITION_VERSION = "phase3-v2"
TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
HORIZONS = (
    ("15m", 15 * 60),
    ("30m", 30 * 60),
    ("1h", 60 * 60),
    ("4h", 4 * 60 * 60),
    ("24h", 24 * 60 * 60),
)
PIVOT_WIDTH = {"1m": 5, "5m": 5, "15m": 3, "1h": 3, "4h": 2, "1d": 2}
HIGHER_TIMEFRAME = {"1m": "15m", "5m": "1h", "15m": "4h", "1h": "4h", "4h": "1d"}
STRUCTURE_GROUPS = {
    "short_term": ("1m", "5m"),
    "intraday": ("15m", "1h"),
    "higher": ("4h", "1d"),
}
MIN_SAMPLE = 30
FAIL_BARS = 3
ATR_PERIOD = 14
ATR_PERCENTILE_WINDOW = 100
RSI_PERIOD = 14
BB_PERIOD = 20
BB_MULT = 2.0
RV_WINDOW = 20
HIGH_VOL_PERCENTILE = 80.0
EXTREME_VOL_PERCENTILE = 95.0
TAIL_PERCENTILE = 0.05
TAIL_LOOKBACK = {"1h": 240, "4h": 180}
RANGE_BARS = 10
SNAPSHOT_BARS = 2000
BOOTSTRAP_SEED = 7
BOOTSTRAP_BLOCK = 5
BOOTSTRAP_DRAWS = 500
BOOTSTRAP_HORIZONS = ("4h", "24h")
BASE_RATE_SEPARATION = 0.05
VOL_TIMEFRAMES = ("15m", "1h", "4h")
TAIL_TIMEFRAMES = ("1h", "4h")


def parameters() -> dict:
    return {
        "version": DEFINITION_VERSION,
        "pivot_width": PIVOT_WIDTH,
        "fail_bars": FAIL_BARS,
        "rsi_period": RSI_PERIOD,
        "macd": [12, 26, 9],
        "bollinger": [BB_PERIOD, BB_MULT],
        "atr_period": ATR_PERIOD,
        "atr_percentile_window": ATR_PERCENTILE_WINDOW,
        "atr_percentile": "share of the window that is less than or equal to the latest value",
        "bollinger_stdev": "population divisor n",
        "ema_seed": "sma",
        "vwap": "typical price (high+low+close)/3, volume-weighted, resets on the UTC date of the bar open",
        "realized_vol": "sample stdev of log returns, divisor n-1, not annualized",
        "rsi": "wilder",
        "atr": "wilder true range",
        "structure": "last two confirmed swing highs and lows",
        "breakout": "close strictly beyond the level; a wick is not enough",
        "sampling": "edge-triggered raw matches are kept; published frequencies use a cooldown equal to the horizon so forward windows do not overlap. Raw matches are not independent events.",
        "base_rate": "non-overlapping closed bars of the same timeframe, same horizon, same exact-minute forward return and the same missing-minute exclusion",
        "forward_return": "close of the 1m bar that closes exactly one horizon after the signal close, divided by the signal close, minus 1",
        "min_sample": MIN_SAMPLE,
        "tail": {"percentile": TAIL_PERCENTILE, "lookback": TAIL_LOOKBACK},
        "high_vol_percentile": HIGH_VOL_PERCENTILE,
        "bootstrap": {
            "seed": BOOTSTRAP_SEED,
            "block_observations": BOOTSTRAP_BLOCK,
            "block_unit": "de-clustered observations one horizon apart",
            "draws": BOOTSTRAP_DRAWS,
            "horizons": list(BOOTSTRAP_HORIZONS),
        },
        "snapshot_bars": SNAPSHOT_BARS,
    }


def definition_hash(params: dict | None = None) -> str:
    body = json.dumps(params or parameters(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Bar:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def valid_bar(bar: Bar) -> bool:
    if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
        return False
    if bar.high < bar.low:
        return False
    if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
        return False
    return True
