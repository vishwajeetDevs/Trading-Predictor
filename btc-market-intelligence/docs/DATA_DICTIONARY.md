# Data dictionary

All candle times are UTC. Exchange timestamps and local receipt times are stored separately. Numbers are `Decimal` in the process and `NUMERIC(38, 12)` in PostgreSQL. JSON scientific notation is preserved exactly at parse time.

## Candle

| Field | Source | Notes |
| --- | --- | --- |
| product_id | Local products row | Foreign key. Not the Delta id. |
| symbol | REST product or WebSocket `sy` | `BTCUSD` for the traded perpetual. |
| timeframe | Requested resolution or `res` | `1m`, `5m`, `15m`, `1h`, `4h`, `1d`. |
| open_time | REST `time` or WebSocket `cst` | Candle open, UTC. REST unit is seconds. `cst` unit is microseconds. |
| open, high, low, close, volume | `o/h/l/c/v` or REST fields | Must be finite. Prices greater than 0. Volume greater than or equal to 0. High covers open and close. Low is at or below both. |
| source | Writer | `delta_rest`, `delta_ws`, or `aggregated_1m`. |
| is_closed | Derived | True when the receipt time is at or after `open_time + resolution`. A closed bar is not reopened by a later update. |
| exchange_time_raw | Original token | The integer string from `time` or `cst`, before unit conversion. |
| timestamp_unit | Detector | `s`, `ms`, or `us`, chosen from the magnitude of that payload. |
| received_at | Local clock | When this process accepted the payload. |

WebSocket `ts` is the publish time. It is kept on the event detail for diagnostics and is not used as `open_time`.

Invalid rows are rejected and counted as `malformed` quality events. No replacement bar is created.

## Product

Public fields returned by `GET /products/btc`: exchange product id, symbol, contract type, state, trading status, underlying, quoting and settling assets, tick size, contract value, contract unit currency, notional type, spot index symbol, description, launch time, settlement time, resolved time, updated time.

Keys whose names contain leverage, margin, wallet, or position are removed before `raw_metadata` is saved, and they are not on the HTTP response.

## Freshness

`freshness_seconds` is the age of the latest closed 1-minute bar, measured from the minute it closed (`open_time + 60s`) to the health check. The feed is `stale` when that age is greater than `STALE_AFTER_SECONDS` (default 180) or when no closed bar exists.

`recent_missing_candles` counts interior gaps inside the latest 300 closed 1-minute opens. Those minutes are reported only. They are not synthesised.

## Quality events

| kind | When |
| --- | --- |
| malformed | A candle row or socket message failed validation. |
| missing_candle | One interior gap, stored as a count plus the first and last missing open. |
| timestamp_unaligned | An open is not on the resolution boundary. |
| truncated_page | A REST page hit the 2,000-row cap before the requested start. The older range is requested again. |
| reconciliation_mismatch | A native candle and the 1-minute aggregate differ, or an aggregate tried to replace a native bar. The native bar is kept. |
| incomplete_aggregate | A higher-timeframe bucket did not contain every 1-minute slot, so no aggregate was written. |
| non_monotonic | A socket candle arrived with an open older than the forming bar. It does not replace the forming bar. |

## What is not stored

- Every ticker tick
- Mark-price candlesticks
- Orders, balances, or leverage settings
- Invented candles for gaps or for dates before the exchange history
