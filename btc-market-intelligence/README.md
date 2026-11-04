# BTC Market Intelligence

Read-only Bitcoin futures market data from the Delta Exchange India public API. This project does not place orders, read wallets, or calculate position size.

The original `day-trader-skill` repository is not part of this application and is left unchanged.

## What is running

- REST base: `https://api.india.delta.exchange`
- Public WebSocket: `wss://public-socket.india.delta.exchange`
- Contract: the live BTC perpetual discovered from `GET /v2/products` (symbol `BTCUSD` at the time of the Phase 1 check)
- Stored timeframes: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`
- Database: PostgreSQL

No Delta API key is required. Trading paths are refused by the REST client.

## Windows PowerShell setup

From the project directory:

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\backend\requirements.txt
```

If activation is blocked:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Database

Docker and a local `psql` client are not installed on this machine. The backend reads `DATABASE_URL` from `backend\.env`.

For a local database after [Docker Desktop](https://www.docker.com/products/docker-desktop/) is installed:

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence"
docker compose up -d
copy .\backend\.env.example .\backend\.env
```

`backend\.env.example` points at `postgresql+psycopg://btc:btc@127.0.0.1:5432/btc_intelligence`, which matches `docker-compose.yml`. If `.env` already exists, keep it and do not overwrite a working URL.

A hosted Postgres URL also works. Use the SQLAlchemy form `postgresql+psycopg://...`, not a `psql '...'` shell command. Add `?sslmode=require` when the host requires TLS.

### Migrate, backfill, and start

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\backend"
..\.venv\Scripts\python.exe -m app.cli migrate
..\.venv\Scripts\python.exe -m app.cli backfill --days 180
..\.venv\Scripts\python.exe -m app.cli study
..\.venv\Scripts\python.exe -m app.cli serve
```

`study` writes historical condition statistics. It is also started by `serve` when `ENABLE_STUDIES` is true, which is the default, and it does not run inside a GET. Set `ENABLE_STUDIES=false` to keep the server from repeating that scan.

`serve` listens on `http://127.0.0.1:8000`. With `ENABLE_WEBSOCKET=true` (the default) the same process opens one public WebSocket and subscribes to BTC `candlestick_1m`, `ticker`, `mark_price`, `funding_rate`, `ob_updates`, and `system_status`. Closed 1-minute candles are stored. Funding messages from that socket are stored when they arrive. Order-book updates stay in memory for sequence checks; the persisted book is the REST snapshot from the derivatives poller.

With `ENABLE_DERIVATIVES=true` (the default) `serve` also polls public REST every `DERIVATIVES_POLL_SECONDS` (60) and stores funding, open interest, mark and last price, and one L2 snapshot. GET routes read those stored rows. They do not call the exchange.

To run the socket without the HTTP server:

```powershell
..\.venv\Scripts\python.exe -m app.cli ws
```

Stop either process with Ctrl+C. A backfill that stops in the middle continues from the last stored candle on the next run and does not insert a second copy of the same candle.

### Check that BTC data is flowing

In a second PowerShell window, after `serve` is running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/products/btc
Invoke-RestMethod "http://127.0.0.1:8000/candles?timeframe=1m&limit=5"
Invoke-RestMethod http://127.0.0.1:8000/data/status
Invoke-RestMethod http://127.0.0.1:8000/analysis/snapshot
Invoke-RestMethod "http://127.0.0.1:8000/analysis/history?limit=20"
Invoke-RestMethod http://127.0.0.1:8000/derivatives/snapshot
```

A healthy flow looks like this:

- `/health` has `database.connected` true and `websocket.connected` true
- `latest_closed_candle_at` is recent and `stale` is false once a minute has closed
- `/products/btc` returns symbol `BTCUSD`, contract type `perpetual_futures`, state `live`
- `/candles` returns `count` greater than 0, with `source` of `delta_rest` or `delta_ws`

`stale` stays true until a closed 1-minute candle is newer than `STALE_AFTER_SECONDS` (180). Right after startup, wait for the next minute boundary.

### Tests

Offline tests do not use the network:

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\backend"
..\.venv\Scripts\python.exe -m pytest
```

One optional public smoke test:

```powershell
$env:RUN_LIVE_SMOKE = "1"
..\.venv\Scripts\python.exe -m pytest .\tests\test_live_smoke.py
```

## Read-only API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Process, database, socket, freshness, gaps |
| GET | `/products/btc` | Resolved BTC perpetual metadata |
| GET | `/candles?timeframe=1m&start=&end=&limit=500` | Stored candles, oldest first |
| GET | `/data/status` | Backfill and socket progress, quality counts |
| GET | `/analysis/snapshot` | Indicators, structure, levels, and stored studies |
| GET | `/analysis/history` | Persisted study summaries |
| GET | `/derivatives/snapshot` | Stored funding, open interest, mark, and order book |
| GET | `/derivatives/funding` | Stored funding observations |
| GET | `/derivatives/open-interest` | Stored open interest |
| GET | `/derivatives/mark-price` | Stored mark, last, and index prices |
| GET | `/derivatives/orderbook` | Latest stored L2 snapshot |

`timeframe` is one of `1m`, `5m`, `15m`, `1h`, `4h`, `1d`. `start` and `end` are unix seconds or ISO-8601. `end` is exclusive. `limit` is 1 to 5000. A derivatives response with `availability` `NOT_COLLECTED` means no row is stored yet. It does not invent a zero.

## Layout

```
btc-market-intelligence/
  backend/app/          FastAPI app, Delta clients, storage
  backend/migrations/   Alembic revisions through 0004_derivatives
  backend/tests/        Offline pytest plus skipped live and PostgreSQL tests
  chrome-extension/     Unpacked Manifest V3 side panel
  docs/                 Phase notes through Phase 5
  docker-compose.yml    Local Postgres 16
```

Indicators, studies, and derivatives states are descriptive. The service does not place orders, recommend a position, or emit a trade signal. The extension is loaded unpacked from `chrome-extension/dist`. It is not published to the Chrome Web Store. Setup is in `docs/CHROME_EXTENSION_SETUP.md`.
