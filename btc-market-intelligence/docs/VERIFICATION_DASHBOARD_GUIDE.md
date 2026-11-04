# Verification dashboard

Temporary read-only screen for the Phase 1 API. It is not the Chrome extension. It does not place orders or compute indicators.

The page polls `GET /health`, `GET /products/btc`, `GET /diagnostics`, `GET /data/status`, and the latest one-minute candles every 10 seconds. `GET /diagnostics/delta` runs once when the page opens and again only if you press its button. A failed request stays blank. The page does not substitute sample prices.

## What each section uses

- System: `/health`, plus one Delta probe from `/diagnostics/delta`. HTTP 200 is not treated as healthy. The System card shows the health `status` field. That field is `degraded` when the database is up but recent one-minute gaps are present, or when the latest closed candle is older than the stale threshold.
- Product: `/products/btc`. A 404 is shown as no stored product.
- Prices: there is no ticker route. The completed close comes from `GET /candles?timeframe=1m&limit=1&order=desc`. The newest stored bar, including a forming bar, comes from the same path with `include_forming=true`. Forming bars are labeled as forming.
- History: `1m`, `5m`, `15m`, `1h`, `4h`, and `1d`, which are the timeframes the candle route accepts. The table is `order=desc&limit=50`. The series start comes from the backfill stream on `/data/status`, not from the first of those 50 rows. The chart draws only those returned rows.
- Database and socket: `/diagnostics` and `/health`. Reconnects are the count for the current server process. Recent quality events are the latest 15 stored events. Responses do not include the database URL or password.
- API panel: buttons call the existing GET routes and keep the status, duration, and JSON.

The checklist marks PASS only from the latest payload. If the API cannot be reached, backend is FAIL and the other checks are BLOCKED, except REST gap recovery, which stays NOT VERIFIED because this screen never replays a gap.

## Start on Windows

Use two PowerShell windows. The virtual environment is `btc-market-intelligence\.venv`.

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\backend"
..\.venv\Scripts\Activate.ps1
..\.venv\Scripts\python.exe -m app.cli serve
```

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\verification-dashboard"
npm install
npm run dev
```

Open `http://127.0.0.1:5173/` or `http://localhost:5173/`.

## Tests

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\backend"
..\.venv\Scripts\python.exe -m pytest
```

That command skips the live smoke test and the PostgreSQL integration tests. To run the integration tests against the configured database, in an isolated schema:

```powershell
$env:RUN_POSTGRES_TESTS = "1"
..\.venv\Scripts\python.exe -m pytest .\tests\test_postgres_integration.py
Remove-Item Env:RUN_POSTGRES_TESTS
```

Dashboard tests and the production build:

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\verification-dashboard"
npm test
npm run build
```

## Endpoints added for this screen

These are read-only and do not replace the existing routes.

- `GET /diagnostics` returns the revision, the five known tables, `uq_candle_slot`, row counts, the latest candle write time, and the latest quality events.
- `GET /diagnostics/delta` makes one public candle request for the stored symbol.
- `GET /candles` accepts `order=asc|desc` (default `asc`) and `include_forming` (default false).
- Health now includes `websocket.reconnect_count` for the current process.
- Browser GET requests from `http://127.0.0.1:5173` and `http://localhost:5173` are allowed. The Vite dev server also proxies the API paths, so the page uses relative URLs.
