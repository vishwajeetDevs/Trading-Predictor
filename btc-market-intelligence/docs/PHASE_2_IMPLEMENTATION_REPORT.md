# Phase 2 implementation report

Read-only indicators, structure, levels, and events on the existing BTC candle store. Phase 1 ingestion was not rewritten. No orders, stops, sizing, or trade signals were added.

Checked on 2026-11-04 after Alembic `0002_analysis` and one full study. The API process was restarted so these routes are the ones listening on `127.0.0.1:8000`.

## 1. Implemented modules

| Module | Role | Status |
| --- | --- | --- |
| `backend/app/analysis/definitions.py` | Frozen parameters, bar checks, definition hash | VERIFIED WORKING |
| `backend/app/analysis/indicators.py` | RSI, SMA, EMA, MACD, UTC VWAP, Bollinger, ATR, ATR percent, ATR percentile, realized volatility | VERIFIED WORKING |
| `backend/app/analysis/structure.py` | Confirmed swings and bullish, bearish, sideways, or insufficient labels | VERIFIED WORKING |
| `backend/app/analysis/events.py` | Close breakouts, failed breakouts, pullbacks, reversals | VERIFIED WORKING |
| `backend/app/analysis/levels.py` | UTC-day extremes, range midpoint, nearest swings, distances | VERIFIED WORKING |
| `backend/app/analysis/snapshot.py` | Per-timeframe view and the comparison object | VERIFIED WORKING |
| `backend/app/api/routes/analysis.py` | `GET /analysis/snapshot` and `GET /analysis/history` | VERIFIED WORKING |
| `docs/MARKET_STRUCTURE_DEFINITIONS.md` | Frozen rules | VERIFIED WORKING |

The formulas and widths are in `MARKET_STRUCTURE_DEFINITIONS.md`. Tests cover the textbook Wilder RSI series (70.464), hand-calculated SMA and EMA, a constant MACD line of 1.5 on an arithmetic series, a UTC-day VWAP reset, population Bollinger bands, Wilder ATR, insufficient history, higher-high and lower-low series, a wick that is not a breakout, a failed breakout, and pullback versus reversal.

## 2. API changes

Existing routes are unchanged. `GET /` now also lists `/analysis/snapshot` and `/analysis/history`.

`GET /analysis/snapshot` returns the product, freshness, the latest closed 1-minute open as the watermark, indicators, one structure label per timeframe, levels, the latest breakout and failed breakout in the loaded window, pullback and reversal labels, study rows when they have been stored, the tail screen, and insufficient-history warnings. It reads at most 2000 closed bars per timeframe. It does not scan the full history.

A live read on 2026-11-04 returned HTTP 200. One call took about 11 seconds and a later call took about 19 seconds. Labels at the later read:

| Timeframe | Label |
| --- | --- |
| 1m | SIDEWAYS |
| 5m | BULLISH |
| 15m | BULLISH |
| 1h | SIDEWAYS |
| 4h | SIDEWAYS |
| 1d | BEARISH |

Those labels are not combined. The 1-hour level block included previous UTC-day high 78039.5, previous UTC-day low 76351.5, current UTC-day high 78152.5, current UTC-day low 75693.0, range midpoint 76922.75, nearest swing high 77880.0, and nearest swing low 77815.5. Each row had a percent distance. The response did not contain stop-loss, take-profit, or leverage text.

`GET /analysis/history` returned the one stored study summary. Both routes are GET-only. A missing product returns 404. A database error returns 503 with the connection string removed. That failure path was tested offline.

## 3. Database migrations

Phase 2 does not add its own tables. The snapshot is computed from `candles`. Persistence of studies is the Phase 3 migration `0002_analysis`.

## 4. Test results

```text
78 passed, 5 skipped
```

The five skips are the existing live-smoke and PostgreSQL integration tests. They were not run in this pass. Offline tests do not call Delta.

Dashboard checklist tests: 4 passed. On the open verification page after the restart, backend startup, PostgreSQL, migrations (`0002_analysis` and the five market-data tables), product, history, WebSocket, candle counts, duplicate prevention, REST gap recovery, and API responses were PASS.

## 5. Performance

The snapshot does not resimulate history. The live response still took about 11 seconds on the first measured call and about 19 seconds on the call after the restart. Most of that is reading six windows from the hosted database and walking those windows. It is not the 146-second historical study.

## 6. Known limitations

- The live label and the nearest swing use the latest 2000 closed bars, or the whole series when it is shorter. Daily structure uses all 180 stored daily bars. A swing older than the 2000-bar window is not a live level.
- The published swing list on each timeframe is the last two confirmed swings, which are the swings that define the label. The nearest-level search uses every confirmed swing in the loaded window.
- Stored 5m, 15m, 1h, 4h, and 1d candles stop earlier than the 1-minute series. At the study read, 1d ended 2026-11-02, 4h ended 2026-11-03 16:00 UTC, and 1m continued into 2026-11-04. Higher-timeframe labels are therefore behind the latest minute. Ingestion was not changed.
- ATR percentile and realized volatility are null until their windows are full. Those warnings are listed, not replaced with a number.
- Phase 4 and Phase 5 were not started.

## 7. Actual historical sample sizes

Sample sizes belong to the stored study and are listed in `PHASE_3_IMPLEMENTATION_REPORT.md`.

## 8. Unsupported or insufficient-history fields

On a short window, RSI, SMA, EMA, MACD, Bollinger, ATR, ATR percent, ATR percentile, and realized volatility are null. The live 1-minute window is long enough for those indicators. A timeframe with fewer than two confirmed highs and two confirmed lows is `INSUFFICIENT_DATA`. Empty higher timeframes in the offline fixture take that label. The live store had a label on all six timeframes at the check above.

## 9. PowerShell

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\backend"
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m app.cli serve
```

In a second window, after the server is up:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/analysis/snapshot
```

The verification page is `http://localhost:5173/` when the dashboard dev server is already running.
