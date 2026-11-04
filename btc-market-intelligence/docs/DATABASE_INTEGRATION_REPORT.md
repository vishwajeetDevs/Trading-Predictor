# Database integration report

Date: 2026-11-04. This pass did not rebuild Phase 1. It checked the existing PostgreSQL path, kept the stored market data, and verified reads and writes against that database.

`.env` was not overwritten. Credentials are loaded from `DATABASE_URL` and are not printed by the health check or by `python -m app.cli` connection errors.

## 1. Connection verification

VERIFIED WORKING.

`SELECT 1` succeeded. The server reported PostgreSQL 18.6. A second connection succeeded after `engine.dispose()`, which is the pool reconnect check.

Windows DNS for this host is intermittent (`getaddrinfo` error 11001). The application engine retries that failure up to three times, with waits of 1, 2, and 4 seconds. Alembic does the same. A bad URL still fails immediately and the error text has the userinfo replaced with `postgresql://***`.

## 2. Configuration

| Setting | Value |
| --- | --- |
| Source | `backend/.env`, field `DATABASE_URL` |
| Driver | `postgresql+psycopg` |
| Pool | size 5, overflow 5, timeout 30s, recycle 300s, `pool_pre_ping` |
| Connect timeout | 10 seconds |
| Example file | `backend/.env.example` has placeholders only |
| Git | `.env` is listed in `.gitignore` |

No second copy of the URL is hardcoded in the application modules. SQLite remains only inside the offline unit tests. It is not the application database.

## 3. Tables

Reused. No second model and no second migration were added. The public schema already has:

- `products`
- `candles`
- `ingestion_state`
- `websocket_events`
- `data_quality_events`
- `alembic_version`

`candles` is unique on `(product_id, timeframe, open_time)` (`uq_candle_slot`). `products.exchange_product_id` and `products.symbol` are unique. `ingestion_state` is unique on `(product_id, timeframe, stream)`. Prices and volume are `NUMERIC(38, 12)`. Timestamps are `timestamptz`.

## 4. Migrations

VERIFIED WORKING.

Revision `0001_initial` is the current Alembic head. `python -m app.cli migrate` connected and reported the database already at head. It did not create duplicate tables.

Integration tests ran the same revision inside schema `bmi_itest`, then dropped only that schema. `public` was not dropped. After the tests, `bmi_itest` was gone and `public.candles` still held the development rows.

## 5. Product records

VERIFIED WORKING. One row:

| id | exchange id | symbol | type | state | trading | assets | tick | contract value |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 27 | BTCUSD | perpetual_futures | live | operational | BTC / USD / USD | 0.5 | 0.001 |

A second upsert of the same catalogue object keeps that row. A duplicate primary insert in the test schema raises `IntegrityError` and is rolled back.

## 6. Historical candles

VERIFIED WORKING. Counts read from PostgreSQL after the catch-up below:

| Timeframe | Rows | Newest open (UTC) |
| --- | --- | --- |
| 1m | 259,234 | 2026-11-04 00:01 |
| 5m | 51,844 | 2026-11-03 23:40 |
| 15m | 17,282 | 2026-11-03 23:30 |
| 1h | 4,320 | 2026-11-03 22:00 |
| 4h | 1,080 | 2026-11-03 16:00 |
| 1d | 180 | 2026-11-02 00:00 |

Total candle rows: 333,940. Oldest 1-minute open: `2026-05-07T23:28:00Z`. The 1-minute series is continuous across that span. Inserts are batched. A repeated upsert of the same bar does not add a row.

`ingestion_state` has 7 rows (six backfill streams and one websocket stream), status `ok`.

## 7. Live candle persistence

VERIFIED WORKING on 2026-11-04 00:01 UTC.

A 40-second public socket session subscribed to `candlestick_1m`, `ticker`, `mark_price`, and `system_status`. Heartbeats arrived. `system_status` was `live`. The run stored:

- `2026-11-04T00:00:00Z`, source `delta_ws`, closed
- `2026-11-04T00:01:00Z`, source `delta_ws`, still forming

