# Phase 3 implementation report

Historical condition studies on the candles already stored for the BTC perpetual. The study is a separate command and a background pass. The snapshot route reads the stored rows.

Checked on 2026-11-04. One study finished in 146.302 seconds, stored 53,252 condition matches and 175 horizon rows, and was then read back through `GET /analysis/snapshot`.

## 1. Implemented modules

| Module | Role | Status |
| --- | --- | --- |
| `backend/app/analysis/studies.py` | Volatility events, tail events, forward returns, base-rate clock | VERIFIED WORKING |
| `backend/app/analysis/stats.py` | Quantiles, minimum sample, base-rate text, seeded block bootstrap, follow-through | VERIFIED WORKING |
| `backend/app/services/study_service.py` | Full scan, persistence, snapshot assembly | VERIFIED WORKING |
| `backend/app/database/models.py` | `study_runs`, `condition_matches`, `horizon_stats`, `analysis_snapshots` | VERIFIED WORKING |
| `migrations/versions/20261104_0002_analysis.py` | Upgrade from `0001_initial` | VERIFIED WORKING |
| `python -m app.cli study` | One-shot study. Migrates first, then scans | VERIFIED WORKING |
| Background pass | Runs when `enable_studies` is true, every 900 seconds, off the request thread | IMPLEMENTED BUT NOT VERIFIED |
| `docs/STATISTICAL_METHODOLOGY.md` | Formulas and sampling | VERIFIED WORKING |
| `docs/MARKET_STRUCTURE_DEFINITIONS.md` | Frozen labels used by the study | VERIFIED WORKING |

The background pass is started by the running server. This check did not wait for a second 146-second pass to finish, so that loop is not marked verified. The one-shot command is the run that produced the numbers below. A second call with the same watermark returns `current` and does not insert another copy. That skip was tested on SQLite.

Phase 4 and Phase 5 were not started. No order, stop, size, or signal was added.

## 2. API changes

`GET /analysis/snapshot` adds `studies`, `crash_screen`, `definition_hash`, and `definition_version`.

At the live read the study status was `READY` and the hash was `aa26586f31b6a05b9b6bfe7bf44ac3430f8cc8c397df7335f3c45e02eb430db8`. Both the 1-hour and 4-hour tail screens said `Current conditions do not match the historical tail definition.`

`GET /analysis/history` returns stored study summaries, not a row per HTTP poll. The live call returned one snapshot. Frequencies below the minimum sample are null.

## 3. Database migrations

`alembic upgrade` applied `0001_initial -> 0002_analysis` on the configured PostgreSQL database. Existing candle rows were not dropped. Diagnostics then reported revision `0002_analysis` and the original five market-data tables still present.

New tables:

- `study_runs` stores the hash, watermark, time, and match counts.
- `condition_matches` stores one row per condition event.
- `horizon_stats` stores one row per timeframe, condition, and horizon.
- `analysis_snapshots` stores the compact summary written by the study command. GET requests do not insert a snapshot.

The offline PostgreSQL integration test, which uses schema `bmi_itest`, was not run in this pass. It now accepts either revision and, when the revision is `0002_analysis`, expects the four new tables. That assertion is NOT VERIFIED against a live `bmi_itest` schema in this pass.

## 4. Test results

```text
78 passed, 5 skipped
```

Statistics tests covered a known 15-minute forward return of 0.10 with favorable excursion 0.20 and adverse excursion 0.10, a non-overlapping base-rate step of 4 samples across 61 minutes, refusal of a 29-observation sample, a deterministic bootstrap, the tail sentence, and a future-bar edit that left earlier tail events unchanged. API tests covered the snapshot schema, a stored match count of 3 on the synthetic bullish series, `INSUFFICIENT_SAMPLE` with a null frequency, a hash change that kept the old matches, and a 503 that hid the connection string.

Dashboard vitest: 4 passed. The open verification page stayed PASS after the API restart, including revision `0002_analysis`.

## 5. Performance

The verified study took 146.302 seconds and wrote 53,252 matches. It loads each timeframe once, measures forwards against a 1-minute index, and inserts in batches of 400. `GET /analysis/snapshot` does not run that scan. A repeated study with the same latest minute is a no-op.

The snapshot itself took about 11 seconds, then about 19 seconds, on the hosted database. That is the bounded candle read and the structure walk.

A new closed minute changes the watermark, so the next background pass recomputes the whole study rather than patching one bar. The interval is 900 seconds. A 146-second job fits in that interval, and it is still a full recompute.

## 6. Known limitations

