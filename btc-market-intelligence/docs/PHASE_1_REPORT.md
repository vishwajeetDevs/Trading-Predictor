# Phase 1 report

Date: 2026-11-04 (UTC work on 2026-11-03). Scope stayed on public BTC market data. No indicator, extension, or trading code was added. `day-trader-skill` was not modified.

## 1. Files created

New project: `btc-market-intelligence/`.

- `README.md`, `.gitignore`, `docker-compose.yml`
- `backend/requirements.txt`, `backend/.env.example`, `backend/pytest.ini`, `backend/alembic.ini`
- `backend/app/` — FastAPI app, settings, time helpers, Delta REST and WebSocket clients, product resolver, normalizer, candle builder, reconciliation, SQLAlchemy models, repository, backfill, socket ingestion, health
- `backend/app/cli.py` — `migrate`, `backfill`, `ws`, `serve`
- `backend/migrations/` — Alembic revision `0001_initial`
- `backend/tests/` — offline tests plus `test_live_smoke.py`
- `backend/tests/fixtures/` — sanitized public responses: `product_btcusd.json`, `candles_1m_recent.json`, `candles_5m_recent.json`, `ws_public_messages.json`
- `docs/DELTA_API_VERIFICATION.md`, `DATA_DICTIONARY.md`, `DATABASE_SCHEMA.md`, `DATA_INGESTION_FLOW.md`, this report

`backend/.env` is local configuration and is gitignored. Its `DATABASE_URL` was rewritten from a `psql '...'` shell snippet into a `postgresql+psycopg://` URL so SQLAlchemy can open it. The secret itself was not copied into source or docs.

## 2. API endpoints verified

Verified working, by real calls:

- `GET https://api.india.delta.exchange/v2/products` with `contract_types=perpetual_futures` and `states=live` (cursor pages)
- `GET /v2/products/BTCUSD`
- `GET /v2/history/candles` for `1m`, `5m`, `15m`, `1h`, `4h`, `1d`
- `wss://public-socket.india.delta.exchange` with `enable_heartbeat` and the four public channels below

The REST client refuses any other path, including orders, positions, wallets, and leverage.

## 3. BTC contract

Discovered from the live catalogue, not from a hardcoded id:

| Field | Value |
| --- | --- |
| symbol | BTCUSD |
| exchange id | 27 |
| contract_type | perpetual_futures |
| state | live |
| trading_status | operational |
| underlying / quoting / settling | BTC / USD / USD |
| tick_size | 0.5 |
| contract_value | 0.001 |
| contract_unit_currency | BTC |
| notional_type | vanilla |
| spot index | .DEXBTUSD |
| settlement_time | null |

`GET /products/btc` does not return leverage or margin. Those keys are also stripped before `raw_metadata` is saved.

## 4. Historical lookback obtained

Verified working and stored in PostgreSQL:

| Timeframe | Rows | Oldest open (UTC) | Newest open (UTC) |
| --- | --- | --- | --- |
| 1m | 259,219 | 2026-05-07 23:28 | 2026-11-03 23:46 |
| 5m | 51,844 | 2026-05-07 23:25 | 2026-11-03 23:40 |
| 15m | 17,282 | 2026-05-07 23:15 | 2026-11-03 23:30 |
| 1h | 4,320 | 2026-05-07 23:00 | 2026-11-03 22:00 |
| 4h | 1,080 | 2026-05-07 20:00 | 2026-11-03 16:00 |
| 1d | 180 | 2026-05-07 00:00 | 2026-11-02 00:00 |

The 180-day backfill wrote 259,200 one-minute bars through `2026-11-03T23:27:00Z` in 173 requests. A later one-request catch-up extended that series through `23:46:00Z` (259,219 bars). The count matches every minute in the window. The 23:00 hour, the 20:00 four-hour bar, and the 2026-11-03 daily bar were still forming, so they are not in the closed store.

Availability beyond this window, checked with small reads on 2026-11-03:

- 1-minute data on 2024-02-02 is empty. 2024-02-03 00:00 UTC is present.
- Daily data from a 2019-01-01 request begins on 2023-12-29. It does not cover 2019.

A longer 1-minute backfill will stop where Delta stops returning bars. Those minutes are not invented.

## 5. Supported timeframes

In use: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`.

Documented by Delta and not ingested in this phase: `3m`, `30m`, `2h`, `6h`, `1w`.

Deprecated by Delta: `7d`, `2w`, `30d`.

## 6. WebSocket channels verified

Verified working on `wss://public-socket.india.delta.exchange`:

