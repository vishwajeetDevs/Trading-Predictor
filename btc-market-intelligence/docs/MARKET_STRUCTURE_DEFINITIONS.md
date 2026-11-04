# Market structure definitions

These rules are frozen as definition version `phase3-v1`. The hash is the SHA-256 of the canonical JSON from `parameters()` in `backend/app/analysis/definitions.py`. A change to a parameter or to the wording recorded in that function changes the hash. Studies for an older hash are kept.

The hash verified with the stored study is `aa26586f31b6a05b9b6bfe7bf44ac3430f8cc8c397df7335f3c45e02eb430db8`.

Nothing in this document is an order, a stop, or a forecast.

## Candles

- Every structure label, indicator, and event uses closed candles only. A candle is closed when its timeframe has finished.
- A bar is ignored when a price is not positive, volume is negative, high is below low, or open or close sits outside the high-low range.
- Indicator values are `null` when the lookback is incomplete. They are not filled from an older bar and they are not treated as a trade signal.
- A pivot can be confirmed only after its right-side bars have closed. Changing a later bar does not change a classification whose confirmation bar is already closed.

## Pivot width

A swing high at index `i` requires `high[i]` to be strictly greater than the highs of the `width` bars on each side. A swing low uses a strictly lower low. Equal prices are not swings.

The confirming bar is index `i + width`. Its open time is the confirmation timestamp stored on the pivot. The bar is already closed, so the pivot was not available before that bar closed.

| Timeframe | Width |
| --- | --- |
| 1m | 5 |
| 5m | 5 |
| 15m | 3 |
| 1h | 3 |
| 4h | 2 |
| 1d | 2 |

## Structure label

The label uses the last two confirmed swing highs and the last two confirmed swing lows.

- `BULLISH` when the latest high is above the previous high and the latest low is above the previous low.
- `BEARISH` when the latest high is below the previous high and the latest low is below the previous low.
- `SIDEWAYS` otherwise, including equal highs or equal lows. `overlapping` is true for every sideways label.
- `INSUFFICIENT_DATA` when two confirmed highs and two confirmed lows do not yet exist.

`range_bound` is true only for a sideways label whose last 10 closed bars all closed between the latest swing low and the latest swing high.

Higher high, higher low, lower high, and lower low are reported as their own flags. They are not added into a score.

Timeframes are not merged. The groups below are only a reading order:

- Short term: 1m and 5m.
- Intraday: 15m and 1h.
- Higher: 4h and 1d.

The higher timeframe used for a pullback is 1m to 15m, 5m to 1h, 15m to 4h, 1h to 4h, and 4h to 1d. The daily label has no higher timeframe inside this set.

The live label is computed from the latest 2000 closed bars of that timeframe, or from every stored closed bar when fewer than 2000 exist. The historical study walks the full stored series.

## Events

Structure events fire when the label changes to bullish, bearish, or sideways. They do not repeat on later bars that keep the same label.

A confirmed breakout is a closed candle whose close is strictly beyond the latest swing high or swing low that was already confirmed on an earlier bar. The previous close must still be on the near side of that level. A wick through the level is not a breakout. The same side does not fire again until price has closed back inside the level.

A failed breakout is a later close back through that level within 3 bars, including the bar after the break. After 3 bars the watch ends. Its direction is the opposite of the original break.

A pullback is a move against the higher-timeframe label that has not crossed the last confirmed swing of that higher timeframe:

- Bullish higher timeframe, close still above its latest swing low, and the close is below the previous close.
- Bearish higher timeframe, close still below its latest swing high, and the close is above the previous close.

A reversal is a change between bullish and bearish in the confirmed structure labels. Pullback and reversal are separate. One can be true while the other is not.

## Levels

Each published level has a price, a source, a timeframe, the distance from the latest completed close, that distance as a percent of the close, that distance in ATR(14) units when ATR is available, and a confirmation time. These are measured prices. They are not stops or entries.

- Previous and current UTC-day high and low come from closed 1-minute candles from the previous UTC midnight through the latest closed minute. The indicator window of 2000 bars does not cut that day off after about 09:20 UTC.
- The range midpoint is the average of the current UTC-day high and low.
- The published swing list is the last two confirmed highs and lows that define the label. Nearest levels search every confirmed swing in the same closed-bar window. A nearest level can be older than the last two label swings.
- The nearest swing high is the lowest confirmed swing high at or above the latest close. The nearest swing low is the highest confirmed swing low at or below the latest close. If price is beyond every pivot in that window, the latest pivot on that side is reported and no breakout level is added beyond it.
- `confirmed_at` is the open time of the bar that completes the right-side width. `price_time` is the open time of the pivot bar itself, which is `width` bars earlier.
- A breakout level above is a confirmed swing high still above the close. A breakout level below is a confirmed swing low still below the close.

## Indicator parameters

These are descriptive. A value is not a buy or sell signal.

| Indicator | Rule |
| --- | --- |
| RSI | Wilder smoothing, period 14. Needs 15 closes. |
| SMA | Arithmetic mean of the last `period` closes. The snapshot uses 20. |
| EMA | Seeded with the SMA of the first `period` values. Multiplier `2/(period+1)`. The snapshot uses 20. |
| MACD | EMA 12 minus EMA 26. Signal is an EMA 9 of the defined MACD values, also SMA-seeded. Histogram is MACD minus signal. |
| VWAP | Typical price `(high+low+close)/3`, weighted by volume, for the UTC date of the last bar only. |
| Bollinger | Period 20, multiple 2. The standard deviation divides by `n`, not `n-1`. |
| ATR | Wilder true range, period 14. The first value averages the first 14 true ranges that have a previous close. |
| ATR percent | ATR divided by the latest close, times 100. |
| ATR percentile | Share of the last 100 ATR-percent values that are less than or equal to the latest, times 100. |
| Realized volatility | Sample standard deviation of the last 20 log returns. Divisor `n-1`. Not annualized. |

Volatility words on the snapshot use the ATR-percent percentile: `EXTREME` at or above 95, `HIGH` at or above 80, `LOW` at or below 20, otherwise `NORMAL`. Missing history is `INSUFFICIENT_DATA`.
