# Layered Market Analysis and Verdict Engine — Methodology

Engine version: `verdict-v1` (`backend/app/analysis/engine/`). Endpoint: `POST /analysis/verdict` (also `GET` with query parameters) and `GET /analysis/verdict/history`.

The engine reads **closed candles only**, runs a fixed sequence of analysis layers, fuses their evidence into probabilities, and maps those probabilities through a deterministic decision table to one of five verdicts: `ENTER_NOW`, `WAIT_FOR_BETTER_ENTRY`, `AVOID_HIGH_RISK`, `HOLD`, `CONSIDER_EXIT`.

Every response carries the disclaimer: *the outputs are probabilities estimated from historical price behaviour, not guarantees; nothing here is investment advice and no orders are placed.*

## 1. Pipeline

`engine/pipeline.py::analyze(MarketData, AnalyzeRequest)` runs each layer inside a guard. A layer that raises is reported as `UNAVAILABLE` with the error in `warnings`, and the remaining layers and the verdict still run. Each layer reports `elapsed_ms`.

| Order | Layer (`layers[].name`) | Module | Produces |
|---|---|---|---|
| L1 | `regime` | `regime.py` | Per-timeframe regime (trend up / trend down / range / compression / high vol), HTF bias (1w/1d/4h/1h), daily-vs-4h conflict flag |
| L2 | `liquidity` | `liquidity.py` | Liquidity pools (PDH/PDL, PWH/PWL, session highs/lows, equal highs/lows, swing points, round numbers), sweeps |
| L3 | `support_resistance` | `zones.py` | Multi-timeframe pivot clusters, trendlines, Fibonacci retracements/extensions, classic pivots, VWAP bands, anchored VWAP, volume profile (POC / value area) |
| L4 | `sessions` | `sessions.py` | Active sessions, killzones, weekend / low-liquidity flags, IST clock |
| L5 | `momentum_volatility_volume` | `momentum.py` | RSI, MACD, stochastic, ROC, CCI, ADX/DI, ATR percentile, Bollinger/Keltner squeeze, volume z-score, OBV slope |
| L6 | `derivatives` | `derivatives_evidence.py` | Funding percentile, OI–price quadrant, mark/index basis, order-book imbalance (from stored observations) |
| L7 | `smc_ict` | `smc.py` | BOS / CHoCH / MSS with displacement, fair value gaps (fresh → partial → filled → inverted), order blocks and breakers, dealing range premium/discount and OTE, session power of three, playbook sequence |
| L8 | `patterns` | `patterns.py` | Candlestick patterns (only scored with location confluence), chart patterns from pivots, RSI divergence |
| L10 | fusion | `fusion.py` | `p_bull`, `p_bear`, `p_range`, confidence, contribution ledger |
| L11 | risk | `risk.py` | Entry zone, invalidation, targets, R:R, EV, quarter-Kelly, sizing |
| L12 | verdict | `verdict.py` | Decision table, reasons, risks, what changes the view |

The weekly series is synthesised from stored daily candles (`regime.synthesize_weekly`). The `1m` series is also used for session levels, VWAP, volume profile and power of three.

## 2. Evidence model

Each layer emits `Evidence(category, name, direction ∈ {-1,0,+1}, strength ∈ [0,1], timeframe, rationale, regime_gate, freshness)`. Categories and the maximum absolute contribution each may add to the fused score:

| Category | Cap |
|---|---|
| `structure` | 0.25 |
| `liquidity_smc` | 0.25 |
| `support_resistance` | 0.15 |
| `momentum` | 0.15 |
| `volume_derivatives` | 0.10 |
| `patterns` | 0.10 |
| `sentiment` | 0.05 (Phase 2) |
| `sessions` | 0.05 |
| `empirical_prior` | 0.15 |

Timeframe weights: 1w 0.10, 1d 0.25, 4h 0.25, 1h 0.20, 15m 0.12, 5m 0.08, 1m 0.05, derivatives 0.15.

Regime gates: evidence tagged `trend_continuation` is multiplied by 0.35 in a range regime and 0.5 in compression; evidence tagged `mean_reversion` is multiplied by 0.4 in a trend. Untagged evidence is unaffected.

## 3. Fusion

```
contribution_i = direction_i × strength_i × freshness_i × tf_weight_i × gate_i
category_total = clamp(Σ contributions in category, −cap, +cap)
logit          = 3.0 × Σ category_totals
p_dir_bull     = clamp(sigmoid(0.9 × logit), 0.03, 0.97)
p_range        = clamp(range_signal, 0.05, 0.60)
p_bull         = (1 − p_range) × p_dir_bull
p_bear         = (1 − p_range) × (1 − p_dir_bull)
```

`range_signal` grows with a range/compression regime, low ADX, a low Bollinger-width percentile and an active squeeze. The three probabilities sum to 1. `probabilities.by_horizon` reports 1h / 4h / 24h variants where longer horizons tilt slightly toward range.

### Empirical prior

If the stored scenario studies (`horizon_stats`, current definition hash) contain an `OK` row for the active condition (`confirmed_breakout`, `bullish_structure`, `bearish_structure`, …) at the requested horizon, its positive rate is shrunk toward the base rate with a Beta posterior (`alpha = MIN_SAMPLE = 30`) and reported with a Wilson 95% interval. Only an edge of at least 2 percentage points becomes evidence.

