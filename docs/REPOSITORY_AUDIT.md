# Day Trader Skill — repository audit

Audit date: 2026-11-04.  
Source: local clone of [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill), version 2.0.0, MIT.  
Scope: inspection only. No scripts were modified, renamed, or executed against the network. No dependencies were installed.

This document describes what the repository actually implements. It is the evidence base for `REUSABILITY_MATRIX.md`, `BTC_INTELLIGENCE_ARCHITECTURE.md`, and `IMPLEMENTATION_ROADMAP.md`.

## 1. What this repository is

It is a pack of eleven Claude skills for US equity day trading. Each skill is a `SKILL.md` (instructions for a Claude agent) plus, in ten of the eleven folders, one standard-library Python CLI. There is no web server, no database, no package manifest, no `requirements.txt`, no Chrome extension, and no frontend.

The tools read free public equity data:

- Yahoo Finance chart and spark JSON (unofficial; the code says so)
- Yahoo Finance headline RSS
- Nasdaq Trader symbol directories
- SEC EDGAR (company tickers, 8-K Atom feeds, XBRL company facts, submissions)
- House Clerk Periodic Transaction Reports
- Stooq CSV, present as a quote fallback that the skill notes was returning 404 when written

Nothing in the pack calls Delta Exchange, a crypto exchange, a WebSocket, or an official market-data vendor.

