# Chrome extension setup

This is a locally installed unpacked extension. It is not published to the Chrome Web Store.

The panel talks to the FastAPI process. Default base URL: `http://127.0.0.1:8000`.

## Windows

1. Install dependencies.

```powershell
cd "c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\chrome-extension"
npm install
```

2. Build the extension.

```powershell
npm run build
```

The build runs the TypeScript check and writes `chrome-extension\dist`. That folder contains `manifest.json`, `background.js`, `index.html`, and `assets`.

3. Open `chrome://extensions/`.
4. Enable Developer mode.
5. Click Load unpacked.
6. Select `c:\Users\ideapad slim 3\Documents\trader project\btc-market-intelligence\chrome-extension\dist`.
7. Open the Chrome Side Panel from the extension action. The service worker sets the panel to open on that click.
8. In Settings, confirm the backend URL is `http://127.0.0.1:8000`. Save. Reset restores that default.
9. Start the API from `backend` with `..\.venv\Scripts\python.exe -m app.cli serve`, then use Refresh. Connection status should show a successful `/health` response. If the API is down, the panel shows the error and a retry. It does not fill missing numbers with zeros.

## Checks before loading

```powershell
npm test
npm run build
```

`npm test` is Vitest. It does not need the exchange or a running API.

## Permissions

`manifest.json` requests `sidePanel` and `storage`.

Host access is only:

- `http://127.0.0.1:8000/*`
- `http://localhost:8000/*`

A backend on another origin will not load until the manifest host permission is changed and the extension is rebuilt. The panel does not fetch arbitrary URLs. It does not call trading routes.

There is no remote script, no `eval`, and no API secret in the package.

## What the panel does not do

It does not recalculate indicators, scenarios, or derivatives metrics. Those values come from the backend. Charts use candle, funding, open-interest, and mark series from those responses. An empty series stays empty.

Indicator overlays on the candle chart and historical-scenario distribution charts are not in this build. The scenario section shows the backend tables and the horizon selector. See `docs/PHASE_5_IMPLEMENTATION_REPORT.md`.
