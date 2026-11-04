# Implementation roadmap

Status: proposed sequence. Do not start until there is an explicit approval for a later phase.

The product remains market intelligence for BTC futures on Delta Exchange. No phase adds order placement, position sizing, or a broker connection.

Each phase ends with something that can be tested on its own. Later phases do not begin by rewriting earlier contracts unless a recorded Delta payload proves a contract wrong.

## How the clone is used

Across all five phases the clone is a reference, not a dependency.

| Reimplement from the clone | Leave in the clone |
| --- | --- |
| Wilder RSI, true range, bar-completeness rule, close-beyond-level event, dedupe and fingerprint, alert conditions, seeded historical replay, "say when the sample is too small" | Nasdaq universe, Yahoo and Stooq clients, NYSE clock, SEC and House clients, Alpaca brackets, risk gate, budget, broker tickets, Pelosi, 13F weights |

Attribution: if a function body is copied, keep the MIT copyright notice with it. Prefer a short reimplementation plus a comment that names `day-trader-skill`.

## Phase 1 — Official BTC data, stored and inspectable

### Objectives

- Resolve the Delta BTC futures product from the official public API and record its tick size and symbol exactly as published.
- Backfill and store closed OHLCV for 1m, 5m, 15m, 1h, 4h, and 1d, or for the subset the API actually serves. Document the maximum lookback the API allows.
- Keep a live public WebSocket connection that closes candles and records gaps.
- Expose a read-only health and candle API.

### Modules to implement

- `data.delta_rest`, `data.stream`, `data.store`
- FastAPI `GET /health`, `GET /products/btc`, `GET /candles`
- Configuration by environment variable for the database URL and the public API base URL. No trading credential.

### Existing components to reuse

- HTTP retry and backoff pattern (reimplemented).
- Bar hygiene: drop null prices, drop incomplete bars, refuse a non-monotonic series.
- Partial-result reporting: counts of requested, stored, and failed pages.
- Offline fixture style: save real public payloads once and test against those files.

### New components to build

- Delta client and normalizer. Nothing in the clone speaks this API.
- PostgreSQL schema and migrations.
- WebSocket supervisor and gap backfill.
- Aggregation check from 1-minute bars to higher timeframes.

### Deliverables

- A running local API that returns stored BTC candles with `as_of` and a stale flag.
- A written note of which official endpoints and fields were confirmed, including any of funding, open interest, book, and liquidations that are missing.
- pytest suite that does not need the network.

### Testing

- Parser tests on recorded payloads.
- Aggregation identity tests.
- Gap test: a socket pause produces a hole, and REST fill closes it without duplicating candles.
- Malformed payload test: the service records an error and does not invent a bar.

### Dependencies

None. This phase blocks every later claim, because there is no honest statistic without this store.

## Phase 2 — Indicators, levels, and market structure

### Objectives

- Compute RSI, EMA, SMA, MACD, VWAP, Bollinger Bands, and ATR on closed bars for each stored timeframe.
- Define bullish, bearish, and sideways as swing structure on each timeframe, and show conflicts instead of forcing one label.
- Publish levels: prior UTC-day high and low, current session high and low, nearest swings, range midpoint, distance in price and in ATR.
- Classify the latest close relative to a declared level: inside, closed beyond, or failed (closed back inside within a configured bar count).

### Modules to implement

- `indicators`, `structure`
- `GET /analysis/snapshot` with only the descriptive sections filled. Horizon tables may be empty but must say "not computed yet", not a placeholder percentage.

### Existing components to reuse

- `rsi()` formula from `trader-scanner`.
- True-range definition from `trader-exit` `atr_of`, with Wilder smoothing added and named.
- SMA(20) generalized to SMA(n).
- Close-strictly-beyond-level rule from `trader-entry` `evaluate()`, including the forming-bar exclusion.
- Opening-range high, low, and midpoint only as arithmetic on a configured session, default UTC day. The 09:30 New York window is not the default.

### New components to build

- EMA, MACD, VWAP, Bollinger Bands, ATR percentile, realized volatility.
- Swing pivots and structure labels.
- Failed-breakout state. The clone never implemented it.
- Multi-timeframe snapshot assembler.

### Deliverables

- Snapshot JSON that answers, with formulas attached: current structure per timeframe, volatility bucket, breakout state, and the level list.
- A one-page definition sheet checked into the repo so the labels cannot drift from the code.

### Testing

- Wilder RSI textbook series, same expectation the scanner test uses (70.46 on that series, to one decimal).
- Hand-computed ATR, EMA, and MACD on a 40-bar fixture.
- A synthetic series with an obvious higher-high structure and one with a failed break.
- A test that a still-forming bar does not flip the label.

### Dependencies

Phase 1 candles and the stale flag. If history is shorter than the longest lookback (for example a 20-day percentile), the field is `insufficient_history`.

## Phase 3 — Historical scenarios and reliability

### Objectives

- For each published condition, measure what the next 15 minutes, 30 minutes, 1 hour, 4 hours, and 24 hours did in the stored sample.
- Show conditional frequencies next to the base rate.
- Withhold frequencies below the minimum sample size.
- Add the crash screen as a tail definition on BTC's own returns and ATR, plus the historical follow-through of that tail.
- Persist every snapshot so a later calibration job can score past statements.

### Modules to implement

- `stats`
- Study tables and an incremental job that runs on candle close, not on every HTTP request.
- Snapshot sections for scenarios, crash screen, and reliability.

### Existing components to reuse

- Time Machine ideas: scale-free bar ratios, seeded random draws, separation between resampled paths and the actual historical count, stop-and-target language removed.
- Verdict humility: a result near the base rate is reported as no separation, in the spirit of "close to a coin flip", with new wording tied to the base rate rather than to a 1R payoff.
- Minimum-sample refusal (the clone uses 20 full sessions; we start at 30 matches per condition and document it).
- Input hash so a snapshot names the definition and the data watermark, in the spirit of the risk fingerprint.