The intended pipeline is a chain of JSON files under a `trader/` directory that does not exist until a skill writes it:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker
alerts watches the open plan
news / analyst / pelosi run on a schedule
```

Only `trader-broker` can lead to an order, and only after `trader-risk` passes that exact order and a human types yes. The Python in `ticket.py` never talks to a broker. It renders a ticket and records the reply. Order placement is delegated to Alpaca's MCP server or an Interactive Brokers connector, outside this repository.

`trader-risk` rejects any symbol that is not a US stock or ETF ticker. A test locks this with `BTC/USD`: the reason string is "this pack trades stocks and ETFs only". Crypto is out of scope by design, not by omission.

## 2. Inventory

| Kind | Count | Notes |
| --- | ---: | --- |
| Files in the clone | 85 | Recursive listing, including fixtures |
| Skill folders | 11 | Each has a `SKILL.md` |
| Implementation Python scripts | 11 | One CLI per skill except that every skill folder that has code has exactly one `.py` |
| Test modules | 13 | `unittest`, plus `tests/_load.py` |
| Offline fixtures | 41 | Yahoo charts, SEC XML/JSON, PTR text, symbol directories, one ZIP |
| Example / plugin JSON | 7 | No runtime config is committed; examples only |
| Documentation | 12 | `README.md` plus 11 skill files |
| License | 1 | MIT, copyright 2026 Jake Schincariol |

Implementation line counts (physical lines, including blanks, comments, and the repeated New York clock):

| Script | Lines | Role |
| --- | ---: | --- |
| `trader-scanner/scan.py` | 1,083 | Universe filter, daily setups, opening-range scan |
| `trader-entry/entry.py` | 960 | Close-confirmed entry on 1-minute or 5-minute bars |
| `trader-timemachine/timemachine.py` | 956 | Bar-ratio Monte Carlo and historical rule replay |
| `trader-budget/size.py` | 881 | Share sizing and Alpaca bracket JSON |
| `trader-risk/check.py` | 819 | Pre-trade PASS / BLOCK gate |
| `trader-alerts/alerts.py` | 787 | Delayed quote polling and one-shot alerts |
| `trader-exit/exit.py` | 735 | Stop, target, and simple ATR |
| `trader-broker/ticket.py` | 720 | Tickets, yes-parsing, fill log |
| `trader-pelosi/ptr.py` | 566 | House PTR index and PDF text rows |
| `trader-analyst/facts.py` | 548 | SEC XBRL quarterly fundamentals |
| `trader-news/news.py` | 546 | Headlines, 8-Ks, percent-from-prior-close |

About 8,600 lines of tool code. The thirteen test modules are about 4,500 lines more. The New York holiday calendar, DST offset, and Yahoo retry loop are copied into most market-data scripts rather than shared.

Tests use `python3 -m unittest discover -s tests`. They are offline. `tests/_load.py` loads a hyphenated skill folder by file path. There is no pytest, no coverage config, and no CI file in the clone.

## 3. Shared engineering patterns

These patterns show up in more than one script. They are the parts most worth remembering even when the trading rules are not.

**Pure functions over files.** Setup detection, entry status, stop math, Monte Carlo, risk checks, alert evaluation, and position sizing are pure functions. The CLI fetches data, calls the function, and writes JSON. That split is sound. The functions themselves are bound to equity session shapes.

**Bar hygiene.** Incomplete bars are dropped. A timestamp off the 5-minute grid (Yahoo's live tick, for example `10:58:07`) is dropped. A null close is dropped. A chart with a missing slot is unusable, because a gap may be a halt. This is the right instinct for any OHLCV engine.

**Honest partial results.** The scanner reports how many symbols were requested, fetched, unusable, and skipped. It refuses to call a capped stage-2 scan "the whole market". The Time Machine refuses to run below 20 full sessions.

**Pinned clock.** `--now "2026-10-19 10:15"` is interpreted as New York wall time, with DST computed inside the file (`_nth_sunday`), not from the machine zone. Tests replay a fixed Monday. The idea of a deterministic clock is reusable. The New York session is not.

**Polite HTTP.** `urllib` only. User-Agent identifies the tool. query2 then query1. One retry after 5 seconds on HTTP 429. A pause between calls (0.3 s to 0.5 s). SEC calls require a User-Agent with a name and email, or they are skipped. No API keys are stored in the repo. The example Alpaca config uses placeholders and tells the operator never to paste keys into chat.

**File contracts.** Skills do not import each other. They meet at:

| File | Writer | Readers |
| --- | --- | --- |
| `trader/universe.txt` | scanner `universe` | scanner `daily`, `orb` |
| `trader/watchlist.json` | scanner | news, entry |
| `trader/entry.json` | entry | exit |
| `trader/plan.json` | exit | timemachine, budget, alerts |
| `trader/timemachine.json` | timemachine | human / Claude, not a later gate |
| `trader/orders.json` | budget, or broker `flatten` | risk, broker |
| `trader/config.json` | human, from `config.example.json` | budget, risk, exit, news, analyst |
| `trader/prices.json` | alerts | budget, risk |
| `trader/portfolio.json` | broker fills | risk, budget, news, alerts, pelosi |
| `trader/state/risk.json` | risk | broker |
| `trader/state/broker.json` | broker | risk |
| `trader/state/news.json` | news | news |
| `trader/state/alerts.json` | alerts | alerts |
| `trader/alerts.json` | alerts | alerts |
| `trader/metrics.json` | analyst | human |
| `trader/state/pelosi.json` | pelosi | pelosi |

Fingerprints (SHA-1 of the order, or of a news id) stop a changed order or a repeated headline from being treated as already approved.

**Long only, one session, one symbol at the decision point.** Entry, exit, and budget refuse shorts. The Time Machine can score a sell if a plan says `side: sell`, but the upstream skills will not produce one. The scanner can return many symbols. From entry onward the workflow is one ticker.

**Language constraints.** Tools target Python 3.8. `tests/test_repo.py` forbids `zoneinfo`, `match` statements, and `list[` / `dict[` / `tuple[` generics. That constraint is for this pack. A new backend may use a current Python.

## 4. Module: trader-scanner

### Purpose

Build a US common-stock universe, then list charts that match a written rule. A hit is not a recommendation. The skill forbids printing a P&L.

### Universe (`universe`)

Downloads `nasdaqlisted.txt` and `otherlisted.txt` from `nasdaqtrader.com/dynamic/SymDir`. `classify()` keeps a row only when all of the following hold:

1. Not a test issue, not an ETF, not NextShares.
2. Ticker is 1–5 letters, or 1–4 letters plus a class suffix such as `BRK.B`. `$` preferreds and `.U` / `.W` / `.R` suffixes are out.
3. Name is not a warrant, SPAC unit, right, note, ETN, debenture, coupon, or non-ADR depositary share.
4. Names that say common stock, ordinary shares, ADS/ADR, or partnership units are kept.
5. Remaining names that still say unit, right, bond, or preferred are dropped.
6. Plain corporate names are kept.

The skill records 5,943 names on the 19 Oct 2026 files. This filter has no meaning for a single BTC perpetual.

### Daily scan (`daily`)

Yahoo spark, 20 symbols per request, daily closes for about three months. Spark has no highs or lows, so every setup is close-only. Liquidity gate: last price at least 5 (default) and last session volume at least 1,000,000. Today's daily bar is ignored until the session close, so an in-progress day cannot look like a completed breakout.

`find_setups()` needs at least 30 closes (`MIN_DAILY_BARS`). Rules:

| Tag | Formula |
| --- | --- |
| `breakout_20d` | Last close is strictly above the maximum of the previous 20 closes. This is a closing-high breakout, not an intraday-high breakout. |
| `bull_flag` | `close[-6] / close[-16] - 1 > 0.10`, and `(max(last 5 closes) - min(last 5 closes)) / last close < 0.05`. No pivot structure, no volume dry-up, no high/low channel. |
| `reclaim_ma20` | Previous close was below the prior 20-close mean, and the last close is above the current 20-close mean. The mean is a simple moving average of closes. |
| `oversold` | Wilder RSI(14) under 30. |
| `oversold_bounce` | Yesterday's RSI(14) under 30 and today's close above yesterday's close. |

Wilder RSI, implemented in `rsi()`:

- Seed: average of the first 14 gains and the first 14 losses.
- Then `ag = (ag * (n - 1) + gain) / n`, same for losses.
- `RSI = 100 - 100 / (1 + ag / al)`, or 100 when average loss is 0.
- Returns `None` when fewer than 15 closes exist.

A unit test checks the Wilder textbook series to one decimal (70.46). That function is the only standard oscillator in the repository.

Hits sort by dollar volume, `price * volume`, descending. There is no composite score, no multi-factor rank, and no short-side setup.

### Opening-range scan (`orb`)

Session assumption: 09:30 to 16:00 New York, 5-minute bars, 78 bars on a full day. Early closes at 13:00 are hardcoded for three dates in 2026–2027. Holidays are a static dict for those two years. Outside those years the tools say the holiday list is unknown.

The opening range is the first `--or-minutes` (default 15): the 09:30, 09:35, and 09:40 bars.

1. Stage 1, spark closes. A symbol is a candidate if some completed bar after the range closed at or above the highest close inside the range. The code treats this as a necessary condition, because the true range high is at least as high as every range close. It is not a sufficient breakout test.
2. Stage 2, one v8 chart per candidate, real highs and lows. Range high is the max high of the range bars. Range low is the min low. A hit is the first later bar whose close is strictly above the range high. The timestamp reported is the bar's close time (the 10:10 bar is a 10:15 event). Percent over the high is `(close / range_high - 1) * 100`.
3. Stage 2 confirms at most 200 candidates (`--max-confirm`), most traded first, and labels the rest unconfirmed.

`usable()` requires every slot from 09:30 through the cutoff. A missing bar fails the chart.

The scan refuses to run before the first post-range bar has closed (09:50 for a 15-minute range), on a weekend or listed holiday, after the close, or after `no_entries_after` (default 15:30).

### What it does not do

No EMA, MACD, VWAP, Bollinger Bands, volume profile, market structure, multi-timeframe stack, or crypto session. No WebSocket. Ranking is liquidity order, not a model.

## 5. Module: trader-news

### Purpose

Show new headlines and SEC filings for watchlist, plan, and portfolio tickers, next to the percent move from the prior close. It does not score sentiment and does not forecast.

### Sources

- Yahoo headline RSS, one request per ticker: `feeds.finance.yahoo.com/rss/2.0/headline?s=TICKER`
- SEC Atom browse feed, default form 8-K. CIK comes from `company_tickers.json`. Without `sec_user_agent` or `SEC_USER_AGENT`, filings are skipped and headlines still run.
- Yahoo v8 chart meta: last price versus `chartPreviousClose` or `previousClose`. A move of at least 3% (`MOVE_PCT`) is marked MOVING.

### Processing

`tag()` applies keyword regular expressions: earnings, analyst, legal, deal, capital, people, risk, halt, regulatory, macro. An 8-K item `2.02` forces the earnings tag. Important 8-K items (`1.03`, `1.05`, `2.02`, `2.04`, `2.06`, `3.01`, `4.02`, `5.01`, `5.02`) are flagged. Filings are preferred over headlines when they disagree. That preference is a rule in the skill text, not a merge algorithm in code.

`dedupe()` collapses the same id, the same link, or the same normalized title on the same day. `select()` keeps items inside a lookback (default 48 hours) and, unless `--all`, only those whose SHA-1 fingerprint is absent from `trader/state/news.json`. Seen ids are kept 30 days.

Earnings exclusion is not a code filter that removes the ticker from a trade. The skill tells the operator that an earnings date inside the hold is a reason to stay flat by the close. The scanner and entry timer do not read that conclusion.

### Crypto relevance

The pipeline shape (fetch, tag, dedupe, fingerprint, move-beside-headline, say when a feed failed) is a reasonable news monitor. The sources are equity-specific. Yahoo per-ticker RSS and SEC 8-K item codes do not cover BTC. Macro keywords (`Fed`, `CPI`, `FOMC`) are a small start, not a crypto event model. There is no exchange-status feed, no funding announcement, no ETF-flow feed, and no liquidation headline source.

## 6. Module: trader-entry

### Purpose

Turn one setup into one moment: the first completed bar that closes above a level. Long side only. It writes `trader/entry.json` and never sizes or orders.

### Setups

- `orb`: level is the max high of the first `or_minutes` of the NYSE session. Default 15 minutes on 5-minute bars (three bars). Also supports 1-minute bars.
- `level`: the caller passes a price, intended as a 20-day high from the scanner.

A trigger requires `close > level`. A wick above the level is ignored. A close equal to the level is ignored. Optional `--min-volume-x 1.5` requires the trigger bar's volume to be at least that multiple of the opening-range bars' average volume. The 09:30 bar includes the opening auction, so the skill warns that 1.0 is already strict.

Bars before `no_entries_before` (09:45) and after `no_entries_after` (15:30, shifted earlier on a 13:00 close) cannot trigger. Status values: `waiting` (with distance from the level), `triggered`, `expired`, `no-data`, plus phases `pre-open`, `forming`, `closed`.

`watch` polls until the trigger or the cutoff. With `--now` or `--offline` it replays without sleeping. A forming bar is described and then excluded until the clock has passed the bar end and the data timestamp has caught up, or one extra minute (`SETTLE`) has passed.

`entry.json` fields used downstream: symbol, setup, side `buy`, date, status, time, bar index, price, level, interval, opening range high/low/minutes, source `yahoo`.

### What it does not do

No failed-breakout test (no "closed back inside"). No retest. No multi-timeframe confirmation. No short trigger. No session other than NYSE. Volume confirmation is a single ratio against the opening range, not relative volume versus a lookback distribution.

## 7. Module: trader-exit

### Purpose

Given an entry, compute a stop and a target before any order exists. Writes `trader/plan.json`. Long only. Refuses a stop at or above the entry, a target at or below it, a risk under 0.05% of price, and an entry at or after the flat-by time.

### Stop rules (`compute()`)

Prices use `Decimal` cents.

| Rule | Calculation |
| --- | --- |
| `or-mid` (default for an opening-range plan) | `(range_high + range_low) / 2`, then floor to the cent |
| `or-low` | Range low, floored |
| `price:X` | Caller price, floored |
| `pct:X` | `entry * (1 - X/100)`, floored |
| `atr:K` | `entry - K * ATR`, floored |

Risk per share is `entry - stop`. Target default is `2R`: `entry + R * risk`, half-up to the cent. `1R`, `1.5R`, and `price:X` are allowed. Reward:risk is `(target - entry) / risk`.

`flat_by` defaults to 15:55 New York, or 12:55 on an early close. That is a day-trade flatten rule, not an analytical horizon.

### ATR (`atr_of`)

True range of bar `i` is `high - low` on the first bar of the session (no overnight gap). Later bars use `max(high-low, abs(high-prev_close), abs(low-prev_close))`.

The reported ATR is the simple mean of the last 14 true ranges, or of all available bars when fewer than 14 exist. The tool says so. It is not Wilder-smoothed ATR. It needs at least 3 closed bars. The TSLA fixture test expects about 3.2182 on the bars up to the sample entry.

This ATR is a stop distance. It is not a volatility regime, not a percentile of ATR, and not annualized volatility.

## 8. Module: trader-timemachine

This is the most relevant analytical module, and it is also the easiest to misuse. It answers a narrow question: if the rest of today's NYSE session is assembled from this stock's own recent 5-minute bars, how often does this specific plan touch the target before the stop?

It is not a market-regime model. It is not a crash model. It does not emit a probability that "BTC will be higher in an hour" unless someone first invents an entry, a stop, and a target and then pretends the equity session math still applies. The skill text is explicit: the output is a simulation, not a prediction.

### Data

One Yahoo request: `range=60d&interval=5m`. Only regular-session bars. Full sessions strictly before the plan date are kept: exactly 78 bars, slots 0 through 77, 09:30 to 16:00. Half days, gaps, and the plan day are excluded from the pool. The run refuses below 20 full sessions. 1-minute plans are refused because Yahoo's 1-minute history is too short for that pool. A live, incomplete bar is never used.

### Pool

For each session and each slot `i` from 1 to 77:

```
(open, high, low, close) / previous bar's close
```

Slot 0 has no previous close inside this definition, so it is not sampled. The pool is time-of-day conditional: a 10:15 bar is built from historical bars near 10:15, not from a random bar in the day.

### One replay (`simulate`)

Start at the plan entry. For each remaining bar slot `i`:

- Draw `j = i + randint(-window, window)`, clamped to 1..77. Default window is 2 bars (10 minutes).
- Draw one ratio tuple from `pool[j]` with `random.Random(seed).choice`. Default seed is 19. Default replays are 10,000. The seed is part of the result so a run can be repeated.
- Scale the ratios by the running price. The open ratio is drawn and then unused. Path updates use the close ratio only.
- If no outcome yet: a long counts `stop` when `low <= stop`, else `target` when `high >= target`. The same bar touching both is a stop. That is a conservative tie-break, not a model of which print happened first. Five-minute bars cannot order two touches.

Outcomes:

- `target`, `stop`, or `neither` (still open at `--until`, default 16:00, or 13:00 on a listed early close).
- `green`: target first, or still open and above the entry (below the entry for a sell).
- R: the plan's reward multiple at the target, `-1` at the stop, otherwise `(price_at_until - entry) / risk` for a long. A gap through the stop is still scored `-1`. The code comments that a real fill can be worse. There is no slippage distribution.

Summary statistics from the 10,000 closes: median, 5th and 95th percentiles via `sorted[int(q * n)]` (a simple index, not a interpolated percentile), mean R. A second run of 2,000 paths with the same seed builds fan-chart bands at 5, 25, 50, 75, and 95 percent. A 24-bin histogram spans the 1st to 99th percentile of terminal price. 160 sample paths are stored.

### Historical rule replay (`history_replay`)

Only when the plan setup is `orb`. On each past full session, find the first bar after the opening range that closes beyond the range (above the high for a long). Apply the same stop rule (`or_mid`, `or_low` / `or_high`, or the plan's stop distance as a percent of entry) and the same target R. Walk forward with the same stop-first rule. This is the empirical record of the rule. It is small: on the published TSLA example, 59 sessions, 33 triggers, 13 target, 15 stop, 5 neither.

`resolve_rule()` infers `or_mid` when the plan stop matches the floored midpoint of that day's opening range within half a cent. ATR stops are not replayed as ATR. The test suite shows an ATR-labelled plan falls through to a percent stop in the history rule.

### Verdict (`lean`, `verdict`)

Among replays that hit a side, share = `p_target / (p_target + p_stop)`. Break-even share for a fixed R target, ignoring the "neither" paths, is `1 / (1 + R)` (50% at 1R, about 33% at 2R). Gap = share minus that break-even share.

| Absolute gap | Label |
| --- | --- |
| under 5 points | "Close to a coin flip" at 1R, otherwise "Close to break-even" |
| 5 to 15 points | "Leans toward the target" or "Leans toward the stop" |
| 15 points or more | "Strongly toward" one side |

If more than 40% of replays hit neither, a note says the terminal price decides those paths. If mean R has the opposite sign of the lean, a second note says so. The skill tells the agent to repeat the verdict line rather than rewrite it.

The published TSLA 1R example (seed 19): target first 29.4%, stop first 25.5%, neither 45.1%, green 51.7%, median close 364.36, mean R +0.034. History average R was -0.062. Verdict: close to a coin flip. That result is the point of the module: it is willing to say the evidence is weak.

### Limitations that matter for BTC

- The pool is one symbol's last ~60 calendar days of cash-session bars. A regime change, a weekend gap, an ETF flow, or a liquidation cascade outside that sample never appears.
- Resampling bar ratios with replacement assumes those ratios are exchangeable within a ±2 bar time-of-day window. It does not model volatility clustering beyond what the window already groups, and it does not model cross-asset or funding effects.
- Terminal probabilities are conditional on a human-chosen stop and target. Change the stop and the "probability" changes. They are not base rates of market direction.
- Same-bar path is unknown. Stop-before-target biases results toward the stop.
- No transaction costs, no spread, no funding, no open interest.
- Sample for the historical rule is a few dozen triggers. The code does not attach a confidence interval or a bootstrap around the 13/33 hit count.
- Percentile indexing with `int(q * n)` is slightly coarse.
- Session length is 78 slots. BTC futures do not stop at 16:00 New York. Copying `SESSION_BARS = 78` would silently drop or mis-slot a 24-hour market.
- The method can be reimplemented for fixed horizons (next 15 minutes, 30 minutes, 1 hour, 4 hours, 24 hours) only after the session model, the conditioning variables, and the claim language are redesigned. The random-seed discipline and the separation of "resampled paths" from "what the rule actually did" should survive that redesign.

## 9. Module: trader-risk

### Purpose

A gate in front of an order. No network. Reads `orders.json`, `config.json`, `portfolio.json`, `prices.json`, and `trader/state/broker.json`. Writes PASS or BLOCK with a fingerprint.

### Checks that are account rules, not market analytics

Format of an Alpaca-shaped order, options symbols, short sales, blocked tickers, min and max order notional, position percent of account, cash buffer, margin flag, duplicate orders, orders per day, trades per day, pattern-day-trader count when `pdt_guard` is on, NYSE entry window, daily loss summed as the full stop-loss of every entry placed today (wins still count).

Loss for a market entry is `qty * (reference - stop)`, where reference is the higher of the plan entry and a quote younger than 15 minutes. Reward:risk is `(target - entry) / (entry - stop)` and must meet `min_reward_risk` (default 1.0).

Exits that only reduce a holding skip the day limits. The module never blocks a flatten solely because the entry window has closed.

### The crypto rejection

```
elif sym and ("/" in sym or not TICKER.match(sym)):
    reasons.append("... this pack trades stocks and ETFs only")
```

`TICKER` is `^[A-Z]{1,5}([.\-][A-Z]{1,2})?$`. `BTCUSD`, `BTC-PERP`, and `BTC/USD` all fail. There is no volatility metric, no VaR, no correlation, no drawdown distribution. "Risk" here means "does this order fit the operator's loss budget".

Example config (`config.example.json`): budget 5000, cash buffer 10%, max position 75%, max order 4000, min order 5, max loss per trade 50, max daily loss 150, 3 trades per day, reward:risk minimum 1, require stop, entries 09:45–15:30, flat by 15:55, paper true, options/margin/shorts off, `pdt_guard` false. `sec_user_agent` is a placeholder, not a secret.

## 10. Module: trader-budget

`size_trade()` converts a plan into a whole-share count. The count is the minimum of:

- `floor(max_loss_per_trade_usd / risk_per_share)`
- `floor(budget * max_position_pct / entry)`
- `floor(max_order_usd / entry)`
- `floor(spendable cash / entry)`

A fresh quote is used only when it is higher than the plan entry, so a rally cannot be sized on a stale smaller risk. Reward:risk below the configured minimum refuses the order. Output is one Alpaca bracket: market or limit buy, `take_profit_limit_price`, `stop_loss_stop_price`. Notes in `why` and `plan` are stripped before the broker payload.

A second mode, `--weights`, splits a budget by target weights (the example is a renormalized Berkshire 13F top five). That is portfolio allocation, not a day-trade signal. With `require_stop` true, risk blocks those buys.

None of this sizes a research view. It sizes an order. It should not be ported into a non-trading intelligence product.

## 11. Module: trader-broker

`ticket.py` renders an order in plain language, checks that today's risk file contains a PASS for the same fingerprint, and accepts a reply only when `parse_yes()` sees an explicit yes for that order. "Sure", "yes to all", and a yes that names a different symbol or quantity are rejected. `flatten` writes a market sell for an open quantity. `fill` and `result` update `portfolio.json` and the broker log, including the entry's max loss so risk can sum the day.

The skill documents Alpaca MCP (`place_stock_order`, paper by default) and an IBKR connector. Keys belong in the operator's local MCP config. The example file contains the literal placeholders `YOUR_PAPER_API_KEY_TYPED_HERE_NEVER_IN_CHAT` and the matching secret placeholder. This audit does not repeat any live credential. The clone contains none.

This module is an execution workflow. The BTC project is not an execution product. Do not reuse it.

## 12. Module: trader-alerts

### Clock

`market_status()` reports whether the NYSE regular session is open, the next open, and whether today is an early close. Holidays match the scanner. DST is internal.

### Quotes

Default source is Yahoo v8 chart meta, one symbol per request, 0.5 s apart, query1 fallback. Stooq CSV parser remains (`stooq.com/q/l/?s=sym.us&f=sd2t2ohlcv`). `quote --write` merges into `prices.json` with an `as_of` timestamp. Risk ignores a price older than 15 minutes. The skill forbids calling these quotes live.

### Rules (`evaluate`)

| Type | Fires when |
| --- | --- |
| `above` / `target` | price at or above the level |
| `below` / `stop` | price at or below the level |
| `pct_move` | absolute percent from previous close, or a stored reference, optionally one direction; at most once per New York day |
| `time` | local clock has passed `at`; `before_close` shifts it three hours earlier on an early close |
| `opening_bell` | session is open; once per day |

A rule with `date` is ignored on every other date. `rearm: true` lets a price rule fire again after the price leaves the level. State is `trader/state/alerts.json`. `add --from-plan` creates stop, target, and flat-by rules for the plan's symbol and date.

The evaluator is a small condition engine. The NYSE clock, the flat-by copy that tells the user to call the broker, and the Yahoo fetcher are not. There is no push channel, no WebSocket, and no browser notification. Polling is the whole design.

## 13. Module: trader-analyst

`facts.py` reads SEC XBRL company facts and builds a quarterly table.

Concepts tried, in order, include `RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues`, `RevenuesNetOfInterestExpense`, `SalesRevenueNet`, cost of revenue, `GrossProfit`, `OperatingIncomeLoss`, `NetIncomeLoss`, and diluted EPS.

A quarter is a duration fact of 80–100 days from a 10-Q, 10-K, 20-F, or 40-F. When the same period appears in a later filing, the latest value wins and the original fiscal label is kept. Q4 is derived as full-year minus the three quarters inside it, and marked `derived`. Year-over-year matches a period about 365 days earlier, slack 10 days. Margins are `100 * part / revenue`. Trailing twelve months sums the last four quarters when they span under 300 days.

`stale_note()` warns when a newer 10-Q exists but the facts API has not caught up. `changes()` writes sentences only from those numbers. The tool refuses to call SEC without a real User-Agent. It does not fetch transcripts. It does not estimate a missing quarter. It does not say a stock is cheap or expensive.

`metrics.example.json` is a real snapshot dated 2026-10-13 for AAPL, AXP, GOOGL, KO, and BAC. Banks in that file have null gross and operating margins, which matches the skill: blank, not zero.

None of the GAAP concepts exist for BTC. The habits worth keeping are methodological: cite the source, mark derived figures, compare like with like, and refuse to fill a gap with an invention. The integration itself does not transfer.

## 14. Module: trader-pelosi

`ptr.py` downloads the House Clerk yearly index ZIP (`<YEAR>FD.zip`), filters FilingType `P`, and parses `pdftotext -layout` output into rows: owner (self, spouse, joint, dependent child), ticker, buy or sell, option strike and expiry when present, trade date, amount range, and disclosure lag. Late means more than 45 days. Amounts stay ranges. State ensures each DocID alerts once. Senate search is documented as a manual browser step because the site requires a human agreement. The skill forbids implying inside information.

This is US political-disclosure software. It has no role in BTC futures intelligence.

## 15. Tests and fixtures

Each skill has a `tests/test_*.py` that loads the script and asserts the published reel numbers where they exist: TSLA on Monday 2026-10-19, opening range 354.47 to 362.18, trigger 364.18 at 10:15, stop 358.32, target 370.04, risk 5.86, 8 shares, max loss 46.88, Time Machine seed 19.

Fixtures live under `tests/fixtures/` and are cut from real responses (Yahoo chart JSON, some gzipped, SEC XML and company facts, House index ZIP, PTR text, Nasdaq symbol files, Stooq CSV). `DEMO-1m-synthetic.json` is the named synthetic entry fixture. Tests monkeypatch sleep and HTTP so the suite does not need a network.

`test_repo.py` checks that all eleven skills exist, plugin JSON matches the README, order-path skills say "not financial advice", and tool source stays on the Python 3.8 subset.

The testing idea to copy is: freeze vendor payloads, call pure functions, assert exact numbers, and forbid the narrative layer from rounding a weak result into an edge. The payloads themselves are Yahoo and SEC, so they are not BTC fixtures.

## 16. Configuration and secrets

Committed JSON is example-only. `config.example.json` holds a placeholder SEC contact string. `claude_desktop_config.example.json` holds placeholder Alpaca paper key names, `ALPACA_PAPER_TRADE` true, and toolset `account,trading,stock-data`. No `.env` file exists. No live key was found in the files inspected.

## 17. Calculations present versus calculations absent

Present, and correctly scoped to equities:

- Wilder RSI(14) on closes
- SMA(20) on closes
- Close-only 20-session high, a 10-session 10% run, and a 5-close 5% box
- Opening-range high, low, and midpoint
- Strict close-above-level trigger
- Optional volume multiple versus the opening-range average
- Simple-average ATR(14) from true range, session-local
- Fixed-fraction and R-multiple distances
- Dollar-volume sort
- Percent move from prior close
- Bar-ratio Monte Carlo with a seeded RNG, time-of-day window, stop-first tie-break, percentile bands, and an R-adjusted verdict
- In-sample replay of the same opening-range rule on prior full sessions
- Position size from a cash loss budget
- Order-level loss, reward:risk, and daily loss sum

Absent:

- EMA, MACD, VWAP, Bollinger Bands, stochastic, ADX
- Wilder-smoothed ATR, realized volatility, Parkinson or Garman-Klass estimators
- Volume profile, anchored volume, delta, cumulative volume delta
- Order book, trades tape, open interest, funding, mark versus index, liquidations, long/short ratio
- Support and resistance beyond the opening range and a caller-supplied level
- Swing structure, higher-high / lower-low classification, regime labels
- Fake-breakout or failed-breakout logic
- Crash or tail-event definition
- Multi-timeframe alignment
- Any instrument that trades outside NYSE cash hours
- Walk-forward testing, purged cross-validation, transaction-cost model, calibration curve, Brier score, or confidence interval on a hit rate
- WebSocket ingestion, database storage, HTTP API, browser UI

## 18. License note for later reuse

The pack is MIT. Copying a function into a new codebase is permitted if the copyright notice and license stay with the substantial copy. The practical recommendation in the other documents is still to reimplement the few small formulas in the new package and cite this repository, rather than import the CLIs. The CLIs are not libraries: they hard-code Yahoo hosts, NYSE hours, and `trader/*.json` paths, and they call `sys.exit` from `main`.
