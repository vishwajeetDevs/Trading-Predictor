# Database schema

PostgreSQL is the application database. Alembic revision `0001_initial` creates the market-data tables below. Revision `0002_analysis` adds the study tables and does not rebuild the candle store. Tests also create the same SQLAlchemy models on SQLite; that file is not the application store.

Unique candle slot: `(product_id, timeframe, open_time)`.

## products

One row per discovered contract. `exchange_product_id` and `symbol` are unique. `raw_metadata` is the public catalogue object with leverage, margin, wallet, and position keys removed. `resolved_at` is the first time this process stored the contract. `updated_at` moves on each refresh.

## candles

OHLCV for historical REST loads and closed socket bars. Prices and volume are `NUMERIC(38, 12)`. `open_time` and `received_at` are `timestamptz`. `exchange_time_raw` and `timestamp_unit` keep the exchange clock separate from `received_at`.

`source` is `delta_rest`, `delta_ws`, or `aggregated_1m`. An aggregated row is inserted only when Delta has no native row for that slot. A later aggregate does not overwrite a native row.

## ingestion_state

One row per `(product_id, timeframe, stream)`. `stream` is `backfill` or `websocket`. `oldest_open_time` moves only earlier. `newest_open_time` moves only later. The next backfill reads this row and requests only the missing older hole and the range after the newest stored open.

## data_quality_events

One row per quality finding. A gap is one row with a count, not one row per missing minute. `detail` is JSON.

## websocket_events

Connection, disconnect, and system-status notes. Market ticks are not written here. Rows older than 7 days are deleted when a new event is recorded.

## study_runs, condition_matches, horizon_stats, analysis_snapshots

Added by `0002_analysis`. A study run records the definition hash and the latest closed 1-minute open it used. Condition matches are one row per raw historical event. Horizon stats are the aggregate for one timeframe, condition, and horizon. Revision `0003_sampling` adds nullable `raw_match_count`, `independent_count`, `clustered_out`, and `comparison_status` on `horizon_stats`. Older rows keep their values; those columns stay null on rows written before the revision. Analysis snapshots store the compact summary written by the study command. GET requests do not insert a snapshot.

## Derivatives observations

Added by `0004_derivatives`. Existing candle and study rows are not rewritten.

- `funding_rate_observations` — one rate per `(product_id, observed_at, source)`. `funding_rate` is `NUMERIC(38, 12)`. `next_funding_at` is set only for WebSocket `funding_rate` messages. REST ticker rows leave it null.
- `open_interest_observations` — contracts, value, and the exchange 6-hour change copied from the ticker. Unique on `(product_id, observed_at, source)`.
- `mark_price_observations` — mark, last traded price, and index (`spot_price`) at one observation time.
- `orderbook_snapshots` — best bid and ask, locally calculated spread and depth, level count, and the REST payload. High-frequency `ob_updates` are not written here.
- `derivatives_data_quality_events` — sequence gaps, checksum mismatches, and book errors. Rows older than 14 days are deleted when a new event is recorded.

Funding and open interest older than `derivatives_retention_days` (90) are deleted on a successful collect. Mark rows older than `mark_retention_days` (14) and order-book rows older than `orderbook_retention_hours` (48) follow the same collect. A duplicate insert uses a nested transaction so one conflict does not roll back the rest of the collect.

## Migration

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\backend"
..\.venv\Scripts\python.exe -m app.cli migrate
```

This runs `alembic upgrade head` using `DATABASE_URL`. On 2026-11-04 the configured PostgreSQL database was upgraded from `0001_initial` through `0004_derivatives`. `0003_sampling` and `0004_derivatives` are additive and do not rewrite candle or study rows. Docker is not installed on this laptop, so the migration was not executed through `docker compose`. Offline tests create the same tables with `create_all` on SQLite.
