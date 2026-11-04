# Phase 2 and 3 final validation

Audit date: 2026-11-04. The indicator, structure, level, and study modules were not rebuilt. Defects below were confirmed in the stored `phase3-v1` rows and in the live snapshot, then fixed. Phase 4 and Phase 5 were not started.

Definition after the fix: `phase3-v2`, hash `c75fff4ad0782095148e2c127003dd8722619e6c303399ebc6994144b0b56ad5`.

The previous definition remains in the database: hash `aa26586f31b6a05b9b6bfe7bf44ac3430f8cc8c397df7335f3c45e02eb430db8`, 175 horizon rows and 53,254 condition-match rows. Those rows are the overlapping historical record. They were not deleted.

## Issues and fixes

### 1. Base-rate comparison

**What was wrong.** Every condition, on every timeframe, was compared with one unconditional series: non-overlapping steps on the 1-minute candles. For a 24-hour horizon that series has 180 returns and a positive rate of 0.47777778. A 4-hour bullish sample of 38 was published as 5.7 percentage points below that 1-minute rate. A 1-day bullish sample of 5 withheld its own rate, but the response still attached the 1-minute base rate and the base count of 180.

**Root cause.** `base_rate_returns` walked only the 1-minute series, and that one result was copied onto every timeframe.

**Fix.** The base rate for a condition is now the unconditional forward return on closed bars of that same timeframe, for the same horizon, using the same forward-return function and the same missing-minute rule. A bar with no exact forward minute is excluded and does not consume the cooldown. The next kept bar is the earliest whose close is at least one horizon after the previous kept close. A difference is stored only when both the de-clustered condition sample and that base sample have at least 30 returns. Otherwise the status is `NOT_COMPARABLE`, and the rates and the difference are null.

**Check that the series are not copies.** The 24-hour unconditional returns were recomputed from stored candles. The 1-minute series has 180 returns and 86 positives, and its first three returns are 0.003153, 0.005881, 0.019842. The 5-minute and 15-minute series also have 180 returns, but 88 positives, and their first three returns are 0.003129, 0.005920, 0.019428. The 1-hour and 4-hour series have 180 returns, 88 positives, and first three returns 0.002483, 0.005970, 0.019136. The 5-minute list equals the 15-minute list. The 1-hour list equals the 4-hour list. The 5-minute list does not equal the 1-hour list (180 of 180 values differ).

That equality is the cooldown grid, not a shared array. The first 5-minute bar and the first 15-minute bar both close at 2026-05-07T23:30:00Z. The first 1-hour bar and the first 4-hour bar both close at 2026-05-08T00:00:00Z. A 24-hour cooldown then keeps that same close once a day. A 5-minute condition is still compared with the 5-minute grid, and a 4-hour condition with the 4-hour grid. The 1-minute grid does not land on those closes, which is why its positive count is 86 rather than 88.

### 2. Overlapping historical events

**What was wrong.** A 1-minute bullish condition with a 24-hour horizon kept 3,450 overlapping forward windows and published them as the sample. Consecutive matches share almost the whole forward day. They are not independent events.

**Fix.** Edge-triggered matches are still written to `condition_matches`. That is the raw record. Published frequencies use a cooldown equal to the horizon after matches with no exact forward minute are dropped. An excluded match does not consume the cooldown. Each horizon row stores the raw count, the de-clustered count, the number clustered out, and the number excluded. Percentiles, the base-rate comparison, and the bootstrap use only the de-clustered returns.

The de-clustered count is not a count of independent draws. Observations one horizon apart can still share volatility. The sampling note on each row says the raw matches overlap and that frequencies use the de-clustered sample only.

For 1-minute bullish structure at 24 hours, the identity is 173 + 3,277 + 20 = 3,470. The old row's sample of 3,450 plus its 20 excluded matches is the same 3,470 raw matches. The extra candles that arrived after the old study did not change this condition's raw count. The cooldown keeps 173 of the 3,450 eligible matches.

### 3. Bootstrap

**What was wrong.** Seed 7, block length 5, and 500 draws were reproducible, and the quantile is the linear interpolant of the resampled medians. The block length was 5 overlapping events. On the 3,450-point 24-hour sample the 10th to 90th percentile of resampled medians was only -0.000600486 to 0.000900376. That tight band is the overlapping-window artifact. The old payload also did not say how long a block was in time, and it did not refuse a block as long as the sample.

