# Phase 1 verification report

Checked on 2026-11-04, with the FastAPI process and the verification dashboard running on this machine. Counts below are a snapshot from the running API at about 00:17 UTC. The public socket is still writing, so the one-minute count increases every minute.

## VERIFIED WORKING

- The backend starts with `python -m app.cli serve` and answers on `http://127.0.0.1:8000`.
- PostgreSQL is connected. `GET /health` returned `database.connected: true` and no database error.
- Alembic revision is `0001_initial`. The five tables are present: `products`, `candles`, `ingestion_state`, `websocket_events`, `data_quality_events`.
- The stored BTC product is the live perpetual: symbol `BTCUSD`, exchange product id `27`, contract type `perpetual_futures`, tick size `0.5`, contract value `0.001`, unit `BTC`, state `live`, settlement time empty. Description: "Bitcoin Perpetual futures, quoted, settled & margined in US Dollar".
- Historical candles are persisted. Snapshot counts: products `1`, candles `333947`, one-minute `259241`, five-minute `51844`, fifteen-minute `17282`, one-hour `4320`, four-hour `1080`, one-day `180`. Quality events `36`. WebSocket diagnostic events `15`. Ingestion-state rows `7`.
- `GET /candles?timeframe=1m&limit=1&order=desc` returned a stored closed candle. At the check, the latest completed open was `2026-11-04T00:16:00Z`. The dashboard labels that value "Latest completed close". A newer forming bar, when present, is labeled "Latest stored close, candle still forming". There is no ticker endpoint, and the screen does not call a stored close a live price.
- The public WebSocket was connected during this run. Subscriptions were `candlestick_1m`, `ticker`, `mark_price`, and `system_status`. Exchange system status was `live`. Freshness was about 15 seconds, and `stale` was false. This process reported `reconnect_count` 3, and the socket was connected again after those reconnects.
- `uq_candle_slot` is present on `candles`. The isolated PostgreSQL tests, which use schema `bmi_itest` and do not delete public rows, passed in this session.
- `GET /diagnostics/delta` performed one public candle read for `BTCUSD` and returned `ok: true` with 4 candles. The response contained no database URL.
- The dashboard at `http://127.0.0.1:5173/` and `http://localhost:5173/` rendered these values. Switching the explorer to `5m` showed 50 returned rows, 51844 stored rows, series start `2026-05-07T23:25:00Z`, and a chart of those returned rows. The manual Health button returned HTTP 200. While Vite was restarting, the same page showed backend FAIL and the other checks BLOCKED, with no invented prices.

## IMPLEMENTED BUT NOT VERIFIED

- Automatic REST recovery of a hole that exists before the socket process starts. The offline test `test_websocket_gap_is_recovered_from_rest` passed. In this live process, closed one-minute opens `2026-11-04T00:01:00Z` through `2026-11-04T00:09:00Z` were still absent. Health therefore stayed `degraded` with `recent_missing_candles: 9`, even though the socket itself was connected and data was not stale. A separate fill of that window did not finish: the new database connection timed out before any write. The dashboard leaves "REST gap recovery" at NOT VERIFIED.

## BLOCKED

- Closing that nine-minute hole in this session. The running server could still read and write through its existing pool. A second connection attempt timed out, so the fill was not applied.
- Local Docker and `psql` are not installed. The app is using the PostgreSQL URL already present in `backend/.env`.

## NOT IMPLEMENTED

- A ticker or live-price endpoint.
- Order, position, wallet, or leverage routes.
- Phase 2 indicators, Phase 3 predictions, Phase 4 derivatives, and the Phase 5 Chrome extension.
- Arbitrary SQL or a database password in any response.

## Tests and build

- Offline pytest, with `RUN_POSTGRES_TESTS` and `RUN_LIVE_SMOKE` unset: 33 passed, 5 skipped.
- The same suite when the PostgreSQL marker was already enabled in the shell: 37 passed, 1 skipped. The four extra passes are the isolated-schema tests. The remaining skip is the live smoke test.
- Dashboard Vitest: 3 passed. `npm run build` completed. Production output is `verification-dashboard/dist/`.
- FastAPI still emits the existing Starlette warning about `httpx` and `httpx2`. That warning does not change the API result.

## Dashboard checklist against this run

| Check | State on the live page | Why |
| --- | --- | --- |
| Backend startup | PASS | `/health` answered. The body status was `degraded`, which the System card shows separately. |
| PostgreSQL connectivity | PASS | `database.connected` was true. |
| Database migrations | PASS | Revision `0001_initial` and all five tables. |
| Product discovery | PASS | `BTCUSD` perpetual was returned. |
| Historical candle persistence | PASS | 259241 one-minute rows at the snapshot. |
| Live WebSocket | PASS | The process reported the socket connected. |
| Candle persistence | PASS | 333947 candle rows. |
| Duplicate prevention | PASS | `uq_candle_slot` reported present. |
| REST gap recovery | NOT VERIFIED | The screen does not replay a gap, and the nine missing minutes were still missing. |
| API responses | PASS | Health, product, candles, and data status returned HTTP 200. |