- Matches are edge-triggered, but nearby events both stay in the sample. The 4-hour and 24-hour bootstrap is the dependence check. It is not a forecast.
- The 1-minute breakout rule re-arms after price closes back inside the latest swing, so that sample is large: 16,751 breakouts. Treat it as a description of that rule, not as 16,751 independent episodes.
- Higher-timeframe candles lag the 1-minute series. See the Phase 2 report. The study used what was stored.
- The background recompute was not watched through to a second commit.
- Phase 4 and Phase 5 were not started.

## 7. Actual historical sample sizes

The study watermark was the 1-minute bar that opened at 2026-11-04T05:45:00Z. Computation finished at 2026-11-04T05:47:46Z.

Closed bars available to that scan:

| Timeframe | Closed bars | Oldest open | Newest open |
| --- | --- | --- | --- |
| 1m | 259,581 | 2026-05-07T23:28:00Z | 2026-11-04T05:48:00Z |
| 5m | 51,844 | 2026-05-07T23:25:00Z | 2026-11-03T23:40:00Z |
| 15m | 17,282 | 2026-05-07T23:15:00Z | 2026-11-03T23:30:00Z |
| 1h | 4,320 | 2026-05-07T23:00:00Z | 2026-11-03T22:00:00Z |
| 4h | 1,080 | 2026-05-07T20:00:00Z | 2026-11-03T16:00:00Z |
| 1d | 180 | 2026-05-07T00:00:00Z | 2026-11-02T00:00:00Z |

Condition matches. The 15-minute forward sample equals this count except where a later horizon notes exclusions. Longer horizons drop a few events at the end of the series when the exact future minute is missing.

| Timeframe | Bullish | Bearish | Sideways | Breakout | Failed breakout | High volatility | Extreme downside |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1m | 3,469 | 3,555 | 7,015 | 16,751 | 8,328 | not computed | not computed |
| 5m | 818 | 814 | 1,629 | 3,324 | 1,693 | not computed | not computed |
| 15m | 445 | 441 | 883 | 1,418 | 706 | 290 | not computed |
| 1h | 112 | 115 | 227 | 351 | 164 | 108 | 194 |
| 4h | 38 | 37 | 73 | 108 | 47 | 20 | 38 |
| 1d | 5 | 6 | 10 | 13 | 7 | not computed | not computed |

Examples read back from `horizon_stats`:

- 1-minute bullish structure, 1-hour horizon: 3,468 returns, 1 excluded, first match 2026-05-08T01:51:00Z, last match 2026-11-04T05:01:00Z. Positive frequency 0.4850 against a base rate of 0.4979.
- 1-hour extreme downside, 24-hour horizon: 194 returns, 0 excluded, first 2026-05-18T13:00:00Z, last 2026-11-03T02:00:00Z. Positive frequency 0.5052 against a base rate of 0.4778. The stored text says that is within 5 percentage points and is not a meaningful separation.

Unconditional base rates on the non-overlapping 1-minute clock were about 0.4919 at 15 minutes, 0.4991 at 30 minutes, 0.4979 at 1 hour, 0.5023 at 4 hours, and 0.4778 at 24 hours.

Follow-through uses the median return of the extreme-downside sample. It describes those past episodes only.

| Sample | 15m | 30m | 1h | 4h | 24h |
| --- | --- | --- | --- | --- | --- |
| 1h extreme downside, n=194 | reversed | reversed | continued_downward | reversed | reversed |
| 4h extreme downside, n=38 | reversed | reversed | continued_downward | reversed | reversed |

The 1-hour median at the 1-hour horizon was -0.000141. The 15-minute median was +0.000190. The current 1-hour and 4-hour bars did not match the tail rule.

Bootstrap rows exist for OK samples at the 4-hour and 24-hour horizons. They were not computed for 15-minute, 30-minute, or 1-hour horizons, and they were not computed for insufficient samples. Seed 7, block length 5, 500 draws.

## 8. Unsupported or insufficient-history fields

`INSUFFICIENT_SAMPLE` with a null frequency, for every horizon:

- Every 1-day condition. Counts are 5, 6, 10, 13, and 7.
- 4-hour high volatility. Count is 20.

Not computed, by the frozen definition, rather than missing data:

- High volatility on 1m, 5m, and 1d.
- Extreme downside on 1m, 5m, 15m, and 1d.
- Bootstrap outside 4h and 24h.

The minimum of 30 was not lowered.

## 9. PowerShell

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\backend"
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m app.cli migrate
..\.venv\Scripts\python.exe -m app.cli study
..\.venv\Scripts\python.exe -m app.cli serve
```

`study` prints match counts and the definition hash. It does not print the database URL. A second `study` with no new minute returns `current`.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/analysis/snapshot
Invoke-RestMethod "http://127.0.0.1:8000/analysis/history?limit=20"
```

Dashboard tests:

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\verification-dashboard"
npm test
```
