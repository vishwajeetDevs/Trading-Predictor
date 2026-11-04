# Phase 5 implementation report

Date: 2026-11-04.

The extension lives in `chrome-extension/`. Backend code is unchanged by the panel. The verification dashboard is still in `verification-dashboard/`.

Install steps are in `docs/CHROME_EXTENSION_SETUP.md`. This build is unpacked only. It is not in the Chrome Web Store.

## Stack

- Manifest V3
- React 19, TypeScript, Vite 6
- Side panel via `side_panel.default_path` and `chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })`
- `lightweight-charts` 4.2 for candles, volume, and line series
- Vitest and Testing Library

`vite.config.ts` sets `base: "./"` so the unpacked `dist` folder can load its assets. `npm run build` runs `tsc` and Vite. Test files are excluded from `tsc` so the production check does not require Node types.

## Permissions

- `sidePanel`
- `storage`
- Hosts: `http://127.0.0.1:8000/*` and `http://localhost:8000/*`

No remote scripts, no `eval`, no secrets, no trading paths. The client rejects a base URL that is not `http` or `https`, and it rejects embedded credentials. Responses are rendered as text and chart numbers. HTML from the API is not inserted.

Changing the URL in settings to an origin outside those two hosts will fail until the manifest is edited and the extension is rebuilt.

## What the panel shows

Data comes from `GET /health`, `GET /diagnostics`, `GET /analysis/snapshot`, `GET /candles`, and `GET /derivatives/snapshot`. The panel does not recompute indicators, levels, scenarios, or derivatives metrics.

| Section | Behavior | Status |
| --- | --- | --- |
| Market overview | Symbol, price, last completed candle time, freshness, health, structure. A stale close is labeled stale. | IMPLEMENTED. Browser install NOT VERIFIED. |
| Multi-timeframe structure | Separate 1m, 5m, 15m, 1h, 4h, 1d labels. Disagreements stay visible. No average. | IMPLEMENTED. Browser install NOT VERIFIED. |
| Indicators | RSI, SMA, EMA, MACD, VWAP, Bollinger, ATR, realized volatility, with tooltips and timestamps from the snapshot. | IMPLEMENTED. Browser install NOT VERIFIED. |
| Reference levels | Prior and current UTC day high and low, swings, midpoint, confirmed breakouts, distance from the completed close. Labeled as reference levels. | IMPLEMENTED. Browser install NOT VERIFIED. |
| Historical scenarios | Horizon selector 15m, 30m, 1h, 4h, 24h. Sample counts, de-clustered counts, median, P10, P90, frequencies, base rate, reliability, exclusions, study freshness. Insufficient samples stay labeled that way. | IMPLEMENTED as tables. Distribution charts NOT IMPLEMENTED. |
| Crash and volatility context | Backend extreme-downside and volatility fields, sample size, freshness. No “crash coming” wording. | IMPLEMENTED. Browser install NOT VERIFIED. |
| Derivatives | Funding, history, open interest, change, mark, last, index, spread, imbalance, freshness. Nulls stay empty. | IMPLEMENTED. Browser install NOT VERIFIED. |
| Charts | Candles plus volume, funding line, open-interest line, mark line. Empty series show an empty state. | IMPLEMENTED. Indicator overlays on the candle chart NOT IMPLEMENTED. |
| Data health | Backend, database, REST, socket, last update, candle, study watermark, derivatives freshness, gaps, quality warnings from the status payload. | IMPLEMENTED. Browser install NOT VERIFIED. |
| Settings | Timeframe, manual refresh, auto refresh interval, backend URL, section collapse, last updated, connection, retry, reset. URL and interval use `chrome.storage` when the extension API exists. | IMPLEMENTED. Browser install NOT VERIFIED. |

Auto refresh uses the configured interval. Candle history requests use `limit=240` for the selected timeframe rather than the full archive.

## Build and tests

From `chrome-extension` on 2026-11-04:

- `npm test`: 6 passed (empty payload, backend error, stale banner, manifest permissions, no order-placement UI).
- `npm run build`: `tsc` and Vite succeeded. Output includes `dist/index.html`, `dist/manifest.json`, `dist/background.js`, CSS about 1.70 kB, and JS about 403.63 kB (gzip about 126.78 kB).

`npm install` reported 4 dependency audit findings (1 moderate, 3 critical). They were not changed.

Chrome was not opened and the unpacked folder was not loaded. Side-panel behavior is therefore **IMPLEMENTED BUT NOT VERIFIED** in the browser.

## Feature classification

| Feature | Status |
| --- | --- |
| Manifest, permissions, production build, unit tests | VERIFIED WORKING |
| Side panel UI against a live API inside Chrome | IMPLEMENTED BUT NOT VERIFIED |
| Scenario distribution charts | NOT IMPLEMENTED |
| Indicator overlays on the candle chart | NOT IMPLEMENTED |
| Synthetic chart data | NOT IMPLEMENTED by design |
| Trade buttons, account access, secrets | NOT IMPLEMENTED by design |
