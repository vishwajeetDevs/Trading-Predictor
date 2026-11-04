# BTC Market Intelligence — proposed architecture

Status: design for review. No implementation in this phase.

The product is a market-intelligence desk for Bitcoin futures on Delta Exchange. It describes the current state, the levels that define that state, and the historical frequency of similar states over fixed horizons. It does not place orders, size positions, or tell the user to buy or sell.

The stack in the brief is suitable:

| Layer | Choice | Why it fits |
| --- | --- | --- |
| Market data | Delta Exchange official public REST and public WebSocket | The exchange the user is watching. Official public market data avoids the unofficial Yahoo endpoints this clone depends on |
| Backend | Python and FastAPI | The analytical code we will write is Python. FastAPI gives a typed JSON API the extension can call. The clone's CLIs are not a server and should not be shelled out to |
| Storage | PostgreSQL | Candles, funding, open interest, news items, and past analysis snapshots need queries by time. The clone's `trader/*.json` files cannot do that |
| Extension | Chrome Manifest V3 | Runs beside the Delta website. A service worker can hold the WebSocket proxy decision and the alarms. The page itself stays on Delta |
| Extension UI | React and TypeScript | A side panel or popup with scenarios, levels, and evidence is a small SPA. TypeScript keeps the API contract honest |
| Tests | pytest | The clone uses `unittest`. The practice (frozen payloads, pure functions, exact numbers) transfers. pytest is a better fit for a new package with fixtures and API tests |

Existing components do not sit inside this stack as processes. A few pure formulas and a larger set of design rules move in as new modules. See `REUSABILITY_MATRIX.md`.

## 1. System context

```
Delta website (user's tab)
        |
        |  user reads the exchange UI; the extension does not click it
        v
Chrome extension (Manifest V3)
  side panel UI (React)
  service worker: poll our API, show notifications for level rules
        |
        |  HTTPS JSON only. No exchange trading key
        v
FastAPI backend
  REST for snapshots and history
  optional SSE or WebSocket for push of our own analysis, not a raw exchange relay by default
        |
        +--> PostgreSQL
        |
        +--> Delta public REST (backfill, snapshots)
        +--> Delta public WebSocket (trades, candles, book, mark, funding if published)
        +--> optional news HTTP sources, added only when a source is chosen
```

The extension never holds a Delta trading secret. Public market endpoints should be called by the backend so there is one rate-limit budget, one clock, and one stored history. The extension calling Delta directly would duplicate connections and split the truth between the page and the panel.

If a later phase finds that a public endpoint is browser-callable and CORS-friendly, the extension may still read it for a lightweight last price. The analysis snapshot remains a backend object. Two calculators will drift.

## 2. What the system is allowed to claim

Each conclusion in the panel is a record:

| Field | Required content |
| --- | --- |
| Question | One of the ten product questions, or a user level alert |
| Definition | The formula, in words and in code version |
| Inputs | Symbol, contract, timeframes, watermark time of the newest bar used |
| Result | A label that the definition produces, such as "higher highs on 1h and 4h" |
| Evidence | The numbers: ATR percent, return percentile, distance to level, sample count |
| History | What the next horizon did after past bars that met the same definition |
| Reliability | Sample size, how far back the sample goes, and a stated gap (no funding in this run, book was stale, fewer than the minimum matches) |
| Non-claim | A fixed sentence that this is a description of history and of the current book, not a forecast |

"Bullish", "bearish", and "sideways" are names for definitions we publish. They are not opinions. If the definitions disagree across timeframes, the panel says they disagree.

"More likely" is allowed only as "in the stored sample, the next horizon's return was positive in X of N comparable windows". If N is under the minimum, the panel says the sample is too small and does not print a percentage that looks like a prediction. No percentage is fabricated to fill a gap.

Confidence is not a single gut score. It is the sample size, the stability of the label across timeframes, and whether the data feeds required by that question are fresh. A later phase can add a calibration table: of the times the service said "follow-through was common", how often the next horizon actually followed through. Until that table exists, the UI must not show a calibrated probability.

## 3. Backend modules

Proposed package layout. Names are for the new codebase, not folders in the clone.

### 3.1 `data.delta_rest`

- Resolve the BTC futures product from the public products list (perpetual first; dated futures only if we explicitly add them).
- Store the product id, symbol, tick size, contract size, and quoting currency.
- Pull historical candles for the timeframes we compute: at least 1m, 5m, 15m, 1h, 4h, 1d. Confirm the exact path and maximum lookback against Delta's current public docs at implementation time. Do not invent query parameters in this design note.
- Pull public snapshots the docs actually offer: ticker, order book, recent trades, funding, open interest. If a field is absent, the module records `unsupported`, and downstream questions that need it are marked unavailable.
- Timeouts, retries with backoff, and a recorded raw payload for tests. No trading routes are wrapped, even if the public docs list them.