`websocket_events` count is 4. The socket does not invent the minutes it never saw. Thirteen missing minutes just before `00:00` were then loaded with one REST request (`stored: 13`). Those rows are `delta_rest`. No synthetic bars were written.

## 8. Duplicate prevention

VERIFIED WORKING.

On the real database, `uq_candle_slot` is present in `pg_constraint`. In `bmi_itest`, inserting a second candle for the same product, timeframe, and open time raised `IntegrityError`, and the schema still contained one candle after rollback. The public candle count was the same before and after that test.

## 9. Test results

| Suite | Result |
| --- | --- |
| Offline pytest | 31 passed, 5 skipped |
| `RUN_POSTGRES_TESTS=1` pytest `tests/test_postgres_integration.py` | 4 passed |

The PostgreSQL file checks connectivity, the Alembic revision, the unique constraint, product and candle upserts, duplicate rejection, pool reconnect, credential redaction, and FastAPI `GET /health`, `/products/btc`, `/candles`, and `/data/status` against this database. `/products/btc` returned `BTCUSD` and exchange id 27. `/candles?timeframe=1m&limit=3` returned 3 stored bars.

Those tests do not run on a normal `pytest` invocation, so an offline run cannot touch this database.

## 10. Remaining issues

- Docker is not installed, so this was not repeated against a local `docker compose` Postgres. The database in `backend/.env` is the one that was verified.
- DNS lookups for the database host still fail on some attempts. Retries cover the short failures. A longer outage still stops migrate, backfill, and serve.
- A socket process that has not yet seen a candle does not know about minutes that closed before it started. Those minutes stay missing until a REST backfill or gap fill runs. The 40-second run showed that, and the following REST fill closed the hole.
- The 23:00 hour, the 20:00 four-hour bar, and the 2026-11-04 daily bar were still forming at the last check, so they are not stored as closed bars.
- Sixteen native-versus-aggregate mismatches from Phase 1 remain in `data_quality_events` (36 quality rows total). Native bars were kept.

## 11. Start and verify on Windows PowerShell

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\backend"
..\.venv\Scripts\python.exe -m app.cli migrate
..\.venv\Scripts\python.exe -m app.cli serve
```

`migrate` is already applied. `serve` listens on `http://127.0.0.1:8000` and opens the public WebSocket when `ENABLE_WEBSOCKET=true`.

Second window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/products/btc
Invoke-RestMethod "http://127.0.0.1:8000/candles?timeframe=1m&limit=5"
Invoke-RestMethod http://127.0.0.1:8000/data/status
```

Expect `database.connected` true, symbol `BTCUSD`, and candle rows. After the next minute closes, `websocket.connected` is true. If the first attempt says the database host could not be resolved, run the same command again.

Tests:

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\backend"
..\.venv\Scripts\python.exe -m pytest
$env:RUN_POSTGRES_TESTS = "1"
..\.venv\Scripts\python.exe -m pytest .\tests\test_postgres_integration.py
```

## Status

| Feature | Status |
| --- | --- |
| Load `DATABASE_URL` from `.env` | VERIFIED WORKING |
| PostgreSQL connection and `SELECT 1` | VERIFIED WORKING |
| Pool, pre-ping, reconnect after dispose | VERIFIED WORKING |
| Alembic revision `0001_initial` on the configured database | VERIFIED WORKING |
| Product, candle, ingestion, socket, and quality tables | VERIFIED WORKING |
| Historical candle rows stored and readable | VERIFIED WORKING |
| Idempotent upsert and unique candle slot | VERIFIED WORKING |
| Live WebSocket candle insert | VERIFIED WORKING |
| FastAPI reads of those rows | VERIFIED WORKING |
| Isolated schema tests that leave `public` data in place | VERIFIED WORKING |
| Local Docker Postgres | NOT IMPLEMENTED here; Docker is not installed |
| Orders, leverage, indicators | NOT IMPLEMENTED |
