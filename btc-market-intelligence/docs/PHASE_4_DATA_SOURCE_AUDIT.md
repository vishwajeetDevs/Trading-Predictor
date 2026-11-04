# Phase 4 data source audit

Audit date: 2026-11-04.

Official documentation: [https://docs.delta.exchange/](https://docs.delta.exchange/).

Region used: Delta Exchange India public REST `https://api.india.delta.exchange` and public socket `wss://public-socket.india.delta.exchange`.

This audit does not use API keys, private trading paths, account, position, or order endpoints. A path that exists on another Delta region is not treated as available here unless the India host returned it.

Contract checked: live BTC perpetual `BTCUSD`, exchange product id 27.

## How a field was classified

- **VERIFIED WORKING** — the India public endpoint or channel returned the field on 2026-11-04, and the application stores or exposes it.
- **IMPLEMENTED BUT NOT VERIFIED** — code exists, but a live packet or HTTP response was not captured for that path during this phase.
- **UNAVAILABLE** — documentation mentions the idea, or another region has it, and no public India path was found.
- **UNSUPPORTED** — the field cannot be derived from the public payload without inventing information.

## Official public REST

| Field | Official source | Public | Update | History | Parameters | Status | Limitation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Current funding rate | `GET /v2/tickers/{symbol}` field `funding_rate` | Yes, no API key. HTTP 200 for `BTCUSD`. | Each REST poll. Default poll is 60 seconds. | No documented history path. | Symbol. | VERIFIED WORKING | The official ticker property table does not list `funding_rate`. The live India payload does. The observation clock is `timestamp` in microseconds. The ISO `time` field was about a day earlier and is not used. |
| Funding interval | `GET /v2/products/{symbol}` `product_specs.rate_exchange_interval` | Yes. | Product refresh. | Not a time series. | Symbol. | VERIFIED WORKING | Observed `28800` seconds (8 hours). Clamp `funding_clamp_value` was `0.05`. The product object does not include the current rate. |
| Next funding timestamp | WebSocket `funding_rate` field `nfr` | Public socket. | On each funding message. | Not a REST history. | Subscribe `funding_rate` for the symbol. | IMPLEMENTED BUT NOT VERIFIED on the live socket | REST ticker and product objects do not include the next funding time. REST rows store `next_funding_at` as null and the API says so. |
| Open interest | `GET /v2/tickers/{symbol}` `oi`, `oi_contracts`, `oi_value`, `oi_value_symbol`, `oi_value_usd`, `oi_change_usd_6h` | Yes. HTTP 200. | Each REST poll. | No documented history path. | Symbol. | VERIFIED WORKING | `oi_change_usd_6h` is copied from the exchange. This process does not recompute it. Open interest does not identify long versus short. |
| Mark price | `GET /v2/tickers/{symbol}` `mark_price`. Socket channel `mark_price` field `p`. | Yes. | REST poll stores a sample. Socket messages arrive about every 2 seconds and are not all persisted. | No documented REST history path. | Symbol. | REST sample VERIFIED WORKING. Socket parse IMPLEMENTED. | Documentation that describes mark price as the most recent transaction is not followed. Mark price is the mark reference, not an executable price. |
| Last traded price | `GET /v2/trades/{symbol}` newest trade `price`. Ticker `close` is a fallback only when the trade list is empty. | Yes. HTTP 200. | Each REST poll. | The trades call returns a recent list, not a full archive. | Symbol. | VERIFIED WORKING | Live `result` is a list of trades. The documented `{trades: [...]}` shape is also accepted. Live trades use `buyer_role` and `seller_role`, not a single `side`. |
| Index price | Ticker and socket `spot_price` | Yes, as a price on the ticker. | Each REST poll. | No history path. | Symbol. | VERIFIED WORKING as `spot_price` | `GET /v2/indices` returns index metadata such as `.DEXBTUSD`. It does not return a live price. The API labels the source as ticker `spot_price`. |
| Order book | `GET /v2/l2orderbook/{symbol}?depth=N` | Yes. HTTP 200. | Each REST poll. Depth default 15. | No book history endpoint. | Symbol and depth. | VERIFIED WORKING | REST has no sequence number. `buy` and `sell` levels are `{price, size, depth}`. `depth` on a level is cumulative size. `last_updated_at` is microseconds. |
| Contract metadata | `GET /v2/products` and `GET /v2/products/{symbol}` | Yes. | Product refresh. | Catalogue, not a time series. | Contract type and state filters. | VERIFIED WORKING | Already stored by Phase 1. Phase 4 reads `rate_exchange_interval` from that stored metadata. |

## Official public WebSocket

| Channel | Documented content | What this process does | Status |
| --- | --- | --- | --- |
| `ticker` | Compact updates about every 5 seconds, including mark, open interest, quotes, spot, and timestamp. | Parsed for candles-adjacent context. Funding, open interest, and the stored book still come from the REST poll so each stored row has one clock. | IMPLEMENTED subscription. Persisted derivatives rows are the REST samples. |
| `mark_price` | Mark about every 2 seconds. | Parsed. Not written on every tick. | IMPLEMENTED. Persistence is the 60-second REST sample. |
| `funding_rate` | `fr`, interval `fi`, next realization `nfr`, symbol, timestamp. | Stored through `store_funding_message` with source distinct from the REST ticker. | IMPLEMENTED BUT NOT VERIFIED against a captured live message in this phase. |
| `ob_l2` | Top 15 about every 500 ms. | Not subscribed. | NOT IMPLEMENTED as a subscription. REST L2 is the stored book. |
| `ob_updates` | Snapshot, then incremental updates with sequence, checksum, and size 0 as a delete. A gap requires resubscribe. | Applied in memory. A sequence gap, error, or checksum mismatch resubscribes and writes a quality event. | IMPLEMENTED BUT NOT VERIFIED against a captured live sequence. |
| `ob_l1` | Best bid and ask. | Not subscribed. Best bid and ask come from the REST L2 snapshot. | NOT IMPLEMENTED |
| `candlestick_1m`, `system_status` | Existing Phase 1 channels. | Unchanged. | VERIFIED WORKING in earlier phases. Heartbeat and reconnect behavior is the existing socket client. |

## Fields that are not available

| Field | Classification | Reason |
| --- | --- | --- |
| Funding rate history from the exchange | UNAVAILABLE | No public India REST path is documented. Permission text that mentions history does not name a path. Stored history is only what this process samples. |
| Open interest history from the exchange | UNAVAILABLE | Same. Interval changes use stored samples only. |
| Mark price history from the exchange | UNAVAILABLE | Same. |
| Long versus short open interest | UNSUPPORTED | The public ticker does not split open interest by side. |
| Executable price equal to mark | UNSUPPORTED | Mark is not the last trade. |
| Direction from book imbalance | UNSUPPORTED | Imbalance is a local ratio of displayed depth. |

## Live probe on 2026-11-04

One public collect, no API key, wrote one row of each supported type:

- Funding rate `0.01`, sign `positive`, historical class `INSUFFICIENT_HISTORY` because the lookback had one observation and the minimum is 30.
- Open interest `815480` contracts. Exchange `oi_change_usd_6h` was `-3270522.66`.
- Mark `78008.38763866`, last trade `78012`, index `spot_price` `78035.6`. Mark minus last was `-3.61236134` (about `-0.0046305%`).
- Book best bid `78011.5`, best ask `78012`, spread `0.5`, 15 levels, imbalance about `-0.0098`.

Those numbers are one observation. They are not a historical distribution.

## What the API refuses

The REST client remains GET-only. Paths containing `/orders`, `/positions`, `/wallet`, or `/leverage` are refused. No trading credential is read.