### 3.2 `data.stream`

- One backend process subscribes to the public WebSocket channels required for live trades, book updates, mark price, and funding if they are on the socket.
- Normalize into append-only events.
- Build closed candles in our process as well as trusting exchange candles, and compare them. A mismatch is a data-quality event, not a silent overwrite.
- Reconnect with backoff. Persist a gap interval when the socket drops so the REST backfill can fill it.
- Heartbeat: if no event arrives inside a stated age, analysis responses carry `stale: true`.

### 3.3 `data.store`

PostgreSQL, time-ordered.

| Table | Holds |
| --- | --- |
| `products` | The BTC contract metadata we resolved |
| `candles` | OHLCV per timeframe, source (exchange or aggregated), closed flag |
| `book_snapshots` | Top of book and depth summary at a chosen cadence, not every tick forever |
| `trades` | Optional, retention-limited, for volume and aggressor stats if the feed marks side |
| `funding` | Rate, interval, timestamp |
| `open_interest` | Level and change, when the API provides it |
| `news_items` | External items, fingerprint, tags, only after a source is approved |
| `level_rules` | User monitors: price, return percentile, feed stale |
| `analysis_snapshots` | The JSON the panel showed, with code version and data watermark, so we can later score what we said |

Indexes are by product and time. Retention: full candles for the research window; book snapshots downsampled (for example once per minute plus event spikes); raw trades shorter. Exact retention is a phase-1 decision once volume is measured.

The clone has no schema to migrate.

### 3.4 `indicators`

All functions take a closed-bar series and return a series plus the parameters. A forming bar is not an input. That rule is inherited from the scanner and the entry timer.

| Indicator | Origin |
| --- | --- |
| Wilder RSI(n), default 14 | Formula matches `trader-scanner` `rsi()`. Reimplement with attribution |
| SMA(n), EMA(n) | SMA(20) exists inline. EMA is new |
| MACD(12, 26, 9) | New. Standard EMA definition, parameters stored on the output |
| VWAP | New. Needs a session anchor. For a perpetual, anchor to the UTC day unless we document another anchor. Do not reuse the NYSE open |
| Bollinger Bands(20, 2) | New. Mean and standard deviation of closes, sample definition written down |
| ATR | True range as in `trader-exit` `atr_of`, then Wilder smoothing. Also publish ATR as a percent of price and the percentile of that percent over a stated window |
| Realized volatility | New. Log-return standard deviation over a stated window, annualized only if the annualization factor is shown |

No indicator output is a trade signal. The panel may show RSI 71 on the 1-hour chart. It may not say "overbought, so sell".

### 3.5 `structure`

New. This is the larger gap.

- Swings: a pivot high or low using a declared fractal width (for example 2 bars on each side), per timeframe.
- Trend structure: higher high and higher low, lower high and lower low, or overlapping. That is the operational definition of bullish, bearish, and sideways on that timeframe.
- Range: prior UTC-day high and low, and the current session high and low. The opening-range idea can be reused only after the session start is configured. A 00:00 UTC 15-minute range is a hypothesis, not a port of the 09:30 New York range.
- Levels to monitor: session high and low, prior day high and low, swing points that price is nearest, range midpoint, and any user rule. Distance in price and in ATR.
- Breakout state, adapted from `evaluate()`: a closed bar beyond a declared level is a break. A later close back through the level within N bars is a failure. Both are events. Follow-through is whatever `stats` measured on past events, not a story about this one bar.
- Pullback: a move against the higher-timeframe structure that has not broken the last confirmed swing. Reversal: the swing structure itself has flipped. These are two labels so the panel does not use one word for both.

### 3.6 `derivatives`

New. Entirely absent from the clone.

- Funding: last rate, change versus the trailing distribution, time to next settlement if provided.
- Open interest: level and change alongside price change. The four combinations (price up or down, open interest up or down) are descriptions, not a position recommendation. State the convention in the UI.
- Order book: spread, depth within a stated number of ticks or a stated percent, imbalance of bid versus ask size. Imbalance is a snapshot fact. It is not a directional probability unless `stats` has counted what happened next.
- Liquidations and long/short ratio: include only when Delta's public API, or a documented public source we choose, actually publishes them. If the field is missing, the question is "not available from the official feed", not a guessed number.
- Mark, index, and last: show the basis. A large last-versus-mark gap is a data fact worth a sentence.

### 3.7 `stats`

The Time Machine's useful core, rebuilt.

For a declared condition C and a horizon H in {15m, 30m, 1h, 4h, 24h}:

