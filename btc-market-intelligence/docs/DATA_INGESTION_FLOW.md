# Data ingestion flow

```text
GET /v2/products  ->  select the live BTC perpetual  ->  products
GET /v2/history/candles in chunks under 2,000 bars
        ->  normalize  ->  candles
        ->  ingestion_state
        ->  data_quality_events when a page is short, malformed, or gapped
closed 1m bars  ->  aggregate 5m, 15m, 1h, 4h, 1d
        ->  compare with native Delta bars
        ->  insert aggregate only where Delta has no bar
        ->  record mismatches, do not average them

wss://public-socket.india.delta.exchange
        ->  enable_heartbeat
        ->  one subscribe for candlestick_1m, ticker, mark_price, system_status
        ->  cst is the open; ts is the publish time
        ->  forming bar stays in memory
        ->  closed bar is upserted
        ->  a skipped minute is one quality event plus a REST fill of that hole
        ->  disconnect waits with backoff, then repeats this socket setup
```

## Backfill

`python -m app.cli backfill --days 180` floors the end to the current minute so the still-forming minute is left to the socket. The start is `end - days * 86400`. Each request covers at most `CANDLE_CHUNK_SIZE` candles (default 1,500), under Delta's 2,000 cap. If a response is still full and its oldest open is after the requested start, the older part is requested again.

On a later run the stored newest open is skipped. If the requested start is older than the stored oldest open, only that older hole is added. Commits happen per chunk, so a stopped run resumes.

## Socket

One connection. Subscriptions are recorded in memory for that connection and are not sent twice on it. A new connection subscribes again, which is required after a drop.

The forming book:

- same `cst`: replace the open bar, or keep it closed if it had already closed
- next minute: mark the previous bar closed and store both
- jump of more than one minute: store both, record the missing opens, fill them from REST
- older `cst`: record `non_monotonic` and keep the current bar

If the socket goes quiet, `close_if_due` closes the forming bar once its minute has ended.

Reconnect delay starts at 1 second, doubles, and is capped at 60 seconds, with jitter. The wait is at least 1 second and stops immediately when the process is shutting down. There is no tight reconnect loop.

## Read path

`GET /candles` returns closed bars only, oldest first, between `start` inclusive and `end` exclusive. `GET /health` reports database connectivity, socket state, the latest closed 1-minute open, freshness, and whether the recent 1-minute series has interior gaps.
