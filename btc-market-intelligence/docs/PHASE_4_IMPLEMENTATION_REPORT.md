# Phase 4 implementation report

Date: 2026-11-04.

Scope: public BTC derivatives observations for Delta Exchange India. No orders, account access, or trade instructions.

## What was added

- `backend/app/derivatives/normalize.py` parses ticker, trades, L2 book, funding and mark socket messages, and calculates spread, imbalance, mark/last difference, and funding class.
- `backend/app/derivatives/context.py` returns a list of descriptive states. There is no score.
- `backend/app/services/derivatives_service.py` stores polls, applies retention, and builds the read-only views.
- `backend/app/api/routes/derivatives.py` exposes the five GET routes.
- Alembic `0004_derivatives` creates five tables. It does not alter candle or study rows.
- The public socket subscribes to `funding_rate` and `ob_updates` in addition to the Phase 1 channels. Incremental book updates stay in memory. The persisted book is the REST snapshot.
- `serve` runs a derivatives poll when `ENABLE_DERIVATIVES` is true (the default). Offline tests set it false so `TestClient` does not start that thread.

Methodology and the source audit are in `docs/DERIVATIVES_METHODOLOGY.md` and `docs/PHASE_4_DATA_SOURCE_AUDIT.md`.

## Migration

`alembic upgrade` moved the application database from `0003_sampling` to `0004_derivatives` on 2026-11-04. Tables:

- `funding_rate_observations`
- `open_interest_observations`
- `mark_price_observations`
- `orderbook_snapshots`
- `derivatives_data_quality_events`

Unique keys are `(product_id, observed_at or snapshot_at, source)`. Duplicate inserts use a nested transaction.

## API contract

Each route returns `read_only`, `product`, `source`, `timestamp`, `freshness_seconds`, `stale`, `availability`, `values`, and `warnings`.

| Route | When empty |
| --- | --- |
| `GET /derivatives/snapshot` | `availability` `NOT_COLLECTED`, empty component values, unsupported notes |
| `GET /derivatives/funding` | warning that no funding row is stored |
| `GET /derivatives/open-interest` | warning that no open-interest row is stored |
| `GET /derivatives/mark-price` | warning that no mark row is stored |
| `GET /derivatives/orderbook` | warning that no book snapshot is stored |

Unavailable numbers are null. The offline API test rejects a body that contains the substring `0.000` on the empty response.

## Live HTTP check after the process restart

The API process was restarted so this code is the one listening on `http://127.0.0.1:8000`.

| Request | Result |
| --- | --- |
| `GET /health` | 200, `status` ok, database connected, websocket connected, candle `stale` false. An earlier call in the same minute saw the socket still connecting (`websocket.connected` false) and then the log recorded the India socket connect. |
| `GET /derivatives/funding` | 200, `AVAILABLE`, not stale, rate `0.01`, source REST ticker |
| `GET /derivatives/open-interest` | 200, `AVAILABLE`, not stale, source `delta_rest_ticker` |
| `GET /derivatives/mark-price` | 200, `AVAILABLE`, not stale, source `delta_rest_ticker_and_trades` |
| `GET /derivatives/orderbook` | 200, `AVAILABLE`, not stale, source `delta_rest_l2orderbook` |
| `GET /derivatives/snapshot` | 200, `AVAILABLE`. At the first call `stale` was true because at least one component was outside `STALE_AFTER_SECONDS`. States were `funding_positive` and `insufficient_historical_data`. |
| `GET /analysis/snapshot` | 200, definition `phase3-v2`, study status `LAGGING` at that check |

The first stored funding sample is still below the 30-observation minimum, so the historical class stays `INSUFFICIENT_HISTORY`. That is the expected result, not a filled history.

One collect written before the restart stored funding, open interest, mark, and a 15-level book for `BTCUSD`. The poller on the new process requested ticker, L2, and trades again (HTTP 200 in the server log).

## Tests

Offline, from `backend`, on 2026-11-04:

```
93 passed, 5 skipped, 1 warning in 4.10s
```

The five skips are the live smoke tests and the PostgreSQL integration test. They are skipped unless `RUN_LIVE_SMOKE` or `RUN_POSTGRES_TESTS` is set. Default pytest does not call the exchange.

Derivatives coverage in `backend/tests/test_derivatives.py` includes funding normalization, history parsing, open-interest parsing and percent change, mark/last difference, spread, imbalance, empty book, invalid payloads, stale responses, socket resubscribe handling, duplicate funding inserts, and the API envelope. Persistence in that file uses SQLite. The optional Postgres test checks Alembic revision membership, including `0004_derivatives`. It was not executed in this run, and it does not assert the five new tables by name.

Dashboard checklist unit tests were re-run after the revision allow-list added `0004_derivatives`: 4 passed. The open verification page was not re-screenshotted after the migration.

## Feature classification

| Feature | Status |
| --- | --- |
| REST funding, open interest, mark, last trade, index `spot_price`, L2 book | VERIFIED WORKING |
| Product funding interval from stored specs | VERIFIED WORKING |
| Local funding sign, mark/last math, spread, imbalance, context list | VERIFIED WORKING (offline tests and live HTTP) |
| Historical funding class and multi-hour open-interest change | VERIFIED WORKING as `INSUFFICIENT_HISTORY` until 30 funding samples and paired open-interest samples exist |
| Exchange funding, open-interest, and mark history endpoints | UNAVAILABLE |
| Long versus short open interest | UNSUPPORTED |
| WebSocket `funding_rate` persistence (`nfr`, interval) | IMPLEMENTED BUT NOT VERIFIED against a captured live message |
| WebSocket `ob_updates` sequence, checksum, and resubscribe | IMPLEMENTED BUT NOT VERIFIED against a captured live sequence |
| `ob_l1` and `ob_l2` socket subscriptions | NOT IMPLEMENTED. REST L2 is the stored book. |
| PostgreSQL integration test of the new tables | IMPLEMENTED schema, migration applied. The optional integration test was not run. |
| Weighted sentiment score or buy/sell output | NOT IMPLEMENTED by design |

## Limitations

- History before this process started collecting does not exist in the database.
- REST rows have no next-funding timestamp.
- Mark socket ticks at about 2 seconds are not all stored. The stored series is the poll interval.
- Order-book change is the difference of two stored REST snapshots, not an exchange delta.
- `oi_change_usd_6h` is copied from the ticker and is not recomputed.