1. Find past closed bars where C was true. Record N, the date of the first and last match, and the bars excluded (incomplete, gapped, maintenance).
2. If N is below the minimum (start at 30 matches, and raise it when the study says so), return "insufficient history" and no frequency.
3. Summarize the next H bars: median return, 10th and 90th percentile returns, fraction of windows with a positive return, median max adverse move, median max favorable move. Use a percentile method that is named (for example linear interpolation), not the clone's `int(q * n)` index, unless we keep that index and document the bias.
4. Optional resampling: draw blocks of past returns with a seeded generator to show a distribution of paths. Prefer block bootstrap over the clone's single-bar draw when the horizon is long, because single-bar draws with replacement destroy volatility clusters. The clone's ±2-slot draw is a reference, not the default for a 24-hour horizon.
5. Always print the seed, the block length, and the sentence that this is a resample of the observed sample.

Crash screen, defined before looking at live prices and then frozen:

- A downside excursion beyond a high percentile of BTC's own history (candidate: 1-hour or 4-hour return at or below the 1st percentile of the stored sample, or a true range above the 99th percentile of ATR percent).
- The screen reports "conditions match the historical tail definition" plus how often, after past matches, the next horizon continued down versus reversed.
- It does not report "a crash is coming".

Regime label for the panel is the structure label plus a volatility bucket (ATR percent percentile bands, for example below 30, 30 to 70, above 70). Multi-timeframe: show 15m, 1h, and 4h side by side. Do not collapse them into one arrow when they conflict.

