# Delta API verification

Checked against the public India endpoints on 2026-11-03 UTC (local date 2026-11-04). Documentation source: [https://docs.delta.exchange/](https://docs.delta.exchange/). No API key was sent.

## Endpoints that were called

| Call | Result |
| --- | --- |
| `GET https://api.india.delta.exchange/v2/products?contract_types=perpetual_futures&states=live&page_size=100` | HTTP 200. Cursor pagination via `meta.after`. About 250 live perpetuals across 3 pages. `BTCUSD` is not on page 1. |
| `GET /v2/products/BTCUSD` | HTTP 200. Saved in `backend/tests/fixtures/product_btcusd.json`. |
| `GET /v2/history/candles` for `1m`, `5m`, `15m`, `1h`, `4h`, `1d` | HTTP 200. Recent windows return candles. Bodies for recent `1m` and `5m` are in `backend/tests/fixtures/`. |
| `wss://public-socket.india.delta.exchange` | Connected. Subscribe acknowledgement plus `heartbeat`, `system_status`, `mark_price`, `ticker`, and `candlestick_1m` were received. Sample saved in `backend/tests/fixtures/ws_public_messages.json`. |

The legacy socket host `wss://socket.india.delta.exchange` was not used. The REST client only allows `/v2/products` and `/v2/history/candles`.

A `User-Agent` header is sent on every request. Requests are spaced by `REQUEST_MIN_INTERVAL_SECONDS` (0.25s).

## Product

The resolver pages `contract_types=perpetual_futures` and `states=live`. It keeps a contract only when all of these are true:

- `contract_type` is `perpetual_futures`
- `state` is `live`
- `settlement_time` is empty
- the symbol is not an option (`C-` / `P-`) and is not a spot-style symbol containing `_`
- `underlying_asset.symbol` is `BTC`
- `contract_unit_currency` is `BTC` or absent

On 2026-11-03 the only match was:

| Field | Value |
| --- | --- |
| symbol | BTCUSD |
| id | 27 |
| contract_type | perpetual_futures |
| state | live |
| trading_status | operational |
| underlying / quoting / settling | BTC / USD / USD |
| tick_size | `"0.5"` (string) |
| contract_value | `"0.001"` |
| contract_unit_currency | BTC |
| notional_type | vanilla |
| spot index | `.DEXBTUSD` |
| settlement_time | null |
| description | Bitcoin Perpetual futures, quoted, settled & margined in US Dollar |

The id is stored after discovery. It is not a constant in the resolver. If more than one live USD BTC perpetual appears, resolution fails instead of guessing.

## Candles

Documented resolutions include `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `1d`, and `1w`. `7d`, `2w`, and `30d` are deprecated. This phase requests only `1m`, `5m`, `15m`, `1h`, `4h`, and `1d`.

Observed response shape:

```json
{"success": true, "result": [{"time": 1793747280, "open": 77802.0, "high": 77823.0, "low": 77792.0, "close": 77792.0, "volume": 463.0}]}
```

Verified details:

- `result` is newest first. The loader sorts to oldest first.
- `time` is unix seconds of the candle open, aligned to the resolution in UTC.
- `end` is inclusive. A request whose `end` equals a candle open includes that candle. The backfill therefore sends `end - 1` for a half-open window.
- A window larger than 2,000 candles returns the newest 2,000 and omits the older part. The loader treats a full page whose oldest open is still after the requested start as truncated and requests the older remainder. It does not drop those minutes.
- Some OHLC numbers arrive as JSON scientific notation (`7.78e4`, `3.3e3`). They are parsed with `parse_float=Decimal`, so `7.78e4` is exactly `77800` and `3.3e3` is exactly `3300`. Precision the API already rounded away cannot be recovered.
- One UTC day of 1-minute data is 1,441 rows when `end` is the next midnight, because that open is included. The backfill uses a half-open range, so a 24-hour window stores 1,440 closes.

History bounds checked with small reads:

- `1m` for 2024-02-02 00:00–00:02 UTC returned 0 rows. The same two-minute request on 2024-02-03 returned 3 rows, oldest open `2024-02-03T00:00:00Z` (`1706918400`).
- `1d` from 2019-01-01 through 2024-01-01 returned 4 rows, oldest `2023-12-29T00:00:00Z`. Daily history does not extend back through 2019. The response was not cut at 2,000.
- The 180-day 1-minute store below starts on 2026-05-07, which is inside the available 1-minute history.
- `15m`, `1h`, and `4h` opens in the stored series are aligned to UTC. `4h` opens include 20:00 and 16:00 UTC.

The 1-minute series is the canonical series. Higher timeframes are also downloaded from Delta and compared with candles built from closed 1-minute bars. A higher bar is built only when every 1-minute slot in the bucket is present. Partial buckets at the edges are skipped and counted, not filled in.

On 2026-11-03 the 180-day backfill stored a continuous 1-minute series from `2026-05-07T23:28:00Z` through `2026-11-03T23:27:00Z` (259,200 bars). A one-request catch-up then extended it through `2026-11-03T23:46:00Z` (259,219 bars). Native higher-timeframe counts after that catch-up were 51,844 (`5m`), 17,282 (`15m`), 4,320 (`1h`), 1,080 (`4h`), and 180 (`1d`). Sixteen aggregate comparisons disagreed with Delta's own bar, all clustered on four opens (`2026-05-23T20:45Z`, `2026-06-28T19:15Z` / `19:20Z`, `2026-08-29T20:30Z` / `20:35Z`, `2026-10-17T20:15Z` / `20:40Z`). The native bar was kept. One example: the 5-minute bar at `2026-05-23T20:45:00Z` has exchange open `76799` while the 1-minute aggregate open is `75931`. Eighteen `incomplete_aggregate` events are the partial buckets at the ends of the requested window.

## WebSocket

Subscribe message:

```json
{"type": "subscribe", "payload": {"channels": [
  {"name": "candlestick_1m", "symbols": ["BTCUSD"]},
  {"name": "ticker", "symbols": ["BTCUSD"]},
  {"name": "mark_price", "symbols": ["MARK:BTCUSD"]},
  {"name": "system_status"}
]}}
```

Heartbeat is enabled with `{"type": "enable_heartbeat"}`. Live heartbeats look like `{"type": "heartbeat", "ts_origin": ..., "ts_publish": ...}` with microsecond timestamps. Any message with `type == "heartbeat"` resets the timeout. If nothing arrives for 35 seconds the socket is closed and reconnected. The delay is `min(60, 2^(attempt-1))` seconds plus up to 20 percent jitter, and shutdown interrupts the wait.

`candlestick_1m` fields that were observed:

| Field | Meaning |
| --- | --- |
| `cst` | Candle open in microseconds. `cst / 1_000_000` equals the REST `time` for the same minute. |
| `ts` | Publish time in microseconds. It is later in the same minute. It is not the open. |
| `o,h,l,c,v` | Forming traded OHLC and volume. Later messages with the same `cst` revise the bar. |
| `sy` | `BTCUSD` for the traded price. `MARK:` symbols are mark candles and are rejected. |
| `res` | `1m` |

`ticker` carries product id `27` in `d[0].i` and a microsecond `ts`. Ticks are not stored. `mark_price` has string price `p`, symbol `MARK:BTCUSD`, and microsecond `ts`. `system_status` snapshot had `status: "live"`.

## Rate limits

Docs: unauthenticated calls share a quota of 20,000 per 5-minute window, per IP. `/v2/products` and `/v2/history/candles` cost 3 each. HTTP 429 may include `X-RATE-LIMIT-RESET` in milliseconds. The successful 200 responses in this phase did not include rate-limit headers. The client still waits on 429 using that header when it is present, and backs off on 5xx and timeouts. It does not retry a 4xx other than 429.

## Unsupported or unused

- Authenticated trading, orders, positions, wallet, and leverage routes
- Legacy private socket channels
- Resolutions other than the six stored timeframes
- 1-minute history before 2024-02-03
- Unlimited lookback. Daily history currently starts 2023-12-29, which is earlier than 1-minute history. Missing 1-minute bars before 2024-02-03 are not invented.
