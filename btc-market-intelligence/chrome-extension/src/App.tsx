import { useEffect, useState, type ReactNode } from "react";
import { defaultSettings, loadBundle, loadSettings, saveSettings, type Settings } from "./api";
import { CandleChart, candleRows, historyPoints, LineChart } from "./charts";
import { HORIZONS, INDICATOR_HELP, TIMEFRAMES, percent, text, tone } from "./format";

type Bundle = Awaited<ReturnType<typeof loadBundle>>;

export function App() {
  const [settings, setSettings] = useState<Settings>(defaultSettings);
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [updated, setUpdated] = useState<string | null>(null);
  const [horizon, setHorizon] = useState("1h");
  const [ready, setReady] = useState(false);
  const [tick, setTick] = useState(0);

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
      {bundle ? <Dashboard bundle={bundle} settings={settings} horizon={horizon} setHorizon={setHorizon} update={update} /> : null}
    </main>
  );
}

function Dashboard({ bundle, settings, horizon, setHorizon, update }: {
  bundle: Bundle;
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
        <Overview snapshot={snapshot} health={health} />
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
      <Section title="Charts" id="charts" settings={settings} update={update}>
        <Charts bundle={bundle} timeframe={settings.timeframe} onTimeframe={(timeframe) => update({ timeframe })} />
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
  return (
    <section className="section">
      <div className="row">
        <h2>{title}</h2>
        <button type="button" onClick={() => update({ collapsed: { ...settings.collapsed, [id]: open } })}>{open ? "Collapse" : "Expand"}</button>
      </div>
      {open ? children : null}
    </section>
  );
}

function Overview({ snapshot, health }: { snapshot: Record<string, unknown>; health: Record<string, unknown> }) {
  const product = objectOf(snapshot.product);
  const close = health.latest_closed_candle_at;
  return (
    <div className="grid">
      <Field label="Symbol" value={text(product.symbol)} />
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

function Charts({ bundle, timeframe, onTimeframe }: { bundle: Bundle; timeframe: string; onTimeframe: (value: string) => void }) {
  const rows = candleRows(bundle.candles);
  const funding = historyPoints(objectOf(objectOf(bundle.derivatives.funding).values).history, "funding_rate");
  const interest = historyPoints(objectOf(objectOf(bundle.derivatives.open_interest).values).history, "contracts");
  const marks = historyPoints(objectOf(objectOf(bundle.derivatives.mark_price).values).history, "mark_price");
  return (
    <>
      <label>Candle timeframe
        <select value={timeframe} onChange={(event) => onTimeframe(event.target.value)}>
          {TIMEFRAMES.map((item) => <option key={item}>{item}</option>)}
        </select>
      </label>
      <p className="muted">Completed candles from the backend. The forming minute is not drawn as a closed bar.</p>
      {rows.length === 0 ? <p>No candle rows returned.</p> : <CandleChart rows={rows} />}
      <h2>Funding history</h2>
      {funding.length === 0 ? <p>No stored funding history.</p> : <LineChart points={funding} />}
      <h2>Open interest history</h2>
      {interest.length === 0 ? <p>No stored open-interest history.</p> : <LineChart points={interest} />}
      <h2>Mark price history</h2>
      {marks.length === 0 ? <p>No stored mark-price history.</p> : <LineChart points={marks} />}
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
  return (
    <div className="settings">
      <label>Backend URL
        <input value={base} onChange={(event) => setBase(event.target.value)} />
      </label>
      <label>Refresh seconds
        <input type="number" min={10} value={settings.refreshSeconds} onChange={(event) => update({ refreshSeconds: Number(event.target.value) })} />
      </label>
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
    <div>
      <div className="muted">{help ? <span className="help" title={help}>{label}</span> : label}</div>
      <div className={tone(value)}>{value}</div>
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