"Should the user wait" is not a trade instruction. The allowed meanings are: feeds are stale, the sample for this condition is too small, or the historical follow-through of this condition was indistinguishable from the base rate (the Time Machine's "coin flip" honesty, applied to a base-rate comparison rather than to a 1R target).

### 3.8 `news`

Not in the first implementation milestone. When added:

- A source adapter interface: fetch, parse, return items with id, time, title, url.
- Fingerprint and dedupe, as in `trader-news`, stored in `news_items`.
- Tags only from words in the item or from the source's own category. Macro tags may include FOMC and CPI. Equity earnings tags are not used.
- The panel places the item next to the return since the item time. It does not attribute the return to the headline.

SEC EDGAR and the House Clerk are not adapters.

### 3.9 `monitor`

A small rules engine in the spirit of `trader-alerts` `evaluate`, without broker language.

- Price at or beyond a level.
- Absolute return over a timeframe at or beyond a percentile the user picked.
- Feed stale.
- Analysis label changed (for example 1h structure flipped), once per episode.

Notifications go to the extension. Each rule fires once until it resets or rearms.

### 3.10 `api`

FastAPI. Read-only from the client's point of view.

| Endpoint idea | Returns |
| --- | --- |
| `GET /health` | Process up, database up, socket age |
| `GET /products/btc` | Resolved contract |
| `GET /candles` | Stored OHLCV |
| `GET /analysis/snapshot` | The full evidence object for the ten questions |
| `GET /analysis/history` | Past snapshots, for the calibration view later |
| `POST /monitors` and `DELETE /monitors` | User level rules. This is not an order |
| `GET /stream` | Optional server-sent events when a new closed bar changes the snapshot |

No `POST /orders`. No route that accepts an API secret.

CORS limited to the extension origin. If the service is bound to localhost in early development, say so in the config. Do not ship a wide-open CORS policy with the extension later.

## 4. Data flow

### Backfill

1. Resolve the BTC product.
2. Request historical candles from the oldest date we choose (start with 180 days of 1m if the API allows it; otherwise the maximum official lookback, and record that cap).
3. Upsert closed candles. Drop or flag rows with null prices, non-monotonic timestamps, or a high below the low.
4. Aggregate and check: 1-minute bars should reproduce the 5-minute and 1-hour exchange candles within a stated tick tolerance. Differences are stored, not hidden.
5. Compute indicators and structure on the closed series. Store the snapshot inputs, not only the labels, so a code change can be recomputed.

### Live

1. WebSocket events update the forming candle in memory.
2. On candle close, append the closed bar, recompute the affected timeframes, and write a new `analysis_snapshots` row if a displayed field changed.
3. REST polling on a slower cadence (funding, open interest, book summary) fills fields the socket does not carry.
4. The extension polls `GET /analysis/snapshot` on an alarm (for example 15 seconds) and, if we add SSE, listens for close events. Polling matches the clone's alert checker, but the period must be short enough for a 15-minute horizon and must display the watermark.

### Historical study

Batch job, not the request path:

- Walk the candle table once.
- Emit condition matches and forward returns into a study table.
- The request path only reads that table. A snapshot must not run 10,000 simulations synchronously on every panel refresh. Precompute on each new closed bar for the horizons we display, which is cheap if the match set is incremental.

The clone runs 10,000 paths inside the CLI on demand because a human invoked it once per trade. A panel beside a live site cannot do that on every tick. The mathematics can be the same family. The scheduling cannot.

## 5. Chrome extension

Manifest V3.

| Piece | Responsibility |
| --- | --- |
| Content script, minimal | Detect that the active site is Delta. Do not scrape the order ticket. Do not read the DOM for prices if the backend already has the official feed. Do not inject click handlers into the order form |
| Service worker | Install alarms, fetch the snapshot, raise a notification when a monitor rule trips, keep the last snapshot for offline display |
| Side panel | React UI: regime by timeframe, volatility bucket, crash-screen status, breakout state, levels, five horizon tables, feed health |
| Options page | Backend base URL, which contract, which monitors. No API secret field |

The panel should remain readable when the snapshot is stale: show the watermark and disable language that sounds current.

UI states to design before build: loading, backend down, socket stale, product not found, insufficient history, timeframes in conflict, news source not configured.

## 6. Frontend and backend boundary

React in the extension is the only required frontend. A separate website is unnecessary for the first release. If a debug page is useful during development, it can be a FastAPI static mount or a small Vite app that calls the same API. It is not a second source of calculations.

TypeScript types are generated from the OpenAPI schema or written once and tested against sample JSON. The clone's ad hoc JSON files (`entry.json`, `plan.json`, `timemachine.json`) are not the schema. The snapshot schema is new and versioned.

## 7. Statistical policy

- Every frequency has N, the window, and the definition hash.
- Base rate sits beside the conditional rate. "62% of breakout bars were higher after 1 hour" is incomplete without "51% of all bars were higher after 1 hour" over the same dates.
- No look-ahead: a condition at time t uses only bars closed at t.
- Costs are not modelled as P&L. If we show an adverse excursion, it is a price move, not a loss on a position.
- Parameters (RSI length, pivot width, percentile cutoffs) are configuration, recorded on the snapshot. Changing them creates a new definition. Old snapshots stay attached to the old definition.
- We do not claim the stock-market opening-range statistics transfer to BTC. Any session range we display starts with zero claimed edge until the study table is filled.

## 8. Security and secrets

- Backend config uses environment variables for the database URL and, only if some public route later requires one, a market-data key. Nothing in the current brief requires a trading key.
- The extension bundle contains no secret.
- Logs omit query strings that might later carry tokens.
- The repository audit found no live broker keys in the day-trader clone. Keep that property in the new repo.

## 9. Testing

pytest, with the same standard as the clone's reel tests:

- Indicator tests with hand-computed series, including the Wilder RSI textbook values the scanner already checks.
- Candle aggregation tests.
- Structure tests on a short synthetic series where swings are obvious.
- Statistics tests that assert a frequency is withheld when N is small, and that a known fixture produces a known table.
- API tests with a recorded Delta payload and the socket disconnected.
- A test that the OpenAPI document contains no order route.
- Extension: typecheck and a component test for the stale and insufficient-history states. Browser verification of the side panel is required once the UI exists, per the project rule for web UI. This phase has no UI to verify.

## 10. Fit of each existing skill to this architecture

| Skill | Place in the new system |
| --- | --- |
| Scanner | Indicator and breakout definitions only, after the universe and Yahoo client are discarded |
| News | Later adapter, state-machine only |
| Entry | Breakout state machine inside `structure` |
| Exit | ATR and level distances inside `indicators` and `structure`. Not an exit planner |
| Time Machine | The parent of `stats`, after the session model is replaced |
| Risk | Freshness and input hashing only. Not the order gate |
| Budget | No place |
| Broker | No place |
| Alerts | Parent of `monitor`, without the NYSE clock |
| Analyst | Labeling habit only. No SEC client |
| Pelosi | No place |

## 11. Risks that can invalidate the architecture

- Delta's public candle history may be shorter than a serious study needs. If so, phase 1 must say so, and horizon statistics stay "insufficient" until enough of our own recorded bars exist. We do not backfill from an unofficial vendor and pretend it is Delta.
- Public WebSocket schemas change. The normalizer must tolerate unknown fields and fail closed when a required field disappears.
- A 15-minute horizon computed from lagged REST polls will look live and be late. The watermark is mandatory.
- Resampling that ignores volatility clustering will look more precise than it is. Prefer block methods and show N.
- Building the extension against DOM scraping of the Delta site would break on every redesign and could stumble into the order form. Use our API.
- Adding "just one" order button later would pull budget, risk, and broker back in. Those modules were rejected on purpose. A new approval would be required, and it is not implied by this design.