**Fix.** The bootstrap runs only on the de-clustered 4-hour and 24-hour samples, and only when that sample is larger than the block. The block is 5 de-clustered observations. Because those observations are one horizon apart, the block spans 5 horizons: 72,000 seconds at 4 hours and 432,000 seconds at 24 hours. The same seed still reproduces the band. The stored note says these quantiles are not a calibrated probability of future market movement.

On the de-clustered 1-minute bullish 24-hour sample of 173, seed 7 and 500 draws give a median of resampled medians of 0.001454898, with the 10th percentile -0.000112596 and the 90th percentile 0.002550381. The historical median of that same sample is 0.001454898. The band is a description of this resample, not a forecast.

### 4. Data freshness

**What was wrong.** The study status was `READY` while the study watermark lagged the latest closed minute. The supplied example was a market candle at 2026-11-04T05:58:00Z, a study watermark at 2026-11-04T05:52:00Z, and a computation time of 2026-11-04T05:54:52Z.

**Fix.** `READY` is no longer a study status. The snapshot separates market freshness from study freshness. Study fields are status, watermark, computation time, lag in seconds, and `up_to_date`. `CURRENT` and `up_to_date: true` require the study watermark to equal the latest closed 1-minute open. A later candle is `LAGGING`. No row for this definition is `NOT_COMPUTED`.

Live read after the API was restarted on the new code:

| Field | Value |
| --- | --- |
| Market watermark | 2026-11-04T06:29:00Z |
| Market freshness | about 102 seconds, not stale |
| Study status | `LAGGING` |
| Study watermark | 2026-11-04T06:17:00Z |
| Study computed at | 2026-11-04T06:19:17.637711Z |
| Study lag | 720 seconds |
| `up_to_date` | false |

`CURRENT` is covered by the offline snapshot test, where the study watermark is the latest seeded candle. It was not observed on the live API during this audit, because the live candle had moved past the study.

### 5. Structure list and nearest levels

**What was wrong.** The 1-minute swing list is the last two confirmed highs and lows that define the label. Nearest levels search every confirmed swing in the same closed-bar window. The payload did not say that, and it published only the confirmation time. Confirmation is the open of the right-side bar, which is the pivot width after the bar that printed the price. A reader could treat 77857.0, confirmed at 2026-11-03T13:37:00Z, as a price printed at 13:37. The price bar is 13:32. That pivot is a strict width-5 fractal. It was not a bad swing. It was also not one of the last two label swings.

**Fix.** The timeframe payload includes `swings_note`. Nearest and breakout levels include `price_time` for the pivot bar and `confirmed_at` for the confirming bar. Level sources say the search uses every confirmed swing in the window.

Live 1-minute read at the same snapshot as the freshness table. Label: `SIDEWAYS`. Width 5, so confirmation is five minutes after the price bar.

| Role | Price | Price bar | Confirmed |
| --- | --- | --- | --- |
| Label high | 78152.5 | 2026-11-04T05:50:00Z | 2026-11-04T05:55:00Z |
| Label high | 77900.5 | 2026-11-04T06:04:00Z | 2026-11-04T06:09:00Z |
| Label low | 77726.0 | 2026-11-04T06:13:00Z | 2026-11-04T06:18:00Z |
| Label low | 77748.0 | 2026-11-04T06:21:00Z | 2026-11-04T06:26:00Z |
| Nearest high | 77926.0 | 2026-11-03T20:58:00Z | 2026-11-03T21:03:00Z |
| Nearest low | 77900.5 | 2026-11-03T21:40:00Z | 2026-11-03T21:45:00Z |

The nearest prices are not the last two label swings. The nearest low shares the price 77900.5 with a later label high. They are different bars.

Previous UTC-day high and low at this read were 78039.5 at 2026-11-03T21:45:00Z and 76351.5 at 2026-11-03T06:13:00Z. Current-day high and low were 78152.5 at 05:50Z and 75693.0 at 03:08Z. The midpoint was 76922.75. At 06:29Z the latest 2,000 one-minute bars still cover the whole previous UTC day, so this live read does not exercise the late-day path. After about 09:20 UTC those 2,000 bars no longer start at the previous midnight. Day levels now load closed 1-minute bars from that midnight. The offline test inserts a previous-day extreme outside a short window and the loader returns it.

