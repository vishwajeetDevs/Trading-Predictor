# Phase 1 stabilization report

Checked on 2026-11-04. The first pass followed the socket outage and 149 missing one-minute intervals. A later dashboard read still showed 2 missing closed minutes; those timestamps, the REST recovery, and the 05:30 UTC health check are in the REST section below. Counts are observations, not targets.

| Item | Result |
| --- | --- |
| WebSocket heartbeat and reconnect | PASS |
| Subscription restored, not duplicated | PASS |
| REST gap recovery | PASS |
| Recovery idempotency | PASS |
| Missing minutes left uninvented when REST is short | PASS |
| Reconciliation mismatches recorded, exchange bar kept against an aggregate | PASS |
| Incomplete aggregates not filled in | PASS |
| Duplicate one-minute volume | PASS |
| Dashboard matches the live payload | PASS |
| Price mismatches removed | NOT VERIFIED. Differences stay on record. |
| Recovery of holes older than one day | NOT VERIFIED |
| Raw 5-second ping/pong as a second check | NOT VERIFIED |

## WebSocket — PASS

Root cause of `sent 1011 (internal error) keepalive ping timeout; no close frame received`: that close frame was sent by our client. The `websockets` library was configured with a protocol ping every 20 seconds and a 20-second pong timeout. Delta's documented check is different. The recommended method is `{"type":"enable_heartbeat"}`. The server then sends an application heartbeat about every 30 seconds, and the client reconnects only after 35 seconds without one. The library's shorter ping closed the feed before that deadline, and the server sent no close frame because the client had already hung up.

The fix does not turn the check off. Library pings are disabled. Each new connection sends `enable_heartbeat`, starts a 35-second heartbeat deadline, and reconnects when that deadline passes. A candle or ticker does not reset the deadline. Subscriptions are built again on every connection, and the same connection does not send them twice. Reconnect delay is still exponential with jitter, capped at 60 seconds. Shutdown cancels the receive wait instead of sitting out the heartbeat window.

Live result after restart, about 05:13 UTC:

- Socket connected to the public India endpoint.
- Last heartbeat `2026-11-04T05:13:42Z`, age about 5 seconds.
- Reconnects this process: 0.
- Last socket error: empty.
- Health `status` was `ok`, `stale` was false, freshness about 168 seconds.

## REST gap recovery — PASS

The old recovery ran only when a live candle jumped forward inside one socket session. A reconnect created a new candle book, so minutes missed while the process was down were never requested. Recovery now runs before each connection, again when a live sequence jumps, and once a minute while the socket stays up.

### Exact intervals the dashboard counted

Queried from PostgreSQL at about 05:21 UTC, while health reported `recent_missing_candles` 2. Both were inside the latest 300 closed one-minute opens. They were real closed minutes, not the current forming bar and not a timestamp-alignment artifact.

| Open (UTC) | What was stored | Exchange candle |
| --- | --- | --- |
| 2026-11-04T05:11:00Z | One `delta_ws` row, `is_closed` false, received 05:11:37Z. Open 77437, high 77440, low 77429, close 77431.5, volume 2953. That is a partial print from 37 seconds into the minute. | REST returned open 77440, high 77440, low 77345.5, close 77345.5, volume 15449. |
| 2026-11-04T05:12:00Z | No row. Neighbors 05:10 and 05:13 were present. | REST returned open 77345.5, high 77365, low 77335, close 77335.5, volume 11743. |

### Root cause

`recover_recent_gaps` took one snapshot at about 05:11:50Z, while 05:11 was still the open minute. The current minute is excluded, so neither 05:11 nor 05:12 was on that list. The snapshot's last missing minute was 05:06. Filling those 153 older minutes took until 05:13:28Z. Nothing read the clock again, so the two minutes that closed during the fill were never requested.

The socket that had written the partial 05:11 bar was stopped at 05:11:47Z. The new candle book started at 05:13 and had no previous bar, so it did not close 05:11 and did not see a jump across 05:12. Health counts only `is_closed` rows, so the partial bar and the absent minute both showed up as missing.

### Recovery attempts

1. 05:12:00Z–05:13:28Z. `recovery_succeeded` for 2026-11-04T00:01:00Z through 2026-11-04T05:06:00Z, 153 minutes. This did not include 05:11 or 05:12.
2. 05:26:01Z–05:26:06Z. The socket had missed its 35-second heartbeat after the database pool (5 connections, 5 overflow) timed out under the dashboard's concurrent reads. The reconnect recovery requested 05:11 through 05:24 from `https://api.india.delta.exchange/v2/history/candles`. The response had 14 rows and inserted 9. `recovery_succeeded`, first 05:11, last 05:24, 10 missing minutes present afterward. 05:11 was updated in place from the partial websocket print to the REST bar. 05:12 was inserted. No second row was created for either open.
3. 05:29:55Z–05:29:59Z. A new hole at 2026-11-04T05:25:00Z had the same shape: the 05:26 snapshot ended before that minute was due, and the new book began at 05:26. Startup recovery with the retry below requested 05:25 through 05:28 (4 rows, 2 inserts). `recovery_succeeded`, first 05:25, last 05:28. The 05:25 bar is `delta_rest`, closed, open 77444.5, high 77445, low 77400, close 77419.5, volume 4776, one copy.

