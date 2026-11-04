import { useEffect, useMemo, useState, type ReactNode } from "react";
import { defaultSettings, loadBundle, loadLive, loadSettings, requestVerdict, saveSettings, verdictRequestFrom, type OpenPosition, type Settings } from "./api";
import { CandleChart, candleRows, formingRow, historyPoints, LineChart } from "./charts";
import { DecisionCard } from "./Decision";
import { HORIZONS, INDICATOR_HELP, TIMEFRAMES, percent, text, tone } from "./format";
import { Nested, VerdictControls, VerdictView, obj } from "./Verdict";

type Bundle = Awaited<ReturnType<typeof loadBundle>>;
type VerdictState = { body: Record<string, unknown> | null; error: string | null; busy: boolean; at: string | null };
type Live = Record<string, unknown> | null;

export const LIVE_POLL_SECONDS = 5;

export function App() {
  const [settings, setSettings] = useState<Settings>(defaultSettings);
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [updated, setUpdated] = useState<string | null>(null);
  const [horizon, setHorizon] = useState("1h");
  const [ready, setReady] = useState(false);
  const [tick, setTick] = useState(0);
  const [verdict, setVerdict] = useState<VerdictState>({ body: null, error: null, busy: false, at: null });
  const [live, setLive] = useState<Live>(null);
  const [liveError, setLiveError] = useState<string | null>(null);

  const analyze = async (force = false) => {
    setVerdict((state) => ({ ...state, busy: true, error: null }));
    try {
      const body = await requestVerdict(settings.baseUrl, verdictRequestFrom(settings, force));
      setVerdict({ body, error: null, busy: false, at: new Date().toISOString() });
    } catch (reason) {
      setVerdict((state) => ({ ...state, busy: false, error: reason instanceof Error ? reason.message : "Analysis failed." }));
    }
  };

  // The decision follows the tape: re-run on every refresh tick once the first analysis has been requested.
  const watchDecision = verdict.body !== null;
  useEffect(() => {
    if (!ready || !watchDecision) return;
    let stop = false;
    const timer = window.setInterval(() => {
      if (stop) return;
      void analyze(false);
    }, Math.max(10, settings.refreshSeconds) * 1000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, watchDecision, settings.baseUrl, settings.refreshSeconds, settings.verdictTimeframe, settings.verdictHorizon, settings.accountSize, settings.riskPercent, settings.position]);

  // Live tape: forming bar and last price, polled lightly. No database history is re-requested here.
  useEffect(() => {
    if (!ready) return;
    let stop = false;
    const pull = async () => {
      try {
        const next = await loadLive(settings.baseUrl, settings.timeframe);
        if (!stop) {
          setLive(next);
          setLiveError(null);
        }
      } catch (reason) {
        if (!stop) setLiveError(reason instanceof Error ? reason.message : "Live feed unavailable.");
      }
    };
    void pull();
    const timer = window.setInterval(pull, LIVE_POLL_SECONDS * 1000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [ready, settings.baseUrl, settings.timeframe, tick]);

  useEffect(() => {
    loadSettings().then((stored) => {
      setSettings(stored);
      setReady(true);
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "Settings could not be read.");
      setReady(true);
    });
  }, []);

  useEffect(() => {
    if (!ready) return;
    let stop = false;
    const pull = async () => {
      try {
        const next = await loadBundle(settings.baseUrl, settings.timeframe);
        if (!stop) {
          setBundle(next);
          setError(null);
          setUpdated(new Date().toISOString());
        }
      } catch (reason) {
        if (!stop) setError(reason instanceof Error ? reason.message : "Backend is unreachable.");
      } finally {
        if (!stop) setLoading(false);
      }
    };
    void pull();
    const timer = window.setInterval(pull, Math.max(10, settings.refreshSeconds) * 1000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [ready, settings.baseUrl, settings.timeframe, settings.refreshSeconds, tick]);

  const update = (patch: Partial<Settings>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    void saveSettings(next);
  };

  return (
    <main className="app">
      <header>
        <div>
          <h1>BTC Market Intelligence</h1>
          <p className="muted">Read-only. Analytical context is not a trade instruction.</p>
        </div>
        <div className="row">
          <span className={error ? "down" : "up"}>{error ? "Disconnected" : bundle ? "Connected" : "Checking"}</span>
          <button type="button" onClick={() => setTick((value) => value + 1)}>Refresh</button>
        </div>
      </header>
      <p className="muted">Last refresh {updated ?? "not yet"} · backend {settings.baseUrl}</p>
      {error ? <div className="error">{error} The last successful view stays on screen when one exists. <button type="button" onClick={() => setTick((value) => value + 1)}>Retry</button></div> : null}
      {loading && !bundle ? <div className="skeleton" /> : null}
      {ready ? (
        <Section title="Trade decision" id="verdict" settings={settings} update={update}>
          <VerdictPanel settings={settings} update={update} state={verdict} onAnalyze={analyze} />
        </Section>
      ) : null}
      {bundle ? <Dashboard bundle={bundle} live={live} liveError={liveError} settings={settings} horizon={horizon} setHorizon={setHorizon} update={update} /> : null}
    </main>
  );
}

function VerdictPanel({ settings, update, state, onAnalyze }: {
  settings: Settings;
  update: (patch: Partial<Settings>) => void;
  state: VerdictState;
  onAnalyze: (force?: boolean) => void;
}) {
  const decision = state.body ? obj(state.body.decision) : null;
  return (
    <>
      <p className="muted">Deterministic rules on closed candles: regime → liquidity → structure → SMC/ICT → levels → momentum → derivatives → fusion → risk → decision. No AI model. Probabilities, not guarantees.</p>
      <VerdictControls
        timeframe={settings.verdictTimeframe}
        horizon={settings.verdictHorizon}
        busy={state.busy}
        onTimeframe={(verdictTimeframe) => update({ verdictTimeframe })}
        onHorizon={(verdictHorizon) => update({ verdictHorizon })}
        onAnalyze={() => onAnalyze(false)}
      />
      {state.error ? <div className="error">{state.error} <button type="button" onClick={() => onAnalyze(false)}>Retry</button></div> : null}
      {state.busy && !state.body ? <div className="skeleton" /> : null}
      {state.body ? (
        <>
          <p className="muted">Analyzed {state.at ?? ""} {settings.position ? `· open ${settings.position.side} from ${settings.position.entryPrice}` : "· no open position"} {settings.accountSize ? `· account ${settings.accountSize}` : ""} · auto-refresh every {Math.max(10, settings.refreshSeconds)}s
            {" "}<button type="button" className="link" onClick={() => onAnalyze(true)} disabled={state.busy}>Recompute now</button>
          </p>
          {decision && Object.keys(decision).length ? (
            <DecisionCard decision={decision} computedAt={text(state.body.computed_at, "") || null} watermark={text(state.body.candle_watermark, "") || null} cached={state.body.cached === true} refreshing={state.body.refreshing === true} />
          ) : <p className="warn">The backend did not return a decision block. Update the backend to the current version.</p>}
          <Nested title="Full layered analysis">
            <VerdictView body={state.body} />
          </Nested>
        </>
      ) : !state.busy && !state.error ? <p className="muted">Press Analyze to run the rule engine on the latest closed candles. It then re-runs automatically on each refresh and whenever a new candle closes.</p> : null}
    </>
  );
}

function Dashboard({ bundle, live, liveError, settings, horizon, setHorizon, update }: {
  bundle: Bundle;
  live: Live;
  liveError: string | null;
  settings: Settings;
  horizon: string;
  setHorizon: (value: string) => void;
  update: (patch: Partial<Settings>) => void;
}) {
  const health = bundle.health;
  const snapshot = bundle.snapshot;
  const derivatives = bundle.derivatives;
  const stale = Boolean(health.stale) || Boolean(derivatives.stale) || objectOf(snapshot.studies).status === "LAGGING";
  return (
    <>
      {stale ? <div className="banner">Some data is stale or the study watermark is behind the latest closed candle. Stale values are not live prices.</div> : null}
      <Section title="Market overview" id="overview" settings={settings} update={update}>
        <Overview snapshot={snapshot} health={health} live={live} />
      </Section>
      <Section title="Live chart" id="charts" settings={settings} update={update}>
        <Charts bundle={bundle} live={live} liveError={liveError} timeframe={settings.timeframe} onTimeframe={(timeframe) => update({ timeframe })} />
      </Section>
      <Section title="Market structure" id="structure" settings={settings} update={update}>
        <Structure snapshot={snapshot} />
      </Section>
      <Section title="Indicators" id="indicators" settings={settings} update={update}>
        <Indicators snapshot={snapshot} timeframe={settings.timeframe} />
      </Section>
      <Section title="Reference levels" id="levels" settings={settings} update={update}>
        <Levels snapshot={snapshot} timeframe={settings.timeframe} />
      </Section>
      <Section title="Historical scenarios" id="studies" settings={settings} update={update}>
        <Studies snapshot={snapshot} timeframe={settings.timeframe} horizon={horizon} setHorizon={setHorizon} />
      </Section>
      <Section title="Volatility context" id="volatility" settings={settings} update={update}>
        <Volatility snapshot={snapshot} />
      </Section>
      <Section title="Derivatives" id="derivatives" settings={settings} update={update}>
        <Derivatives body={derivatives} />
      </Section>
      <Section title="Derivatives history" id="derivatives-charts" settings={settings} update={update}>
        <DerivativesCharts bundle={bundle} />
      </Section>
      <Section title="Data health" id="health" settings={settings} update={update}>
        <Health health={health} diagnostics={bundle.diagnostics} snapshot={snapshot} derivatives={derivatives} />
      </Section>
      <Section title="Settings" id="settings" settings={settings} update={update}>
        <SettingsForm settings={settings} update={update} />
      </Section>
    </>
  );
}

function Section({ title, id, settings, update, children }: { title: string; id: string; settings: Settings; update: (patch: Partial<Settings>) => void; children: ReactNode }) {
  const open = !settings.collapsed[id];
  const toggle = () => update({ collapsed: { ...settings.collapsed, [id]: open } });
  return (
    <section className={`section dropdown ${open ? "is-open" : "is-closed"}`}>
      <button
        type="button"
        className="dropdown-trigger"
        aria-expanded={open}
        aria-controls={`section-body-${id}`}
        onClick={toggle}
      >
        <h2>{title}</h2>
        <span className="dropdown-meta">
          <span className="dropdown-chevron" aria-hidden="true">▾</span>
        </span>
      </button>
      <div id={`section-body-${id}`} className="dropdown-panel" aria-hidden={!open}>
        <div className="dropdown-inner">
          {children}
        </div>
      </div>
    </section>
  );
}

function Overview({ snapshot, health, live }: { snapshot: Record<string, unknown>; health: Record<string, unknown>; live: Live }) {
  const product = objectOf(snapshot.product);
  const close = health.latest_closed_candle_at;
  const liveStale = !live || live.stale !== false;
  return (
    <div className="grid">
      <Field label="Symbol" value={text(product.symbol)} />
      <Field
        label={liveStale ? "Last price (not live)" : "Last price (live)"}
        value={text(live?.last_price, "Unavailable")}
        help={liveStale ? "The socket has not delivered a fresh minute. This is the newest price the backend has seen, not a live quote." : "Close of the forming minute from the public socket."}
      />
      <Field label="Latest completed close" value={text(close, "No closed candle")} help="This is the last completed candle time, not a live executable price." />
      <Field label="Freshness seconds" value={text(health.freshness_seconds)} />
      <Field label="Health" value={text(health.status)} />
      <Field label="Stale" value={health.stale ? "Yes" : "No"} />
    </div>
  );
}

function Structure({ snapshot }: { snapshot: Record<string, unknown> }) {
  const frames = objectOf(snapshot.timeframes);
  const labels = TIMEFRAMES.map((frame) => text(objectOf(frames[frame]).label, "INSUFFICIENT DATA"));
  const unique = new Set(labels);
  return (
    <>
      {unique.size > 1 ? <p className="warn">Timeframes disagree. They are shown separately and are not averaged.</p> : null}
      <div className="grid">
        {TIMEFRAMES.map((frame) => <Field key={frame} label={frame} value={text(objectOf(frames[frame]).label, "INSUFFICIENT DATA")} />)}
      </div>
    </>
  );
}

function Indicators({ snapshot, timeframe }: { snapshot: Record<string, unknown>; timeframe: string }) {
  const view = objectOf(objectOf(snapshot.timeframes)[timeframe]);
  const indicators = objectOf(view.indicators);
  return (
    <>
      <p className="muted">{timeframe} closed-bar indicators. {text(view.warning, "")}</p>
      <div className="grid">
        {Object.entries(indicators).map(([name, value]) => (
          <Field key={name} label={name} value={formatIndicator(value)} help={INDICATOR_HELP[name] ?? "Descriptive indicator from the backend."} />
        ))}
      </div>
      {Object.keys(indicators).length === 0 ? <p>No indicator values for this timeframe.</p> : null}
    </>
  );
}

function Levels({ snapshot, timeframe }: { snapshot: Record<string, unknown>; timeframe: string }) {
  const rows = Array.isArray(objectOf(snapshot.levels)[timeframe]) ? objectOf(snapshot.levels)[timeframe] as unknown[] : [];
  return (
    <>
      <p className="muted">Analytical reference levels measured from completed candles. Not entries or exits.</p>
      {rows.length === 0 ? <p>No levels for this timeframe.</p> : (
        <table>
          <thead><tr><th>Level</th><th>Price</th><th>Distance</th><th>Confirmed</th></tr></thead>
          <tbody>
            {rows.map((row, index) => {
              const level = objectOf(row);
              return <tr key={index}><td>{text(level.name)}</td><td>{text(level.price)}</td><td>{text(level.distance)}</td><td>{text(level.confirmed_at)}</td></tr>;
            })}
          </tbody>
        </table>
      )}
    </>
  );
}

function Studies({ snapshot, timeframe, horizon, setHorizon }: { snapshot: Record<string, unknown>; timeframe: string; horizon: string; setHorizon: (value: string) => void }) {
  const studies = objectOf(snapshot.studies);
  const conditions = objectOf(objectOf(studies.by_timeframe)[timeframe]);
  return (
    <>
      <p className="muted">Study {text(studies.status)} · watermark {text(studies.watermark)} · computed {text(studies.computed_at)} · lag {text(studies.lag_seconds)}s</p>
      <label>Horizon
        <select value={horizon} onChange={(event) => setHorizon(event.target.value)}>
          {HORIZONS.map((item) => <option key={item}>{item}</option>)}
        </select>
      </label>
      {Object.keys(conditions).length === 0 ? <p>No stored study for this timeframe.</p> : Object.entries(conditions).map(([name, horizons]) => {
        const row = objectOf(objectOf(horizons)[horizon]);
        const reliable = row.status === "OK";
        return (
          <article className="card" key={name}>
            <strong>{name}</strong>
            <p className="muted">{reliable ? "Stored sample" : "Insufficient sample. Not a probability."}</p>
            <div className="grid">
              <Field label="Raw matches" value={text(row.raw_match_count)} />
              <Field label="De-clustered" value={text(row.independent_count)} help="Cooldown sample. These are not independent draws." />
              <Field label="Excluded" value={text(row.excluded)} />
              <Field label="Median" value={reliable ? text(row.median_return) : "Withheld"} />
              <Field label="P10" value={reliable ? text(row.p10_return) : "Withheld"} />
              <Field label="P90" value={reliable ? text(row.p90_return) : "Withheld"} />
              <Field label="Positive" value={reliable ? percent(row.positive_rate) : "Withheld"} />
              <Field label="Negative" value={reliable ? percent(row.negative_rate) : "Withheld"} />
              <Field label="Base comparison" value={text(objectOf(row.base_rate).status)} />
              <Field label="Difference" value={text(objectOf(row.base_rate).difference)} />
            </div>
          </article>
        );
      })}
    </>
  );
}

function Volatility({ snapshot }: { snapshot: Record<string, unknown> }) {
  const screen = objectOf(snapshot.crash_screen);
  const frames = objectOf(screen.timeframes);
  return (
    <>
      <p>{text(screen.note, "A tail match describes stored history. It is not a forecast.")}</p>
      {Object.entries(frames).map(([name, value]) => {
        const row = objectOf(value);
        return <p key={name}>{name}: {text(row.sentence, text(row.status))}</p>;
      })}
    </>
  );
}

export function Derivatives({ body }: { body: Record<string, unknown> }) {
  if (body.availability === "NOT_COLLECTED") return <p>Derivatives data has not been collected.</p>;
  const funding = objectOf(objectOf(body.funding).values);
  const interest = objectOf(objectOf(body.open_interest).values);
  const mark = objectOf(objectOf(body.mark_price).values);
  const book = objectOf(objectOf(body.orderbook).values);
  return (
    <div className="grid">
      <Field label="Funding" value={text(funding.funding_rate)} help="A positive rate is not a forecast of a decline." />
      <Field label="Funding class" value={text(objectOf(funding.classification).sign)} />
      <Field label="Open interest" value={text(interest.contracts)} help="Outstanding contracts. Not long versus short." />
      <Field label="Exchange 6h OI change" value={text(interest.exchange_change_6h)} />
      <Field label="Mark" value={text(mark.mark_price)} help="Mark price is not an executable price." />
      <Field label="Last" value={text(mark.last_price)} />
      <Field label="Index" value={text(mark.index_price)} />
      <Field label="Spread" value={text(book.spread)} />
      <Field label="Imbalance" value={text(book.imbalance)} help="Locally calculated. Not a directional signal." />
      <Field label="Fresh" value={body.stale ? "Stale" : "Current"} />
    </div>
  );
}

export function Charts({ bundle, live, liveError, timeframe, onTimeframe }: { bundle: Bundle; live: Live; liveError: string | null; timeframe: string; onTimeframe: (value: string) => void }) {
  const rows = useMemo(() => candleRows(bundle.candles), [bundle.candles]);
  const forming = useMemo(() => formingRow(live, rows), [live, rows]);
  const liveFresh = Boolean(live) && live?.stale === false;
  const lastPrice = Number(live?.last_price);
  const last = forming ?? rows[rows.length - 1];
  const previous = forming ? rows[rows.length - 1] : rows[rows.length - 2];
  const change = last && previous ? ((last.close - previous.close) / previous.close) * 100 : null;
  const priceLine = Number.isFinite(lastPrice) ? lastPrice : null;
  return (
    <>
      <div className="chart-toolbar">
        <label>Timeframe
          <select value={timeframe} onChange={(event) => onTimeframe(event.target.value)}>
            {TIMEFRAMES.map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
        <span className={`live-pill ${liveFresh ? "is-live" : "is-stale"}`}>{liveFresh ? "LIVE" : liveError ? "LIVE FEED DOWN" : "NOT LIVE"}</span>
      </div>
      {last ? (
        <div className="ohlc">
          <span>O <b>{fmtPrice(last.open)}</b></span>
          <span>H <b className="up">{fmtPrice(last.high)}</b></span>
          <span>L <b className="down">{fmtPrice(last.low)}</b></span>
          <span>C <b className={last.close >= last.open ? "up" : "down"}>{fmtPrice(last.close)}</b></span>
          {change !== null ? <span className={change >= 0 ? "up" : "down"}>{change >= 0 ? "+" : ""}{change.toFixed(2)}%</span> : null}
          <span className="muted">{forming ? "forming bar" : "last closed bar"} · {new Date(last.time * 1000).toISOString().slice(0, 16).replace("T", " ")} UTC</span>
        </div>
      ) : null}
      {rows.length === 0 ? <p>No candle rows returned.</p> : <CandleChart rows={rows} forming={forming} lastPrice={priceLine} liveLabel={liveFresh ? "live" : "last seen"} />}
      <p className="muted">
        Green closes above its open, red closes below. Solid candles are completed. The hollow candle is the forming {timeframe} bar built from stored closed minutes plus the socket's forming minute; it changes until the bucket closes.
        {live && live.stale !== false ? " The live feed is stale, so the forming bar and last price are the newest values the backend has seen." : ""}
        {liveError ? ` ${liveError}` : ""}
      </p>
    </>
  );
}

function fmtPrice(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 1, minimumFractionDigits: 1 });
}

function DerivativesCharts({ bundle }: { bundle: Bundle }) {
  const funding = historyPoints(objectOf(objectOf(bundle.derivatives.funding).values).history, "funding_rate");
  const interest = historyPoints(objectOf(objectOf(bundle.derivatives.open_interest).values).history, "contracts");
  const marks = historyPoints(objectOf(objectOf(bundle.derivatives.mark_price).values).history, "mark_price");
  return (
    <>
      <h2>Funding history</h2>
      {funding.length === 0 ? <p>No stored funding history.</p> : <LineChart points={funding} color="#f59e0b" />}
      <h2>Open interest history</h2>
      {interest.length === 0 ? <p>No stored open-interest history.</p> : <LineChart points={interest} color="#60a5fa" />}
      <h2>Mark price history</h2>
      {marks.length === 0 ? <p>No stored mark-price history.</p> : <LineChart points={marks} color="#a78bfa" />}
    </>
  );
}

function Health({ health, diagnostics, snapshot, derivatives }: { health: Record<string, unknown>; diagnostics: Record<string, unknown>; snapshot: Record<string, unknown>; derivatives: Record<string, unknown> }) {
  const database = objectOf(health.database);
  const socket = objectOf(health.websocket);
  const study = objectOf(snapshot.studies);
  return (
    <div className="grid">
      <Field label="Backend" value={text(health.status)} />
      <Field label="PostgreSQL" value={database.connected === true ? "Connected" : "Not connected"} />
      <Field label="Delta REST" value={text(diagnostics.rest_status, text(health.status))} />
      <Field label="WebSocket" value={socket.connected === true ? "Connected" : "Not connected"} />
      <Field label="Latest candle" value={text(health.latest_closed_candle_at)} />
      <Field label="Study watermark" value={text(study.watermark)} />
      <Field label="Study status" value={text(study.status)} />
      <Field label="Derivatives" value={text(derivatives.availability)} />
      <Field label="Missing intervals" value={text(health.recent_missing_candles, "0")} />
      <Field label="Revision" value={text(diagnostics.alembic_revision)} />
    </div>
  );
}

function SettingsForm({ settings, update }: { settings: Settings; update: (patch: Partial<Settings>) => void }) {
  const [base, setBase] = useState(settings.baseUrl);
  const [message, setMessage] = useState("");
  const position: OpenPosition = settings.position ?? { side: "long", entryPrice: 0, size: null };
  const setPosition = (patch: Partial<OpenPosition>) => {
    const next = { ...position, ...patch };
    update({ position: next.entryPrice > 0 ? next : null });
  };
  return (
    <div className="settings">
      <label>Backend URL
        <input value={base} onChange={(event) => setBase(event.target.value)} />
      </label>
      <label>Refresh seconds
        <input type="number" min={10} value={settings.refreshSeconds} onChange={(event) => update({ refreshSeconds: Number(event.target.value) })} />
      </label>
      <h2>Risk inputs for the verdict</h2>
      <div className="grid">
        <label>Account size (quote currency)
          <input type="number" min={0} step="any" value={settings.accountSize ?? ""} placeholder="optional" onChange={(event) => update({ accountSize: Number(event.target.value) > 0 ? Number(event.target.value) : null })} />
        </label>
        <label>Risk per trade %
          <input type="number" min={0.1} max={10} step={0.1} value={settings.riskPercent} onChange={(event) => update({ riskPercent: Number(event.target.value) > 0 ? Number(event.target.value) : 1 })} />
        </label>
      </div>
      <h2>Open position (optional)</h2>
      <div className="grid">
        <label>Side
          <select value={position.side} onChange={(event) => setPosition({ side: event.target.value === "short" ? "short" : "long" })}>
            <option value="long">long</option>
            <option value="short">short</option>
          </select>
        </label>
        <label>Entry price
          <input type="number" min={0} step="any" value={position.entryPrice || ""} placeholder="none" onChange={(event) => setPosition({ entryPrice: Number(event.target.value) || 0 })} />
        </label>
        <label>Size
          <input type="number" min={0} step="any" value={position.size ?? ""} placeholder="optional" onChange={(event) => setPosition({ size: Number(event.target.value) > 0 ? Number(event.target.value) : null })} />
        </label>
      </div>
      {settings.position ? <button type="button" onClick={() => update({ position: null })}>Clear position</button> : null}
      <div className="row">
        <button type="button" onClick={() => {
          try {
            update({ baseUrl: new URL(base).origin });
            setMessage("Saved.");
          } catch {
            setMessage("That URL is not valid.");
          }
        }}>Save URL</button>
        <button type="button" onClick={() => { const next = defaultSettings(); setBase(next.baseUrl); update(next); setMessage("Reset."); }}>Reset</button>
      </div>
      {message ? <p className="muted">{message}</p> : null}
    </div>
  );
}

function Field({ label, value, help }: { label: string; value: string; help?: string }) {
  return (
    <div className="field-box">
      <div className="field-label muted">{help ? <span className="help" title={help}>{label}</span> : label}</div>
      <div className={`field-value ${tone(value)}`}>{value}</div>
    </div>
  );
}

function objectOf(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function formatIndicator(value: unknown): string {
  if (value === null || value === undefined) return "Unavailable";
  if (typeof value === "object") return JSON.stringify(value);
  return text(value);
}