### 6. Look-ahead

Reviewed and covered by deterministic tests:

- A forward return uses the signal close and the 1-minute close at signal close plus the horizon minus 60 seconds. Editing a bar after that minute does not change the return. Deleting that exact minute excludes the observation even when a later minute exists.
- The path for favorable and adverse excursion starts at the first 1-minute bar whose open is at or after the signal close.
- A pivot is not confirmed until the right-side bars have closed. `confirmed_at` is that confirming bar's open.
- An extreme-downside event uses only earlier returns for its threshold. Replacing the last bar does not change earlier events.
- Tail screens on the live snapshot, for both 1 hour and 4 hours, said: "Current conditions do not match the historical tail definition."

## Before and after samples

Old rows are hash `aa26586f…`. New rows are hash `c75fff4a…`. The old code stored one sample size and did not store a de-clustered count. The old 24-hour base rate is the 1-minute grid in every row below.

| Case | Before | After |
| --- | --- | --- |
| 1m bullish, 24h | Sample 3,450, excluded 20, positive rate 0.50434783, median 0.000152919. Base 0.47777778 on 180 one-minute steps. Difference 0.02657005. Bootstrap n=3,450, block 5, median of medians 0.000135386, p10 -0.000600486, p90 0.000900376. | Raw 3,470. De-clustered 173. Clustered out 3,277. Excluded 20. Positive rate 0.54335260, median 0.001454898. Base is the 1-minute grid, 0.47777778 on 180, status `OK`. Difference 0.06557482. Bootstrap n=173, block span 432,000 seconds, median of medians 0.001454898, p10 -0.000112596, p90 0.002550381. |
| 4h bullish, 24h | Sample 38, excluded 0, positive rate 0.42105263. Compared with the 1-minute base 0.47777778 on 180. Difference -0.05672515. | Raw 38. De-clustered 37. Clustered out 1. Excluded 0. Compared with the 4-hour grid, positive rate 0.48888889 on 180, status `OK`. Difference -0.05645646. |
| 1d bullish, 24h | Sample 5. Own rate withheld. The 1-minute base rate 0.47777778 and count 180 were still stored. Difference null. | Raw 5. De-clustered 5. Status `INSUFFICIENT_SAMPLE`. Comparison `NOT_COMPARABLE`. Base rate null. Difference null. The base count 180 is still reported so the withheld comparison can be seen. Explanation: "NOT_COMPARABLE. More history is required before a frequency is shown." |
| 1h extreme downside, 24h | Sample 194, excluded 0, positive rate 0.50515464, median 0.000054416. Base 0.47777778 on 180. Difference 0.02737686. Bootstrap n=194. | Raw 194. De-clustered 92. Clustered out 102. Excluded 0. Base is the 1-hour grid, 0.48888889 on 180, status `OK`. Difference 0.04371981. Bootstrap n=92, block span 432,000 seconds. |

4-hour high volatility stays under 30 after the cooldown. At 24 hours the raw count is 20, the de-clustered count is 18, and the comparison is `NOT_COMPARABLE`. Daily conditions remain under 30 on every horizon.

The study command reported 53,254 raw matches and 175 horizon rows in 305.287 seconds. Raw event counts by timeframe: 1m 16,751 breakouts, 8,328 failed, 7,016 sideways, 3,555 bearish, 3,470 bullish. 5m 3,324 / 1,693 / 1,629 / 814 / 818. 15m 1,418 / 706 / 883 / 445 / 441 plus 290 high volatility. 1h 351 / 164 / 227 / 112 / 115 plus 108 high volatility and 194 extreme downside. 4h 108 / 47 / 73 / 38 / 37 plus 20 high volatility and 38 extreme downside. 1d 13 / 7 / 10 / 6 / 5.

## Methodology now in force

