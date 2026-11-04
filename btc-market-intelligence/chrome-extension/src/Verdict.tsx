import { useState, type ReactNode } from "react";
import { HORIZONS, TIMEFRAMES, text } from "./format";

type Obj = Record<string, unknown>;

export const VERDICT_LABELS: Record<string, string> = {
  ENTER_NOW: "Enter now",
  WAIT_FOR_BETTER_ENTRY: "Wait for better entry",
  AVOID_HIGH_RISK: "Avoid · high risk",
  HOLD: "Hold",
  CONSIDER_EXIT: "Consider exit",
};

const SEQUENCE = ["HTF_BIAS", "LIQUIDITY_IDENTIFIED", "SWEEP", "MSS", "DISPLACEMENT", "IN_ENTRY_ZONE", "CONFLUENCE_CHECKED"];
const SEQUENCE_LABELS: Record<string, string> = {
  HTF_BIAS: "HTF bias",
  LIQUIDITY_IDENTIFIED: "Liquidity",
  SWEEP: "Sweep",
  MSS: "MSS",
  DISPLACEMENT: "Displacement",
  IN_ENTRY_ZONE: "Entry zone",
  CONFLUENCE_CHECKED: "Confluence",
};

const LAYER_TITLES: Record<string, string> = {
  regime: "Regime and higher-timeframe bias",
  liquidity: "Liquidity pools and sweeps",
  support_resistance: "Support, resistance, and levels",
  sessions: "Sessions and killzones",
  momentum_volatility_volume: "Momentum, volatility, volume",
  derivatives: "Derivatives positioning",
  smc_ict: "Smart money concepts / ICT",
  patterns: "Candlestick and chart patterns",
};

export function obj(value: unknown): Obj {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Obj) : {};
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function strings(value: unknown): string[] {
  return list(value).filter((item): item is string => typeof item === "string");
}