### Confidence

```
confidence = agreement × sufficiency × freshness
```

- `agreement`: half from the dispersion of signed evidence, half from the magnitude of the mean.
- `sufficiency`: fraction of layers that returned `OK`.
- `freshness`: 1.0 until the latest 1m candle is 2 minutes old, decaying to 0.2 at 30 minutes.

Confidence is reported separately from direction: a strong bearish lean with low confidence still yields `AVOID_HIGH_RISK`.

## 4. ICT playbook sequence

`smc.playbook_sequence` walks these stages for the HTF bias direction and reports the current stage, what is still missing and whether price is on the correct side of the dealing range:

1. `HTF_BIAS` — daily / 4h structure label agrees.
2. `LIQUIDITY_IDENTIFIED` — pools exist on the opposite side.
3. `SWEEP` — a wick through a pool with a close back inside (≤ 40 bars ago).
4. `MSS` — a close through the last swing against the prior trend after the sweep.
5. `DISPLACEMENT` — the break candle's body ≥ 1.5 × the 20-bar mean body and ≥ 60% of its range.
6. `IN_ENTRY_ZONE` — price inside a fresh FVG or order block in discount (long) / premium (short).
7. `CONFLUENCE_CHECKED` — no HTF conflict and normal liquidity window.

## 5. Risk plan

- **Entry**: midpoint of the fresh FVG / order block in the bias direction; otherwise the latest close (informational).
- **Invalidation**: beyond the sweep extreme or far zone edge + 0.5 ATR; falls back to 1.5 ATR when no structural level exists or the structural stop exceeds 4 ATR.
- **Targets**: nearest liquidity pools and S/R zones on the opposite side, reported as R-multiples (synthetic 1R/2R/3R if none exist).
- **Risk:reward** to the first target; minimum 2.0 to enter.
- **Expected value** in R: `p × R − (1 − p)` using the directional probability conditional on resolution.
- **Quarter Kelly**: `0.25 × (p − (1 − p) / R)`, floored at 0.
- **Sizing**: `account × risk% ÷ risk_per_unit`, converted to contracts with the product's contract value. Round down to the exchange lot size before use.
- **Time stop**: two horizons without progress.

## 6. Verdict table

| Verdict | Condition |
|---|---|
| `CONSIDER_EXIT` | Open position and any of: structure break against it within 10 bars, protective liquidity swept within 5 bars, p(position side) < 0.45, price beyond invalidation, a target reached |
| `HOLD` | Open position and none of the above |
| `AVOID_HIGH_RISK` | ATR percentile ≥ 95, confidence < 0.30, daily/4h conflict, stale data, or a scheduled high-impact event within 2h |
| `ENTER_NOW` | Playbook at `IN_ENTRY_ZONE` or later, directional p ≥ 0.60, confidence ≥ 0.50, first-target R:R ≥ 2.0 |
| `WAIT_FOR_BETTER_ENTRY` | Everything else, including a ranging HTF bias with clean conditions |

Each verdict lists `reasons`, `risks` and `what_changes_this_view`.

## 7. Persistence and caching

Every computed run is stored in `verdict_runs` (migration `0005_verdict_runs`) with its probabilities, verdict and full payload. `resolved_at` and `outcome` are reserved for later calibration (Brier score / reliability by regime). Identical requests within `VERDICT_CACHE_SECONDS` (default 30) return the cached payload with `cached: true`; `force_refresh` bypasses the cache.

Settings: `ENABLE_VERDICT`, `VERDICT_CACHE_SECONDS`, `DEFAULT_ACCOUNT_SIZE`, `DEFAULT_RISK_PERCENT`.

## 8. Request / response

```json
POST /analysis/verdict
{
  "focus_timeframe": "15m",       // 1m | 5m | 15m | 1h | 4h | 1d
  "horizon": "4h",                // 15m | 30m | 1h | 4h | 24h
  "position": {"side": "long", "entry_price": 78000, "size": 0.5},   // optional
  "account_size": 10000,          // optional
  "risk_percent": 1.0,
  "force_refresh": false
}
```

Response keys: `engine_version`, `computed_at`, `close`, `market_read`, `verdict`, `probabilities` (`p_bull`, `p_bear`, `p_range`, `confidence_components`, `category_contributions`, `ledger`, `by_horizon`), `confidence`, `why_bullish`, `why_bearish`, `risks`, `what_changes_this_view`, `trade_plan`, `htf_bias`, `regime`, `sequence`, `empirical_prior`, `layers`, `layer_summaries`, `data_freshness`, `elapsed_ms`, `disclaimer`, `cached`, `run_id`.

## 9. Known limits (Phase 1)

- No external sentiment feeds yet (Fear & Greed, Coinbase premium, CME basis, macro calendar) — `high_impact_event_within_2h` is always `null` and the `sentiment` category is unused.
- No cross-asset correlation (DXY, SPX, ETH/BTC).
- Probabilities are not yet calibrated against resolved outcomes; the `verdict_runs` table exists to make that possible.
- Pattern detectors are rule-based and conservative; they only add weight when they occur at a mapped level.