### New components to build

- Forward-return study with no look-ahead.
- Base-rate column.
- Block bootstrap for longer horizons. The clone's single-bar resample is not the default for 4-hour and 24-hour windows.
- Crash-screen definition and its historical table.
- Named percentile method.
- Calibration scaffold: store the claim, do not pretend it has been calibrated until enough future bars exist. The UI says "not yet calibrated".

### Deliverables

- Horizon tables for the structure label, the breakout state, and the crash screen.
- A test that changes the stop or target of an old Time Machine plan does not appear anywhere in this API. Those are trade-plan parameters. This phase does not accept them.

### Testing

- A fixture series with a known count of matches and known forward returns.
- A look-ahead test: editing a future bar must not change the condition at an earlier time.
- A small-N test: the response contains no frequency.
- A determinism test: same seed, same sample, same resampled quantiles.

### Dependencies

Phase 2 definitions, frozen. If a definition changes, the study is recomputed and the definition hash changes. Phase 1 history length caps what this phase can say. A short backfill means many rows stay `insufficient_history` until our own recorder has aged. That is an acceptable result.

## Phase 4 — Derivatives context and optional news

### Objectives

- Attach funding, open interest, order-book summary, and mark-versus-last when the official public API provides them.
- If liquidations or a long/short ratio are not on that API, leave those panels on "not available" with the doc link we checked. Do not scrape an unofficial page to fill them.
- Add news only if a specific public source is chosen in the phase approval. The pipeline is allowed to ship with news disabled.

### Modules to implement

- `derivatives`
- `news` adapter interface, disabled by default
- Snapshot sections that fail closed when a feed is missing

### Existing components to reuse

- News fingerprint, dedupe, seen-state, and per-feed error reporting from `trader-news`.
- The policy that a primary record beats a headline, and that a headline is not a forecast.
- Percent move beside an event, replaced by a comparison to BTC's own return distribution rather than a fixed 3% flag.

### New components to build

- Funding and open-interest history and their joint description with price.
- Book imbalance and spread at a documented depth.
- Basis between last, mark, and index.
- Any news adapter that the phase approval names. Not SEC, not Yahoo equity RSS, not the House Clerk.

### Deliverables

- Snapshot fields for the derivatives facts that exist, each with its own watermark.
- A checklist, filled from the live docs, of the microstructure questions we cannot answer.

### Testing

- Recorded payloads for book, funding, and open interest.
- A missing-field test that yields `unsupported` rather than zero. Zero would look like a real reading.
- News dedupe test if, and only if, a source is in scope.

### Dependencies

Phase 1 client. Phase 3 should already be able to display horizons without this phase. Derivatives facts may become extra conditions in the study only after they have their own history. Do not back-fit a funding story onto candles that have no funding column.

## Phase 5 — Chrome extension and monitors

### Objectives

- A Manifest V3 extension that sits beside the Delta site and renders the snapshot.
- User monitors for a price level, a return percentile, feed staleness, and a structure-label change.
- Notifications that state a fact and the watermark. They do not say to enter or exit.

### Modules to implement

- `monitor` in the backend
- Extension: service worker, side panel React app, options page
- `POST /monitors` and `DELETE /monitors` only

### Existing components to reuse

- Alert evaluation cases from `trader-alerts`: at or beyond a price, percent move, once-only, optional rearm.
- The rule that a dated condition does not fire on the wrong day, translated to "this episode already fired".

### New components to build

- The entire extension. The clone has no browser code.
- Notification copy that cannot drift into order language. A test can scan the UI strings.
- Stale, empty, conflict, and backend-down views.

### Deliverables

- An unpacked extension that points at the local API.
- A side panel that answers the ten product questions from the snapshot, leaving a question blank when earlier phases marked it unavailable.
- Browser verification of the panel: load, refresh, stale banner, and a monitor firing when a test snapshot crosses a level.

### Testing

- API tests for monitor create, fire once, and rearm.
- Frontend tests for the four empty states.
- Manual browser pass on the side panel, desktop width. A narrow popup layout if one is shipped.
- A static check that the extension manifest has no host permission aimed at placing orders, and that the backend OpenAPI still has no order route.

### Dependencies

Phases 1 and 2 are required for a useful panel. Phase 3 is required before any horizon percentage is shown. Phase 4 fields appear when present and stay on "not available" when absent. The extension must not compute its own indicators.

## Sequence

```
Phase 1  data store and official feed
   |
Phase 2  indicators, structure, levels, breakout state
   |
Phase 3  horizon studies, crash screen, reliability language
   |
   +---- Phase 4  funding, open interest, book, optional news
   |
Phase 5  extension and monitors
```

Phase 4 can overlap the end of phase 3 only for ingestion. It must not change phase 2 definitions in order to make a derivatives story look cleaner. Phase 5 can start as a shell against phase 2 snapshots, and it may display horizon numbers only after phase 3 is merged.

## What this roadmap refuses to schedule

- A trading bot, a paper-trading broker, or a Delta order client.
- Porting `trader-budget`, `trader-risk`, or `trader-broker` and "disabling" them.
- Using Yahoo charts as a stand-in for Delta history.
- Publishing a probability the study table did not compute.
- Treating NYSE opening-range hit rates, RSI 30, or a 3% equity move as BTC parameters.
- Calibration claims before the snapshot log has a forward window long enough to score.

## Suggested first approval

Approve phase 1 only. The first implementation milestone is a tested Delta candle store and a read-only candle API. Indicators wait until that store is real, because the lookback the API actually allows will decide which statistics are honest.
