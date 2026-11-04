import { useCallback, useEffect, useState } from "react";

import { callApi } from "./api";
import { CandleChart } from "./Chart";
import { TIMEFRAMES, buildChecklist, priceCaption, type Snapshot } from "./checklist";
import type { ApiResult, Candle, CandlePage, DeltaProbe, Diagnostics, Health, Product, StatusPayload } from "./types";

const TESTS = [
  ["Health", "/health"],
  ["BTC product", "/products/btc"],
  ["Candles", "/candles?timeframe=1m&limit=2&order=desc"],
  ["Data status", "/data/status"],
] as const;

export function App() {
  const [health, setHealth] = useState<ApiResult | null>(null);
  const [product, setProduct] = useState<ApiResult | null>(null);
  const [diagnostics, setDiagnostics] = useState<ApiResult | null>(null);
  const [status, setStatus] = useState<ApiResult | null>(null);
  const [completed, setCompleted] = useState<ApiResult | null>(null);
  const [newest, setNewest] = useState<ApiResult | null>(null);
  const [delta, setDelta] = useState<ApiResult | null>(null);
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>("1m");
  const [history, setHistory] = useState<ApiResult | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [manual, setManual] = useState<Record<string, ApiResult>>({});
  const [offline, setOffline] = useState(false);
  const [healthChecked, setHealthChecked] = useState(false);

  const loadHistory = useCallback(async (next: string) => {
    setLoadingHistory(true);
    const result = await callApi(`/candles?timeframe=${next}&limit=50&order=desc`);
    setHistory(result);
    setHistoryError(result.ok ? null : result.error);
    setLoadingHistory(false);
  }, []);

  const refresh = useCallback(async () => {
    await Promise.all([
      callApi("/health").then((result) => {
        setHealth(result);
        setHealthChecked(true);
        setOffline(!result.ok && result.status === null);
      }),
      callApi("/products/btc").then(setProduct),
      callApi("/diagnostics").then(setDiagnostics),
      callApi("/data/status").then(setStatus),
      callApi("/candles?timeframe=1m&limit=1&order=desc").then(setCompleted),
      callApi("/candles?timeframe=1m&limit=1&order=desc&include_forming=true").then(setNewest),
    ]);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    void loadHistory(timeframe);
    const timer = window.setInterval(() => void loadHistory(timeframe), 10000);
    return () => window.clearInterval(timer);
  }, [loadHistory, timeframe]);

  useEffect(() => {
    void callApi("/diagnostics/delta").then(setDelta);
  }, []);

  const healthBody = bodyOf<Health>(health);
  const productBody = bodyOf<Product>(product);
  const diagnosticsBody = bodyOf<Diagnostics>(diagnostics);
  const statusBody = bodyOf<StatusPayload>(status);
  const completedCandle = firstCandle(completed);
  const newestCandle = firstCandle(newest);
  const page = bodyOf<CandlePage>(history);
  const candles = page?.candles ?? [];
  const chronological = [...candles].reverse();
  const stream = statusBody?.streams.find((item) => item.timeframe === timeframe && item.stream === "backfill");
  const socket = healthBody?.websocket;
  const recoveredInLog = diagnosticsBody?.recent_quality_events?.some((event) => event.kind === "recovery_succeeded") ? 1 : 0;

  const snapshot: Snapshot = {
    reachable: Boolean(health && health.status !== null),
    databaseConnected: healthBody?.database.connected ?? null,
    revision: diagnosticsBody?.alembic_revision ?? null,
    tablesOk: diagnosticsBody ? Object.values(diagnosticsBody.tables).every(Boolean) : null,
    productSymbol: product?.ok ? productBody?.symbol ?? null : null,
    productContract: product?.ok ? productBody?.contract_type ?? null : null,
    candleTotal: diagnosticsBody?.counts.candles ?? null,
    minuteCount: diagnosticsBody?.counts.by_timeframe["1m"] ?? null,
    websocketConnected: healthBody?.websocket.connected ?? null,
    duplicateConstraint: diagnosticsBody?.constraints.uq_candle_slot ?? null,
    endpointsOk: health && product && completed && status ? [health, product, completed, status].every((item) => item.ok) : null,
    recoverySucceeded:
      socket && typeof socket.recovery_succeeded === "number"
        ? Math.max(socket.recovery_succeeded, recoveredInLog)
        : diagnosticsBody
          ? recoveredInLog
          : null,
    missingIntervals: healthBody ? healthBody.recent_missing_candles : null,
  };
  const checks = buildChecklist(snapshot, !healthChecked);

  return (
    <main>
      <h1>BTC market verification</h1>
      <p className="muted">Read-only view of the running API. Empty responses stay empty.</p>
      <section className="checks">
        {checks.map((item) => (
          <article className="check" key={item.id}>
            <div className="row"><strong>{item.label}</strong><span className={tone(item.state)}>{item.state}</span></div>
            <p className="muted">{item.evidence}</p>
          </article>
        ))}
      </section>

      <section className="grid">
        <article className="card">
          <h2>System</h2>
          <Line label="Backend" value={!healthChecked ? "checking" : offline ? "unreachable" : health?.ok ? healthBody?.status ?? "answered" : health?.error ?? "no response"} />
          <Line label="PostgreSQL" value={flag(healthBody?.database.connected)} />
          <Line label="Database error" value={healthBody?.database.error || "none reported"} />
          <Line label="Delta REST" value={delta ? (bodyOf<DeltaProbe>(delta)?.ok ? "public read succeeded" : bodyOf<DeltaProbe>(delta)?.error || "public read failed") : "not checked"} />
          <Line label="WebSocket" value={socket ? (socket.connected ? "connected" : "not connected") : "not reported"} />
          <Line label="Last update" value={healthBody?.latest_market_event_at || "none"} />
          <Line label="Latest completed candle" value={healthBody?.latest_closed_candle_at || "none"} />
          <Line label="Freshness" value={healthBody ? `${healthBody.freshness_seconds ?? "unknown"} seconds` : "unknown"} />
          <Line label="Stale" value={healthBody ? (healthBody.stale ? "yes" : "no") : "unknown"} />
        </article>
        <article className="card">
          <h2>BTC product</h2>
          {product?.status === 404 ? <p>The product endpoint returned 404. No product is stored.</p> : null}
          {productBody ? (
            <>
              <Line label="Description" value={productBody.description || "not returned"} />
              <Line label="Product ID" value={String(productBody.exchange_product_id)} />
              <Line label="Symbol" value={productBody.symbol} />
              <Line label="Contract" value={productBody.contract_type} />
              <Line label="Tick size" value={productBody.tick_size || "not returned"} />
              <Line label="Contract value" value={productBody.contract_value || "not returned"} />
              <Line label="Unit" value={productBody.contract_unit_currency || "not returned"} />
              <Line label="State" value={productBody.state} />
              <Line label="Settlement" value={productBody.settlement_time || "none"} />
            </>
          ) : <p className="muted">{product?.error || "Product not loaded."}</p>}
        </article>
        <article className="card">
          <h2>Stored BTC prices</h2>
          <p className="muted">There is no ticker endpoint. These values are stored candle closes.</p>
          <Line label={priceCaption(completedCandle)} value={completedCandle ? completedCandle.close : "none"} />
          <Line label="Completed timestamp" value={completedCandle?.timestamp || "none"} />
          <Line label={priceCaption(newestCandle)} value={newestCandle ? newestCandle.close : "none"} />
          <Line label="Newest stored timestamp" value={newestCandle?.timestamp || "none"} />
          {completedCandle ? <Ohlc candle={completedCandle} /> : null}
        </article>
      </section>

      <section className="card">
        <h2>Historical candles</h2>
        <div className="toolbar">
          {TIMEFRAMES.map((item) => (
            <button key={item} onClick={() => setTimeframe(item)} aria-pressed={item === timeframe}>{item}</button>
          ))}
        </div>
        {loadingHistory ? <p>Loading candles…</p> : null}
        {historyError ? <p className="bad">{historyError}</p> : null}
        <div className="stats">
          <Stat label="Rows in this response" value={page ? String(page.count) : "—"} />
          <Stat label="Stored for timeframe" value={diagnosticsBody ? String(diagnosticsBody.counts.by_timeframe[timeframe] ?? 0) : "—"} />
          <Stat label="Series start" value={stream?.oldest_open_time || "not reported"} />
          <Stat label="Series latest" value={stream?.newest_open_time || page?.candles[0]?.timestamp || "not reported"} />
        </div>
        <CandleChart candles={chronological} />
        <div className="scroll">
          <table>
            <thead><tr><th>Timestamp</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th><th>Source</th><th>Closed</th></tr></thead>
            <tbody>
              {candles.length === 0 ? <tr><td colSpan={8}>No candles returned.</td></tr> : candles.map((candle) => (
                <tr key={candle.timestamp}>
                  <td>{candle.timestamp}</td><td>{candle.open}</td><td>{candle.high}</td><td>{candle.low}</td><td>{candle.close}</td><td>{candle.volume}</td><td>{candle.source}</td><td>{candle.is_closed ? "yes" : "forming"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="grid">
        <article className="card">
          <h2>Database</h2>
          {diagnosticsBody ? (
            <>
              <Line label="Connected" value={flag(healthBody?.database.connected)} />
              <Line label="Revision" value={diagnosticsBody.alembic_revision || "not reported"} />
              <Line label="Products" value={String(diagnosticsBody.counts.products)} />
              <Line label="Candles" value={String(diagnosticsBody.counts.candles)} />
              <Line label="Latest write" value={diagnosticsBody.latest_received_at || "none"} />
              <Line label="Unique candle slot" value={flag(diagnosticsBody.constraints.uq_candle_slot)} />
              {Object.entries(diagnosticsBody.tables).map(([name, present]) => <Line key={name} label={name} value={present ? "present" : "missing"} />)}
              {Object.entries(diagnosticsBody.counts.by_timeframe).map(([name, count]) => <Line key={name} label={name} value={String(count)} />)}
            </>
          ) : <p className="muted">{diagnostics?.error || "Diagnostics not loaded."}</p>}
        </article>
        <article className="card">
          <h2>WebSocket</h2>
          <Line label="Connected" value={socket ? (socket.connected ? "yes" : "no") : "not reported"} />
          <Line label="Last message" value={socket?.last_message_at || "none"} />
          <Line label="Last heartbeat" value={socket?.last_heartbeat_at || "none"} />
          <Line label="Reconnects this process" value={socket && typeof socket.reconnect_count === "number" ? String(socket.reconnect_count) : "not reported"} />
          <Line label="Recoveries succeeded" value={socket && typeof socket.recovery_succeeded === "number" ? String(socket.recovery_succeeded) : "not reported"} />
          <Line label="Recoveries failed" value={socket && typeof socket.recovery_failed === "number" ? String(socket.recovery_failed) : "not reported"} />
          <Line label="Last recovery" value={socket?.last_recovery_status || "not reported"} />
          <Line label="Last recovery error" value={socket?.last_recovery_error || "none"} />
          <Line label="System status" value={socket?.system_status || "not reported"} />
          <Line label="Last socket error" value={socket?.last_error || "none"} />
          <Line label="Recent missing candles" value={healthBody ? String(healthBody.recent_missing_candles) : "not reported"} />
          <h2>Recent quality events</h2>
          {(diagnosticsBody?.recent_quality_events ?? []).length === 0 ? <p className="muted">No quality events returned.</p> : (
            <ul>{diagnosticsBody?.recent_quality_events.map((event, index) => <li key={`${event.occurred_at}-${index}`}>{event.occurred_at} {event.kind} {event.timeframe || ""} {qualitySummary(event.detail)}</li>)}</ul>
          )}
        </article>
      </section>

      <section className="card">
        <h2>API tests</h2>
        <div className="toolbar">
          {TESTS.map(([label, path]) => (
            <button key={path} onClick={() => void callApi(path).then((result) => setManual((current) => ({ ...current, [path]: result })))}>Test {label}</button>
          ))}
          <button onClick={() => void callApi("/diagnostics/delta").then(setDelta)}>Test Delta REST</button>
        </div>
        {Object.values(manual).map((result) => <ResultView key={result.path} result={result} />)}
        {delta ? <ResultView result={delta} /> : null}
      </section>
    </main>
  );
}

function bodyOf<T>(result: ApiResult | null): T | null {
  return result?.ok && result.body && typeof result.body === "object" ? result.body as T : null;
}

function firstCandle(result: ApiResult | null): Candle | null {
  const page = bodyOf<CandlePage>(result);
  return page?.candles[0] ?? null;
}

function Line({ label, value }: { label: string; value: string }) {
  return <div className="row"><span className="muted">{label}</span><span>{value}</span></div>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="card"><span className="muted">{label}</span><div>{value}</div></div>;
}

function Ohlc({ candle }: { candle: Candle }) {
  return (
    <>
      <Line label="Open" value={candle.open} />
      <Line label="High" value={candle.high} />
      <Line label="Low" value={candle.low} />
      <Line label="Close" value={candle.close} />
      <Line label="Volume" value={candle.volume} />
      <Line label="Timeframe" value={candle.timeframe} />
    </>
  );
}

function ResultView({ result }: { result: ApiResult }) {
  return (
    <details>
      <summary>{result.path} · {result.status ?? "no response"} · {result.ms} ms · {result.ok ? "success" : "failure"} · {result.testedAt}</summary>
      <pre>{JSON.stringify(result.body ?? result.error, null, 2)}</pre>
    </details>
  );
}

function qualitySummary(detail: unknown): string {
  if (!detail || typeof detail !== "object") return "";
  const record = detail as Record<string, unknown>;
  if (typeof record.reason === "string") return record.reason;
  if (typeof record.count === "number") return String(record.count);
  return "";
}

function flag(value: boolean | null | undefined): string {
  if (value === true) return "yes";
  if (value === false) return "no";
  return "not reported";
}

function tone(state: string): string {
  if (state === "PASS") return "badge ok";
  if (state === "FAIL") return "badge bad";
  return "badge warn";
}
