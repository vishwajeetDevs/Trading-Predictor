# Final system verification report

Date: 2026-11-04.

Read-only BTC market intelligence for Delta Exchange India. Phase 4 derivatives collection and Phase 5 unpacked Chrome extension are in the tree. No further product changes are in scope until they are requested.

## Running process

The API was restarted so the Phase 4 routes are loaded. It listens on `http://127.0.0.1:8000`. After startup the log shows public product, ticker, L2, and trades requests returning HTTP 200, then the public India WebSocket connected.

A later `GET /health` returned 200, database connected, websocket connected, and candle `stale` false.

`GET /analysis/snapshot` returned 200 with definition `phase3-v2` and study status `LAGGING` at the check immediately after restart. That status is reported as returned. It was not forced to `CURRENT`.

Phase 1 candle routes, `/products/btc`, and `/data/status` continued to answer during the same process (200s in the server log). Phase 2 and 3 snapshot and history routes are the same handlers as before this phase.

## Derivatives HTTP

| Route | HTTP | Availability | Stale at the dedicated check |
| --- | --- | --- | --- |
| `/derivatives/funding` | 200 | AVAILABLE | false |
| `/derivatives/open-interest` | 200 | AVAILABLE | false |
| `/derivatives/mark-price` | 200 | AVAILABLE | false |
| `/derivatives/orderbook` | 200 | AVAILABLE | false |
| `/derivatives/snapshot` | 200 | AVAILABLE | true on the first call, because snapshot stale is the union of the four components |

Funding historical class remains `INSUFFICIENT_HISTORY` until 30 stored observations exist inside the 30-day lookback. Open-interest interval comparisons stay `INSUFFICIENT_HISTORY` until a paired earlier sample exists. The responses say so. They do not invent a history.

## Database

Alembic head on the application database is `0004_derivatives`. The revision adds the five derivatives tables and does not delete candles or study rows. Retention deletes only aged derivatives rows on a later successful collect.

The optional PostgreSQL integration test was not run (`93 passed, 5 skipped`). SQLite tests cover duplicate funding inserts and the API schema. Postgres persistence of the new tables is **IMPLEMENTED** (migration applied, one live collect wrote rows that the HTTP routes then read) and the named integration test is **not verified** in this run.

## Backend tests

```
cd backend
..\.venv\Scripts\python.exe -m pytest -q
```

Result: `93 passed, 5 skipped, 1 warning in 4.10s`.

No separate mypy run exists in this project. The extension TypeScript check is part of `npm run build`.

Verification dashboard `npm test`: 4 passed. The checklist accepts revisions `0001_initial` through `0004_derivatives`. The dashboard page was not re-opened after the migration.

## Extension

`chrome-extension` `npm test`: 6 passed.

`npm run build`: succeeded. `dist` contains `manifest.json`, `background.js`, `index.html`, and hashed assets.

Unpacked load in Chrome: **not performed**.

## Security and permissions

- REST client remains GET-only and refuses order, position, wallet, and leverage paths.
- No API key is required or stored.
- Extension permissions are `sidePanel` and `storage`, plus host access limited to `127.0.0.1:8000` and `localhost:8000`.
- The panel does not execute HTML from API bodies and does not call trading routes.
- Context states have no score and no buy or sell label.

## Performance

- Derivatives REST poll default is 60 seconds, not the socket's sub-second book rate.
- Incremental `ob_updates` are not inserted.
- Mark socket messages are not inserted on every 2-second tick.
- The panel candle request is limited to 240 rows for the selected timeframe.
- Auto refresh follows the stored interval instead of a fixed tight loop.
- Study recompute remains on its own interval and is not part of a derivatives GET.

## Feature classification

| Area | Status |
| --- | --- |
| Phase 1 ingestion, candles, health, dashboard checklist unit tests | VERIFIED WORKING |
| Phase 2 and 3 analysis API | VERIFIED WORKING. Study status was `LAGGING` at the post-restart check. |
| Phase 4 REST derivatives storage and GET routes | VERIFIED WORKING |
| Exchange history for funding, open interest, and mark | UNAVAILABLE |
| Long/short split of open interest | UNSUPPORTED |
| Live WebSocket funding message and live `ob_updates` sequence | IMPLEMENTED BUT NOT VERIFIED |
| `ob_l1` / `ob_l2` socket subscriptions | NOT IMPLEMENTED |
| Extension production build and unit tests | VERIFIED WORKING |
| Extension loaded in Chrome | IMPLEMENTED BUT NOT VERIFIED |
| Scenario distribution charts and candle indicator overlays | NOT IMPLEMENTED |
| Trading, sizing, leverage, account access | NOT IMPLEMENTED by design |

## Known limitations

Documented in `docs/PHASE_4_DATA_SOURCE_AUDIT.md`, `docs/DERIVATIVES_METHODOLOGY.md`, and `docs/PHASE_5_IMPLEMENTATION_REPORT.md`.

The extension dependency audit reported 4 findings and was left unchanged.