- `candlestick_1m` for `BTCUSD`. `cst` is the open in microseconds. `ts` is the publish time, not the open.
- `ticker` for `BTCUSD`. Parsed, not stored as ticks.
- `mark_price` for `MARK:BTCUSD`. Parsed, not stored as a traded candle. `MARK:` candlesticks are rejected.
- `system_status`. A live run reported `live`.
- `heartbeat` after `enable_heartbeat`.

A 50-second run on 2026-11-03 23:45 UTC subscribed to all four channels, received a heartbeat, and stored:

- `2026-11-03T23:44:00Z` as a closed `delta_ws` bar
- `2026-11-03T23:45:00Z` as the still-forming bar (`is_closed` false)

## 7. Data freshness

At the end of that socket run the last message was `2026-11-03T23:45:36Z`, the last heartbeat was `2026-11-03T23:45:32Z`, and the newest closed 1-minute bar was `2026-11-03T23:44:00Z`. That is inside the 180-second stale limit. Freshness is measured from when the minute closed (`open + 60s`), not from the open.

## 8. Database schema

Revision `0001_initial` was applied to the configured PostgreSQL database. Tables: `products`, `candles`, `ingestion_state`, `data_quality_events`, `websocket_events`. Candle uniqueness is `(product_id, timeframe, open_time)`.

Docker and `psql` are not installed on this laptop, so `docker compose` was not run. The same revision was applied to SQLite inside the offline Alembic test. Details are in `docs/DATABASE_SCHEMA.md`.

## 9. Test results

Verified working:

- Offline pytest: 30 passed. The live test is skipped unless `RUN_LIVE_SMOKE=1`.
- Live smoke: passed. It resolved `BTCUSD`, fetched recent 1-minute candles, and normalized a live `candlestick_1m` (`timestamp_unit` `us`).
- A later full pytest run with the live flag set passed 31 tests, including that smoke test.
- One-day backfill, then the 180-day backfill above, then the 50-second socket run.

FastAPI's TestClient emits a Starlette deprecation warning about `httpx`. The tests still pass. `httpx2` was not added.

## 10. Known limitations

- Python on this machine sometimes fails DNS for the database host with `getaddrinfo` error 11001, then succeeds on retry. `Resolve-DnsName` can work while `psycopg` cannot. If `serve` or `backfill` fails that way, run the command again.
- Sixteen native-versus-aggregate mismatches are stored as `reconciliation_mismatch`. The exchange bar is kept. They are real differences between Delta's higher timeframe and the 1-minute bars, not filled values.
- Eighteen `incomplete_aggregate` events are partial buckets at the edges of the requested window.
- REST sometimes sends OHLC as scientific notation. The stored value is the exact JSON token (`7.78e4` becomes `77800`). Digits Delta did not send are gone.
- Rate-limit headers were not present on the successful 200 responses. 429 handling is implemented and covered offline, and was not triggered by this backfill.
- Ticker and mark-price messages are not kept as a tick store.
- A forming 1-minute bar is written and then revised. `GET /candles` returns closed bars only.

## 11. Unresolved

- Local Docker Postgres was not started, because Docker is not installed. The hosted database in `backend/.env` is the one that was migrated and filled.
- 1-minute history before 2024-02-03 is not available from this API. Daily history starts earlier, on 2023-12-29, and those extra days were not expanded into fake 1-minute bars.

## 12. Recommended Phase 2

Build read-only studies on the stored candles: RSI, EMA, MACD, VWAP, Bollinger, ATR, and the higher-timeframe context the extension will show. Keep using this database and these four HTTP routes. Do not add orders, leverage, position size, or a trading key.

## Status split

| Item | Status |
| --- | --- |
| Dynamic BTC perpetual resolution | Verified working |
| 180-day candle store | Verified working |
| Public WebSocket connect, normalize, store closed bar | Verified working |
| Gap fill from REST after a socket hole | Implemented, covered offline, not forced on the live socket |
| Reconnect with backoff | Implemented, covered offline |
| Alembic on PostgreSQL | Verified working |
| Alembic via Docker Compose | Not run; Docker is absent |
| Read-only FastAPI routes | Implemented, covered offline against SQLite |
| Indicators, extension, orders | Not implemented |
| 1-minute history before 2024-02-03 | Unsupported by the data the API returned |
| Resolutions `3m`, `30m`, `2h`, `6h`, `1w` | Supported by Delta, not ingested |