export function pct(value: unknown, digits = 0): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${(number * 100).toFixed(digits)}%`;
}

function num(value: unknown, digits = 1): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function VerdictControls({
  timeframe,
  horizon,
  busy,
  onTimeframe,
  onHorizon,
  onAnalyze,
}: {
  timeframe: string;
  horizon: string;
  busy: boolean;
  onTimeframe: (value: string) => void;
  onHorizon: (value: string) => void;
  onAnalyze: () => void;
}) {
  return (
    <div className="verdict-controls">
      <label>Focus timeframe
        <select value={timeframe} onChange={(event) => onTimeframe(event.target.value)} disabled={busy}>
          {TIMEFRAMES.map((item) => <option key={item}>{item}</option>)}
        </select>
      </label>
      <label>Horizon
        <select value={horizon} onChange={(event) => onHorizon(event.target.value)} disabled={busy}>
          {HORIZONS.map((item) => <option key={item}>{item}</option>)}
        </select>
      </label>
      <button type="button" className={`analyze ${busy ? "is-busy" : ""}`} onClick={onAnalyze} disabled={busy} aria-busy={busy}>
        {busy ? "Analyzing…" : "Analyze"}
      </button>
    </div>
  );
}

export function VerdictView({ body }: { body: Obj }) {
  const decision = obj(body.verdict);
  const probabilities = obj(body.probabilities);
  const plan = obj(body.trade_plan);
  const sequence = obj(body.sequence);
  const read = obj(body.market_read);
  const htf = obj(body.htf_bias);
  const code = text(decision.verdict, "UNKNOWN");
  const layers = list(body.layers).map(obj);
  const freshness = obj(body.data_freshness);
  const components = obj(probabilities.confidence_components);
  const stale = Boolean(freshness.stale);
  return (
    <div className="verdict">
      <div className={`verdict-badge verdict-${code.toLowerCase()}`} role="status">
        <span className="verdict-code">{VERDICT_LABELS[code] ?? code}</span>
        <span className="verdict-sub">
          {text(read.lean, "—")} · {text(body.regime, "—").replace(/_/g, " ")} · confidence {pct(body.confidence)}
          {body.cached === true ? " · cached" : ""}
        </span>
      </div>
      {stale ? <p className="warn">Market data is stale. The verdict reflects the last closed candles, not the live tape.</p> : null}
      <p className="verdict-sentence">{text(read.sentence, "")}</p>

      <div className="prob-bars">
        <ProbBar label="Bullish" value={probabilities.p_bull} kind="bull" />
        <ProbBar label="Bearish" value={probabilities.p_bear} kind="bear" />
        <ProbBar label="Range" value={probabilities.p_range} kind="range" />
      </div>
      <div className="grid">
        <Mini label="Confidence" value={pct(body.confidence)} />
        <Mini label="Agreement" value={pct(components.agreement)} />
        <Mini label="Layers OK" value={pct(components.sufficiency)} />
        <Mini label="Freshness" value={pct(components.freshness)} />
        <Mini label="HTF bias" value={`${text(htf.bias, "—")}${htf.conflict ? " (conflict)" : ""}`} />
        <Mini label="Horizon" value={`${text(body.focus_timeframe, "—")} → ${text(body.horizon, "—")}`} />
      </div>
      <HorizonRow by={obj(probabilities.by_horizon)} />

      <Stepper sequence={sequence} />

      <Nested title="Why this verdict" defaultOpen>
        <Bullets items={strings(decision.reasons)} empty="No reasons were produced." />
      </Nested>
      <Nested title="Risks">
        <Bullets items={strings(decision.risks)} empty="No elevated risk flags." />
      </Nested>
      <Nested title="What changes this view">
        <Bullets items={strings(decision.what_changes_this_view)} empty="—" />
      </Nested>
      <Nested title="Bullish evidence">
        <Evidence items={list(body.why_bullish).map(obj)} />
      </Nested>
      <Nested title="Bearish evidence">
        <Evidence items={list(body.why_bearish).map(obj)} />
      </Nested>
      <Nested title="Trade plan">
        <TradePlan plan={plan} />
      </Nested>
      <Nested title="Layer details">
        {layers.map((layer) => (
          <Nested key={text(layer.name)} title={`${LAYER_TITLES[text(layer.name)] ?? text(layer.name)} · ${text(layer.status)}`} small>
            <p className="muted">{text(layer.summary, "")} {layer.elapsed_ms !== undefined ? `(${num(layer.elapsed_ms, 0)} ms)` : ""}</p>
            {strings(layer.warnings).length ? <Bullets items={strings(layer.warnings)} empty="" /> : null}
            <Evidence items={list(layer.evidence).map(obj)} compact />
          </Nested>
        ))}
      </Nested>
      <p className="muted disclaimer">{text(body.disclaimer, "")}</p>
    </div>
  );
}

function ProbBar({ label, value, kind }: { label: string; value: unknown; kind: "bull" | "bear" | "range" }) {
  const number = Number(value);
  const width = Number.isFinite(number) ? Math.max(0, Math.min(100, number * 100)) : 0;
  return (
    <div className={`prob-bar prob-${kind}`}>
      <div className="prob-head"><span>{label}</span><span className="prob-value">{pct(value)}</span></div>
      <div className="prob-track"><div className="prob-fill" style={{ width: `${width}%` }} /></div>
    </div>
  );
}

function HorizonRow({ by }: { by: Obj }) {
  const keys = Object.keys(by);
  if (keys.length === 0) return null;
  return (
    <table className="horizon-table">
      <thead><tr><th>Horizon</th><th>Bull</th><th>Bear</th><th>Range</th></tr></thead>
      <tbody>
        {keys.map((key) => {
          const row = obj(by[key]);
          return <tr key={key}><td>{key}</td><td>{pct(row.p_bull)}</td><td>{pct(row.p_bear)}</td><td>{pct(row.p_range)}</td></tr>;
        })}
      </tbody>
    </table>
  );
}

function Stepper({ sequence }: { sequence: Obj }) {
  const reached = new Set(strings(sequence.reached));
  const stage = typeof sequence.stage === "string" ? sequence.stage : null;
  const direction = text(sequence.direction, "none");
  return (
    <div className="stepper" aria-label="Playbook sequence">
      <div className="stepper-head">
        <span>Playbook</span>
        <span className="muted">{stage ? `${stage.replace(/_/g, " ")} · ${direction}` : text(sequence.note, "Not armed")}</span>
      </div>
      <ol className="steps">
        {SEQUENCE.map((name) => {
          const done = reached.has(name);
          const current = stage === name;
          return (
            <li key={name} className={`step ${done ? "is-done" : ""} ${current ? "is-current" : ""}`}>
              <span className="step-dot" aria-hidden="true" />
              <span className="step-label">{SEQUENCE_LABELS[name]}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function TradePlan({ plan }: { plan: Obj }) {
  if (plan.status === "NO_DIRECTION") return <p className="muted">{text(plan.note, "No directional plan.")}</p>;
  const targets = list(plan.targets).map(obj);
  const sizing = obj(plan.sizing);
  return (
    <>
      <p className="muted">{text(plan.entry_note, "")}</p>
      <div className="grid">
        <Mini label="Direction" value={text(plan.direction, "—")} />
        <Mini label="Entry" value={num(plan.entry, 1)} />
        <Mini label="Invalidation" value={num(plan.invalidation, 1)} />
        <Mini label="Alt. stop (1.5 ATR)" value={num(plan.alternative_invalidation, 1)} />
        <Mini label="R:R to T1" value={num(plan.risk_reward_first_target, 2)} />
        <Mini label="EV (R)" value={num(plan.expected_value_r, 2)} />
        <Mini label="Quarter Kelly" value={pct(plan.quarter_kelly_fraction, 1)} />
        <Mini label="ATR" value={num(plan.atr, 1)} />
      </div>
      <p className="muted">{text(plan.invalidation_note, "")} {text(plan.time_stop, "")}</p>
      {targets.length ? (
        <table>
          <thead><tr><th>Target</th><th>Price</th><th>R</th></tr></thead>
          <tbody>
            {targets.map((target, index) => (
              <tr key={index}><td>T{index + 1}{target.synthetic ? " (synthetic)" : ""}</td><td>{num(target.price, 1)}</td><td>{num(target.r_multiple, 2)}</td></tr>
            ))}
          </tbody>
        </table>
      ) : <p className="muted">No mapped target on the opposite side.</p>}
      {Object.keys(sizing).length ? (
        <div className="grid">
          <Mini label="Risk amount" value={num(sizing.risk_amount, 2)} />
          <Mini label="Risk / unit" value={num(sizing.risk_per_unit, 2)} />
          <Mini label="Base units" value={num(sizing.base_asset_units, 6)} />
          <Mini label="Contracts" value={text(sizing.contracts, "—")} />
        </div>
      ) : <p className="muted">Set an account size in Settings to see position sizing.</p>}
    </>
  );
}

function Evidence({ items, compact = false }: { items: Obj[]; compact?: boolean }) {
  if (items.length === 0) return <p className="muted">None.</p>;
  return (
    <ul className={`evidence ${compact ? "is-compact" : ""}`}>
      {items.map((item, index) => {
        const contribution = Number(item.contribution);
        const direction = Number(item.direction);
        const signed = Number.isFinite(contribution) ? contribution : Number.isFinite(direction) ? direction * Number(item.strength ?? 0) : 0;
        const sign = signed > 0 ? "+" : signed < 0 ? "−" : "·";
        return (
          <li key={index} className={`evidence-item ${signed > 0 ? "is-bull" : signed < 0 ? "is-bear" : ""}`}>
            <span className="evidence-sign" aria-hidden="true">{sign}</span>
            <span className="evidence-body">
              <span className="evidence-name">{text(item.name, "").replace(/_/g, " ")}{item.timeframe ? ` · ${text(item.timeframe)}` : ""}</span>
              <span className="evidence-text">{text(item.rationale, "")}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function Bullets({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return empty ? <p className="muted">{empty}</p> : null;
  return <ul className="bullets">{items.map((item, index) => <li key={index}>{item}</li>)}</ul>;
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="field-box">
      <div className="field-label muted">{label}</div>
      <div className="field-value">{value}</div>
    </div>
  );
}

export function Nested({ title, children, defaultOpen = false, small = false }: { title: string; children: ReactNode; defaultOpen?: boolean; small?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`nested dropdown ${open ? "is-open" : "is-closed"} ${small ? "is-small" : ""}`}>
      <button type="button" className="dropdown-trigger nested-trigger" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <span className="nested-title">{title}</span>
        <span className="dropdown-chevron" aria-hidden="true">▾</span>
      </button>
      <div className="dropdown-panel" aria-hidden={!open}>
        <div className="dropdown-inner nested-inner">{children}</div>
      </div>
    </div>
  );
}