No candle was inserted for a minute the exchange did not return. There is no `recovery_failed` event from these attempts, and `unavailable` is empty.

### Database check after the 05:29 recovery

- 05:10, 05:11, 05:12, and 05:13 are consecutive. 05:11 and 05:12 match the REST values above and are closed.
- 05:24, 05:25, and 05:26 are consecutive. 05:25 is the REST bar above.
- `recent_missing_candles` was 0. A grouped count of one-minute opens found 0 duplicate slots.
- Health at 05:30:18Z: `status` ok, database connected, socket connected, heartbeat age under a second, `recovery_succeeded` 1, `recovery_failed` 0, `recent_missing_candles` 0, `stale` false. No existing history was deleted.

The recovery window is still the latest 1,440 closed minutes, not the whole store. A hole older than that, and not at the tip of the series, is left for a normal backfill.

## Reconciliation — PASS as reporting, mismatches remain

`reconciliation_mismatch` means an exchange candle and the aggregate of closed one-minute candles differ. The exchange row is kept when the other side is an aggregate. The event now stores the reason, both values, the delta, and whether the price gap is larger than one tick. Equal values produce no event.

`incomplete_aggregate` means a higher-timeframe bucket was not built because at least one closed one-minute slot was missing. The reason is stored. No candle is invented for that bucket.

Duplicate one-minute rows in a bucket no longer inflate volume. The later write for that minute is the one that is aggregated.

This run also recorded one-minute conflicts while recovery upserted REST bars over websocket bars. Example: `2026-11-04T05:03:00Z`, stored `delta_ws` close `77300`, incoming `delta_rest` close `77313.5`. That is larger than the 0.5 tick. The REST bar replaced the websocket bar, and the conflict was stored. An aggregate still cannot replace either exchange source. New conflict rows also say whether the stored row was kept or replaced.

## Tests — PASS

Offline pytest: 42 passed, 5 skipped. The skips are the live smoke test and the PostgreSQL integration tests. Dashboard tests from the earlier verification round: 4 passed. This follow-up did not change the dashboard tests.

Covered here: library ping disabled, heartbeat deadline, pong message parsing, candle traffic not counted as a heartbeat, reconnect after a dropped socket, subscription sent once and restored on a new connection, gap detection, REST fill, recovery idempotency, a short REST page left as a failure, aggregate volume, and mismatch reporting.

Added for the two-minute hole:

- An elapsed unclosed websocket minute and the next absent minute are both requested. The stored minute is replaced by the REST bar and marked closed. A second call does not insert another row.
- A REST page that omits one of the minutes records that timestamp on `unavailable`, does not invent it, and a later call with the candle fills only that minute.
- A recovery that succeeds, then sees a later clock, fills the minutes that closed during the first pass and stops when the next pass finds nothing.

## Dashboard

The socket card shows connection, last heartbeat, last message, reconnects, recovery success and failure counts, and the last recovery error. Missing intervals come from health. A recovery line is PASS only when a successful recovery is recorded and the recent closed series has no gap. After the 05:30Z health read those conditions are met: the process recorded one successful pass, and `recent_missing_candles` was 0. A remaining hole is FAIL, not a pass. A minute the exchange does not return stays missing and is listed on `recovery_failed`; that is not marked PASS.

## Still limited

- A network stall longer than 35 seconds still drops the socket. The next connection fills the closed minutes it missed, up to one day. While the socket stays up, the same check also runs once a minute, and a successful fill is followed by a fresh snapshot so minutes that closed during the fill are requested. The loop stops after three successful passes, or on the first failure.
- A minute Delta does not return is stored as `unavailable` on `recovery_failed` and is not invented. None of the minutes in this run were unavailable.
- The database pool is still 5 connections plus 5 overflow. A burst of slow diagnostics reads can still delay a candle write. Socket event writes now run off the heartbeat thread, so that wait no longer consumes the 35-second deadline by itself.
- Historical higher-timeframe mismatches from the original backfill are still in `data_quality_events`. They were not rewritten.
- The 5-second raw ping/pong alternative in the Delta docs is not used. The 20-second library ping was the check that produced the 1011. Liveness is the documented 35-second heartbeat.
- Phase 2 and Phase 3 started after this report. Their results are in `PHASE_2_IMPLEMENTATION_REPORT.md` and `PHASE_3_IMPLEMENTATION_REPORT.md`.