- Raw matches stay in `condition_matches` and can overlap.
- Published frequencies use the horizon cooldown described above.
- Base rate uses that same cooldown on every closed bar of the condition's timeframe.
- Comparison status is `OK` only when both samples have at least 30 returns. Otherwise `NOT_COMPARABLE`.
- Block bootstrap: seed 7, 500 circular-block resamples, block length 5 de-clustered observations, linear quantile. A block spans five horizons. The result is not a future probability.
- Study status is `CURRENT`, `LAGGING`, or `NOT_COMPUTED`. `CURRENT` is the only up-to-date state.

`docs/STATISTICAL_METHODOLOGY.md` and the level section of `docs/MARKET_STRUCTURE_DEFINITIONS.md` were updated to this version. Alembic revision `0003_sampling` adds nullable `raw_match_count`, `independent_count`, `clustered_out`, and `comparison_status` on `horizon_stats`. Older rows keep nulls in those columns.

## Test results

Offline, from `btc-market-intelligence\backend`:

```powershell
..\.venv\Scripts\python.exe -m pytest -q --tb=line
```

Result on the final code: 83 passed, 5 skipped, 1 warning, 4.88 seconds. The five skips are the live smoke tests and the Postgres integration test, which run only when their environment flags are set. They were not run.

New or extended checks in that run:

- Two events one minute apart, 15-minute horizon: raw 2, de-clustered 1, clustered out 1.
- A 1-hour bar's base return uses that bar's close, not the 1-minute close.
- A base sample of 10 against a condition sample of 30 is `NOT_COMPARABLE`, and the difference is null.
- A forward return ignores a bar after the horizon and refuses a later minute as a substitute.
- Bootstrap span equals block length times the sampling interval. A block at least as long as the sample returns no bootstrap. The note contains "not a calibrated probability".
- A newer closed candle makes the snapshot `LAGGING`, with lag at least 60 seconds and `up_to_date` false.
- A previous-UTC-day extreme outside the short indicator window is still loaded.
- A matching watermark is `CURRENT`, not `READY`.

Dashboard, from `verification-dashboard`:

```powershell
npm test
```

Result: 4 passed. The checklist accepts `0001_initial`, `0002_analysis`, and `0003_sampling`.

Live API after restart: `GET /health` status ok, database connected, public socket connected, stale false, latest closed candle 2026-11-04T06:28:00Z at that health read. `GET /diagnostics` reported Alembic revision `0003_sampling`. The verification page at `http://localhost:5173` showed PASS for database migrations with the text "Alembic revision 0003_sampling and the five market-data tables are present." The other visible checks on that page were PASS, including REST gap recovery, API responses, candle persistence, and the live socket. Stored one-minute candles on the page: 259,624. Total candle rows: 334,330.

## Commands that produced the new study

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\backend"
..\.venv\Scripts\python.exe -m app.cli migrate
..\.venv\Scripts\python.exe -m app.cli study
```

`migrate` applied `0002_analysis -> 0003_sampling`. The first `study` attempt failed when the database connection closed during the write, after the scan had held that connection open. The scan now releases the connection before the CPU work and measures one horizon at a time. A second attempt failed because the database host did not resolve. The third attempt committed the hash and counts above. The connection string was not printed.

The API process was then restarted so `/analysis/snapshot` loaded this code. Its first background study failed the same host lookup and was not run again during this audit. The next attempt is the existing 900-second loop. The snapshot read above is the committed CLI study, lagged by 720 seconds. That lag is reported as `LAGGING`.

## Remaining limitations

- De-clustered events are one horizon apart. They are still dependent. The block bootstrap describes that sample. It is not a probability of a future return.
- A 24-hour cooldown on 5-minute and 15-minute bars can select the same close timestamp, and the same is true for 1-hour and 4-hour bars. The unconditional 24-hour rates then match even though the timeframe of the comparison is still the condition's timeframe.
- Daily samples, and 4-hour high volatility, stay below 30. No frequency or difference is published for them.
- Higher-timeframe candles still end before the latest 1-minute candle. Ingestion was not changed.
- The live clock during this audit was 06:29Z, so the previous UTC day still fit inside 2,000 one-minute bars. The after-09:20 path was verified by the offline test, not by the live snapshot.
- The background study had not caught the snapshot up to the latest minute when this report was written.
- `GET /analysis/snapshot` does not rescan history. A lagged study stays lagged until the study command or the background loop commits a new watermark.

No Phase 4 or Phase 5 work was started. Waiting for approval.
