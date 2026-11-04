import { Nested, obj, pct } from "./Verdict";
import { text } from "./format";

type Obj = Record<string, unknown>;

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function strings(value: unknown): string[] {
  return list(value).filter((item): item is string => typeof item === "string");
}

function price(value: unknown): string {
  const number = Number(value);
  if (value === null || value === undefined || !Number.isFinite(number)) return "—";
  return number.toLocaleString(undefined, { maximumFractionDigits: 1, minimumFractionDigits: 1 });
}

function ratio(value: unknown): string {
  const number = Number(value);
  if (value === null || value === undefined || !Number.isFinite(number)) return "—";
  return `${number.toFixed(2)} : 1`;
}

export function decisionTone(action: string, direction: string): "bull" | "bear" | "wait" | "neutral" {
  if (action === "TAKE_TRADE" || action === "HOLD") return direction === "BULLISH" ? "bull" : direction === "BEARISH" ? "bear" : "neutral";
  if (action === "CONSIDER_EXIT") return "bear";
  return "wait";
}

export function DecisionCard({ decision, computedAt, watermark, cached, refreshing = false }: { decision: Obj; computedAt: string | null; watermark: string | null; cached: boolean; refreshing?: boolean }) {
  const action = text(decision.action, "WAIT");
  const direction = text(decision.direction, "NONE");
  const tone = decisionTone(action, direction);
  const targets = list(decision.take_profit).map(obj);
  const checklist = list(decision.checklist).map(obj);
  const indicators = obj(decision.indicators);
  const votes = list(indicators.votes).map(obj);
  const sizing = obj(decision.sizing);
  const hasLevels = decision.entry !== null && decision.entry !== undefined;
  const actionLabel = action === "TAKE_TRADE" ? "TAKE TRADE" : action === "CONSIDER_EXIT" ? "CONSIDER EXIT" : action;
  return (
    <div className={`decision decision-${tone}`} role="status" aria-live="polite">
      <div className="decision-head">
        <div>
          <div className="decision-action">{actionLabel}</div>
          <div className="decision-direction">{text(decision.headline, "")}</div>
        </div>
        <div className="decision-confidence">
          <span className="field-label muted">Confidence</span>
          <span className={`decision-confidence-value conf-${text(decision.confidence_label, "LOW").toLowerCase()}`}>{pct(decision.confidence)} · {text(decision.confidence_label, "—").replace("_", " ")}</span>
        </div>
      </div>
      <p className="muted">
        Rule-based on closed candles{watermark ? ` through ${watermark}` : ""}{computedAt ? ` · computed ${computedAt}` : ""}{cached ? " · cached" : ""}. Updates when a new candle closes.{refreshing ? " A new candle closed; recalculating…" : ""}
      </p>
      {hasLevels ? (
        <div className="levels">
          <Level label="Entry" value={price(decision.entry)} note={text(decision.entry_note, "")} />
          <Level label="Stop loss" value={price(decision.stop_loss)} note={text(decision.stop_loss_note, "")} tone="bear" />
          {targets.map((target, index) => (
            <Level key={index} label={`Target ${index + 1}${target.synthetic ? " (synthetic)" : ""}`} value={price(target.price)} note={`${Number(target.r_multiple ?? 0).toFixed(2)} R`} tone="bull" />
          ))}
          <Level label="Reward : risk (T1)" value={ratio(decision.risk_reward)} note={decision.expected_value_r !== null && decision.expected_value_r !== undefined ? `EV ${Number(decision.expected_value_r).toFixed(2)} R` : ""} />
          {Object.keys(sizing).length ? <Level label="Size (informational)" value={text(sizing.contracts, "—")} note={`contracts · risk ${price(sizing.risk_amount)}`} /> : null}
        </div>
      ) : (
        <p className="decision-wait">No entry, stop, or target is shown because the rules did not pass. Levels appear only with a TAKE TRADE decision.</p>
      )}
      {decision.time_stop ? <p className="muted">{text(decision.time_stop, "")}</p> : null}

      <Nested title="Why" defaultOpen>
        <ul className="bullets">{strings(decision.explanation).map((line, index) => <li key={index}>{line}</li>)}</ul>
      </Nested>
      <Nested title={`Rule checklist · ${checklist.filter((item) => item.passed === true).length}/${checklist.length} passed`}>
        <table className="checklist">
          <thead><tr><th></th><th>Rule</th><th>Value</th><th>Threshold</th></tr></thead>
          <tbody>
            {checklist.map((item, index) => {
              const state = item.passed === true ? "pass" : item.passed === false ? "fail" : "unknown";
              return (
                <tr key={index} className={`check-${state}`} title={text(item.detail, "")}>
                  <td className="check-mark">{state === "pass" ? "✓" : state === "fail" ? "✕" : "·"}</td>
                  <td>{text(item.rule, "")}</td>
                  <td>{valueText(item.value)}</td>
                  <td className="muted">{text(item.threshold, "")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Nested>
      <Nested title={`Indicator votes · ${text(indicators.agree, "0")} agree / ${text(indicators.disagree, "0")} against / ${text(indicators.neutral, "0")} neutral`}>
        <p className="muted">{text(indicators.note, "")}</p>
        {votes.length === 0 ? <p className="muted">No indicator values on the focus timeframe.</p> : (
          <table className="checklist">
            <thead><tr><th></th><th>Indicator</th><th>Value</th><th>Reading</th></tr></thead>
            <tbody>
              {votes.map((vote, index) => {
                const state = text(vote.state, "unavailable");
                return (
                  <tr key={index} className={`vote-${state}`}>
                    <td className="check-mark">{state === "agree" ? "▲" : state === "disagree" ? "▼" : "·"}</td>
                    <td>{text(vote.indicator, "")}</td>
                    <td>{valueText(vote.value)}</td>
                    <td className="muted">{text(vote.reading, "")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Nested>
      <Nested title="Risk rules">
        <ul className="bullets">{strings(decision.risk_rules).map((line, index) => <li key={index}>{line}</li>)}</ul>
      </Nested>
      <p className="muted disclaimer">{text(decision.note, "")} {text(decision.disclaimer, "")}</p>
    </div>
  );
}

function valueText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") {
    return Object.entries(value as Obj).map(([key, item]) => `${key} ${price(item)}`).join(" · ");
  }
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(Math.abs(value) < 1 ? 4 : 2);
  return String(value);
}

function Level({ label, value, note, tone }: { label: string; value: string; note: string; tone?: "bull" | "bear" }) {
  return (
    <div className={`level ${tone ? `level-${tone}` : ""}`}>
      <div className="field-label muted">{label}</div>
      <div className="level-value">{value}</div>
      {note ? <div className="level-note muted">{note}</div> : null}
    </div>
  );
}
