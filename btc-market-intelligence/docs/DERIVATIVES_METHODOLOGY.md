# Derivatives methodology

This module describes stored public observations. It does not recommend a trade, a size, or a direction.

Every number in an API response is either copied from Delta Exchange India or calculated from stored copies. The response names which is which.

## Clocks

- REST observation time is the ticker or book `timestamp` / `last_updated_at` converted from microseconds to UTC.
- The ticker ISO `time` field is not the observation clock.
- `received_at` is when this process stored the row.
- Freshness is `now - observed_at` in seconds.
- `stale` is true when that lag is greater than `STALE_AFTER_SECONDS`.
- The snapshot `stale` flag is true when any of funding, open interest, mark, or the book is stale. A fresh funding row can sit next to a stale book.

## Funding

Stored from:

- REST `GET /v2/tickers/BTCUSD` field `funding_rate`. Interval seconds come from the stored product spec `rate_exchange_interval` (28800). `next_funding_at` is null. Source identifies the REST ticker.
- WebSocket `funding_rate` when a message arrives. That row can include `fi` and `nfr`.

Sign, using `funding_near_zero` default `0.0001`:

- `positive` when the rate is above that band.
- `negative` when the rate is below the negative band.
- `near_zero` when the absolute rate is at or inside the band.

Historical class uses only stored observations inside `funding_lookback_days` (30). It requires at least `funding_min_sample` (30) observations. With fewer rows the class is `INSUFFICIENT_HISTORY`.

With enough rows, the 90th and 10th percentiles use the same linear quantile helper as the study module:

- `historically_elevated` when the current rate is at or above the 90th percentile.
- `historically_depressed` when the current rate is at or below the 10th percentile.
- `within_historical_range` otherwise.

A positive rate is not a forecast that price will fall. A negative rate is not a forecast that price will rise. The API note says this on every classified row.

## Open interest

Latest contracts, value, value symbol, USD value, and `oi_change_usd_6h` are copied from the ticker. The 6-hour figure is labeled as an exchange field.

Changes over 1 hour, 4 hours, and 24 hours compare the latest stored sample with an earlier stored sample. The earlier sample must fall within twice the poll interval of the target lookback. If either sample or the matching 1-minute candle close change is missing, the interval state is `INSUFFICIENT_HISTORY`.

Percent change is `(current - previous) / previous`. A previous value of zero yields no percent.

Descriptive states, only when both changes exist:

- `price_rising_oi_rising`
- `price_rising_oi_falling`
- `price_falling_oi_rising`
- `price_falling_oi_falling`
- plus `unchanged` on either side when that change is zero

These states do not say whether the outstanding contracts are long or short. There is no exchange history endpoint, so a lookback that this process has not sampled cannot be filled in.

## Mark, last, and index

- Mark price: ticker `mark_price`.
- Last price: newest public trade. Ticker `close` is used only when the trade list is empty.
- Index price: ticker `spot_price`. `GET /v2/indices` is metadata and is not used as the live price.

Local calculations:

- `mark_last_difference = mark - last`
- `mark_last_percent = (mark - last) / last` when last is not zero
- the same pair against the index when the index is present

The snapshot calls a mark/last divergence when the absolute percent is at least `mark_divergence_percent` (default `0.001`, 10 basis points). The live sample on 2026-11-04 was far inside that band.

Mark price is not an executable market price.

## Order book

Exchange fields from `GET /v2/l2orderbook/BTCUSD`: best bid, best ask, level price, level size, cumulative level depth, and snapshot time.

Local fields:

- spread = best ask − best bid
- spread percent = spread / best ask when the ask is not zero
- side depth = the cumulative depth on the deepest returned level, otherwise the sum of sizes
- imbalance = `(bid_depth - ask_depth) / (bid_depth + ask_depth)` when the total is not zero
- concentration = top-of-book size / side depth
- short-term change = the difference between two stored REST snapshots, labeled as a local comparison

Empty or one-sided books withhold spread and imbalance and add a warning.

An imbalance at or beyond `book_imbalance_threshold` (default `0.2`) produces the state `order_book_imbalance`. That state is not a directional signal.

WebSocket `ob_updates` keep an in-memory book:

- a snapshot replaces the book and records the sequence
- an update must follow the previous sequence by 1
- size 0 deletes a level
- checksum is CRC32 of the first 10 asks (ascending) and first 10 bids (descending), `price:size` joined by commas, asks then a pipe then bids
- a gap, error, or checksum mismatch clears the book, records a quality event, and resubscribes

Those incremental updates are not written as snapshots. The stored book is the REST poll, so the database does not keep every sub-second update. Retention is `orderbook_retention_hours` (48).

## Context states

`context_states` returns a list. There is no score and no weight.

| State | Rule |
| --- | --- |
| `derivatives_data_unavailable` | No component has `availability` `AVAILABLE`. |
| `funding_positive`, `funding_negative`, `funding_near_zero` | Funding sign above. |
| `funding_elevated`, `funding_depressed` | Historical class above. |
| `insufficient_historical_data` | Funding sample below the minimum, or the 1-hour open-interest comparison lacks a pair. |
| `open_interest_expanding` or `open_interest_contracting` | 1-hour stored open interest rose or fell. |
| `price_oi_divergence` | 1-hour price and open interest moved in opposite directions. |
| `mark_last_divergence` | Absolute mark/last percent at or beyond the threshold. |
| `order_book_imbalance` | Absolute imbalance at or beyond the threshold. |
| `no_threshold_state` | Data exists and none of the rules above fired. |

Duplicate names are dropped. Each state includes the rule text in `detail`.

## Retention and duplicates

Unique key for funding, open interest, and mark: `(product_id, observed_at, source)`. Order book: `(product_id, snapshot_at, source)`.

A duplicate insert is caught inside a nested transaction. The rest of that collect still commits.

On a successful collect, rows older than the retention window for that table are deleted. Candles and study rows are not deleted by this path.
