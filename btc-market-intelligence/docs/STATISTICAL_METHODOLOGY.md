# Statistical methodology

This note defines the historical studies. The numbers describe stored BTC perpetual candles. They are not a probability of a future move, and a positive frequency above 50% is not by itself evidence that price is more likely to rise.

Definition version `phase3-v1`. Verified hash `aa26586f31b6a05b9b6bfe7bf44ac3430f8cc8c397df7335f3c45e02eb430db8`.

## Conditions

Each condition is its own sample. Matches are not pooled.

| Condition | Rule |
| --- | --- |
| Bullish, bearish, sideways structure | The structure label changes to that state. Later bars that keep the label are not new matches. |
| Confirmed breakout | The close-strict break in `MARKET_STRUCTURE_DEFINITIONS.md`. |
| Failed breakout | The close back through the broken level within 3 bars. |
| High volatility | The ATR-percent percentile first reaches 80 on 15m, 1h, or 4h. It is not computed on 1m, 5m, or 1d. The condition rearms after the percentile falls below 80. |
| Extreme downside | On 1h, the bar's log return is at or below the 5th percentile of the previous 240 one-hour log returns. On 4h the lookback is 180. The current bar is not inside its own threshold. Consecutive tail bars count as one episode until a bar is no longer in the tail. |

The live crash screen asks a different question from the historical episode list: whether the latest closed 1h or 4h bar itself meets that tail rule. When it does, the sentence is exactly `Current conditions match the historical tail definition.` When it does not, the sentence says that it does not match. Neither sentence says that a crash is coming.

## Forward return

The signal time is the open time of the closed bar that produced the condition. The entry price is that bar's close.

The forward close is the close of the 1-minute bar that closes exactly one horizon after the signal bar closes. Horizons are 15 minutes, 30 minutes, 1 hour, 4 hours, and 24 hours.

```
forward return = forward_1m_close / entry_close - 1
```

If that exact minute is missing, or the entry close is not positive, the observation is excluded. A later minute is not substituted. Excluded observations are counted and are not invented.

Maximum favorable excursion and maximum adverse excursion use 1-minute bars whose open time is at or after the signal bar's close and at or before the forward bar's open. The signal bar itself is not in the path.

```
upside = max(high) / entry_close - 1
downside = 1 - min(low) / entry_close
```

- Direction +1 (bullish structure, breakout up): favorable is upside and adverse is downside.
- Direction -1 (bearish structure, breakout down, extreme downside): the two are swapped.
- Direction 0 (sideways, high volatility): favorable is the larger of the two and adverse is the smaller.
- A failed breakout uses the opposite of the original break direction.

## Summaries

For a sample of returns:

- Median, 10th percentile, and 90th percentile use linear interpolation: position `q * (n - 1)` between sorted values.
- Positive frequency counts returns strictly above zero. Negative frequency counts returns strictly below zero. Zeros are in neither.
- Median favorable and adverse excursions use the same percentile function.

If the sample has fewer than 30 returns, the status is `INSUFFICIENT_SAMPLE`. The count and the excluded count are returned. The frequencies and percentiles are null. The threshold is not lowered.

## Base rate

The base rate for a condition uses the same timeframe, the same forward horizon, and the same forward-return rule as that condition. Every closed bar of that timeframe is a candidate. A bar whose exact forward minute is missing is excluded and does not consume the cooldown. Among the remaining bars, the next kept bar is the earliest whose close time is at least one horizon after the previous kept close. The 1-minute base rate is not reused for a 4-hour or 1-day condition.

The comparison is published only when both the de-clustered condition sample and this same-timeframe base sample have at least 30 returns. Otherwise the status is `NOT_COMPARABLE`, and the rates and the difference are null. When both samples qualify, the difference is the condition's positive frequency minus the base rate. A gap under 5 percentage points is described as no meaningful separation in this stored sample. A larger gap is a difference in this stored sample. Neither is a forecast.

## Dependence

Edge-triggered matches are the raw historical record. They are stored in `condition_matches` and are not independent: a 1-minute condition with a 24-hour horizon can match on consecutive minutes, and those forward windows overlap.

Published frequencies use a second sample. After dropping matches whose exact forward minute is missing, a cooldown equal to the horizon keeps the earliest remaining match and then the next match whose close is at least one horizon later. The horizon row reports the raw match count, the de-clustered count, the number clustered out, and the number excluded for a missing forward minute. Percentiles, the base-rate comparison, and the bootstrap use only the de-clustered returns.

## Block bootstrap

Used only when the horizon is 4h or 24h and the de-clustered sample has at least 30 returns. It is not run when the block length is at least the sample size.

- Seed 7, block length 5 de-clustered observations, 500 draws.
- Because those observations are one horizon apart, a block of 5 spans five horizons.
- Each draw is a circular block sample of the de-clustered returns, of the same length as that sample.
- The reported band is the 10th, 50th, and 90th percentile of the resampled medians, using linear interpolation.
- The same seed reproduces the same band. The note says these quantiles are not a calibrated probability of future market movement.

## Follow-through after an extreme downside

This uses the median forward return of that extreme-downside sample only.

- Median below zero: `continued_downward`.
- Median above zero: `reversed`.
- Median equal to zero: `stabilized`.
- No median, including an insufficient sample: `INSUFFICIENT_HISTORY`.

## What the job stores

`study` walks each timeframe once, writes condition matches and horizon rows, and commits. `GET /analysis/snapshot` reads those rows. It does not run the historical scan. A run is skipped when the same definition hash and the same latest closed 1-minute open time are already stored with status `ok`. A new closed minute changes that watermark, so the next background pass recomputes the study. The pass interval is 900 seconds. Rows for an older hash are not deleted.

## No look-ahead

- Pivot confirmation waits for the right-side bars.
- Breakout levels are swings confirmed on an earlier bar.
- Tail thresholds use returns strictly before the bar being classified.
- Forward returns use only minutes after the signal close.
- Tests change a future bar and require the earlier structure label, and the earlier tail events, to stay the same.
