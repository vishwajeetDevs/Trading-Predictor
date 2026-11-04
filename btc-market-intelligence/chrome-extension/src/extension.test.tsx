import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CHART_CANDLES, defaultSettings, getJson, loadBundle, normalizeBase, postJson, verdictRequestFrom } from "./api";
import { Derivatives } from "./App";
import { candleRows, formingRow } from "./charts";
import { DecisionCard, decisionTone } from "./Decision";
import { VerdictView } from "./Verdict";

describe("backend url", () => {
  it("rejects credentials and non-http schemes", () => {
    expect(normalizeBase("http://127.0.0.1:8000/")).toBe("http://127.0.0.1:8000");
    expect(() => normalizeBase("http://user:secret@127.0.0.1:8000")).toThrow(/credentials/);
    expect(() => normalizeBase("file:///tmp")).toThrow(/http/);
  });

  it("reports a down backend without inventing data", async () => {
    const fetcher = async () => {
      throw new Error("offline");
    };
    await expect(getJson("http://127.0.0.1:8000", "/health", fetcher as typeof fetch)).rejects.toThrow(/unreachable/);
  });

  it("rejects a non-object payload", async () => {
    const fetcher = async () => new Response("[]", { status: 200, headers: { "content-type": "application/json" } });
    await expect(getJson("http://127.0.0.1:8000", "/health", fetcher as typeof fetch)).rejects.toThrow(/not an object/);
  });
});

describe("derivatives presentation", () => {
  it("shows an empty collection instead of zeros", () => {
    render(<Derivatives body={{ availability: "NOT_COLLECTED" }} />);
    expect(screen.getByText(/not been collected/i)).toBeTruthy();
    expect(screen.queryByText("0")).toBeNull();
  });

  it("marks a stale snapshot", () => {
    render(<Derivatives body={{
      availability: "AVAILABLE",
      stale: true,
      funding: { values: { funding_rate: "0.0002", classification: { sign: "positive" } } },
      open_interest: { values: { contracts: "10", exchange_change_6h: null } },
      mark_price: { values: { mark_price: "100", last_price: "101", index_price: null } },
      orderbook: { values: { spread: "0.5", imbalance: "0.1" } },
    }} />);
    expect(screen.getByText("Stale")).toBeTruthy();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
  });
});

describe("verdict", () => {
  it("builds a request from settings and drops an empty position", () => {
    const base = defaultSettings();
    expect(verdictRequestFrom(base)).toEqual({ focus_timeframe: "15m", horizon: "4h", position: null, account_size: null, risk_percent: 1, force_refresh: false });
    const withPosition = verdictRequestFrom({ ...base, accountSize: 5000, riskPercent: 0.5, position: { side: "short", entryPrice: 80000, size: null } }, true);
    expect(withPosition.position).toEqual({ side: "short", entry_price: 80000 });
    expect(withPosition.account_size).toBe(5000);
    expect(withPosition.force_refresh).toBe(true);
  });

  it("posts JSON and surfaces the backend detail on failure", async () => {
    const calls: RequestInit[] = [];
    const ok = async (_url: string, init?: RequestInit) => {
      calls.push(init ?? {});
      return new Response(JSON.stringify({ verdict: { verdict: "HOLD" } }), { status: 200, headers: { "content-type": "application/json" } });
    };
    const body = await postJson("http://127.0.0.1:8000", "/analysis/verdict", { horizon: "4h" }, ok as unknown as typeof fetch);
    expect(body.verdict).toEqual({ verdict: "HOLD" });
    expect(calls[0].method).toBe("POST");
    const failing = async () => new Response(JSON.stringify({ detail: "BTC perpetual is not in the database yet." }), { status: 404 });
    await expect(postJson("http://127.0.0.1:8000", "/analysis/verdict", {}, failing as unknown as typeof fetch)).rejects.toThrow(/404.*not in the database/);
  });

  it("renders the verdict, probabilities, sequence, and disclaimer", () => {
    render(<VerdictView body={{
      verdict: { verdict: "WAIT_FOR_BETTER_ENTRY", reasons: ["Bias is bullish but the playbook is at SWEEP."], risks: ["Daily and 4h structure labels disagree."], what_changes_this_view: ["A retrace into a fresh bullish FVG."] },
      probabilities: { p_bull: 0.52, p_bear: 0.28, p_range: 0.2, confidence_components: { agreement: 0.6, sufficiency: 1, freshness: 1 }, by_horizon: { "4h": { p_bull: 0.52, p_bear: 0.28, p_range: 0.2 } } },
      confidence: 0.6,
      regime: "trend_up",
      market_read: { lean: "BULLISH", sentence: "Market reads as moderate bullish lean." },
      sequence: { stage: "SWEEP", direction: "long", reached: ["HTF_BIAS", "LIQUIDITY_IDENTIFIED", "SWEEP"] },
      trade_plan: { status: "NO_DIRECTION", note: "No directional plan." },
      layers: [{ name: "regime", status: "OK", summary: "Bias bullish.", evidence: [], warnings: [], elapsed_ms: 3 }],
      data_freshness: { stale: true },
      disclaimer: "Probabilities, not guarantees.",
    }} />);
    expect(screen.getByText("Wait for better entry")).toBeTruthy();
    expect(screen.getAllByText("52%").length).toBe(2);
    expect(screen.getAllByText("Sweep").length).toBeGreaterThan(0);
    expect(screen.getByText(/data is stale/i)).toBeTruthy();
    expect(screen.getByText("Probabilities, not guarantees.")).toBeTruthy();
    expect(screen.getByText(/playbook is at SWEEP/)).toBeTruthy();
  });
});

describe("live chart data", () => {
  it("requests the newest candles and draws them oldest first", async () => {
    const urls: string[] = [];
    const fetcher = async (url: string) => {
      urls.push(url);
      const body = url.includes("/candles")
        ? { candles: [
            { timestamp: "2026-11-04T18:30:00+00:00", open: "3", high: "4", low: "2", close: "3.5", volume: "1", is_closed: true },
            { timestamp: "2026-11-04T18:15:00+00:00", open: "1", high: "2", low: "0.5", close: "1.5", volume: "1", is_closed: true },
            { timestamp: "2026-11-04T18:45:00+00:00", open: "5", high: "6", low: "4", close: "5.5", volume: "1", is_closed: false },
          ] }
        : {};
      return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    };
    const bundle = await loadBundle("http://127.0.0.1:8000", "15m", fetcher as typeof fetch);
    const candleUrl = urls.find((url) => url.includes("/candles")) ?? "";
    expect(candleUrl).toContain("order=desc");
    expect(candleUrl).toContain(`limit=${CHART_CANDLES}`);
    const rows = candleRows(bundle.candles);
    expect(rows.map((row) => row.close)).toEqual([1.5, 3.5]);
    expect(rows.every((row) => row.closed)).toBe(true);
  });

  it("only appends a forming bar that is newer than the last closed bar", () => {
    const closed = candleRows({ candles: [{ timestamp: "2026-11-04T18:30:00+00:00", open: "3", high: "4", low: "2", close: "3.5", volume: "1", is_closed: true }] });
    const older = formingRow({ forming_candle: { open_time: "2026-11-04T18:30:00+00:00", open: "3", high: "4", low: "2", close: "3.5", volume: "1", is_closed: false } }, closed);
    expect(older).toBeNull();
    const newer = formingRow({ forming_candle: { open_time: "2026-11-04T18:45:00+00:00", open: "3.5", high: "4.2", low: "3.4", close: "4.1", volume: "0.3", is_closed: false } }, closed);
    expect(newer?.closed).toBe(false);
    expect(newer?.close).toBe(4.1);
    expect(formingRow(null, closed)).toBeNull();
  });
});

describe("trade decision", () => {
  it("shows TAKE TRADE with levels for a bullish decision", () => {
    render(<DecisionCard cached={false} computedAt="2026-11-04T18:30:00Z" watermark="2026-11-04T18:29:00Z" decision={{
      action: "TAKE_TRADE", direction: "BULLISH", side: "BUY", headline: "BULLISH (BUY)",
      entry: 80100.5, entry_note: "Midpoint of the bullish fvg.", stop_loss: 79600, stop_loss_note: "Structural.",
      take_profit: [{ price: 81200, r_multiple: 2.2, synthetic: false }], risk_reward: 2.2, expected_value_r: 0.9,
      confidence: 0.63, confidence_label: "MEDIUM",
      explanation: ["All 9 entry rules passed on the 15m closed bars."],
      checklist: [{ rule: "Confidence", passed: true, value: 0.63, threshold: ">= 0.50", detail: "" }],
      indicators: { agree: 6, disagree: 0, neutral: 1, votes: [{ indicator: "RSI(14)", value: 58.2, reading: "above 50", state: "agree" }], note: "votes" },
      risk_rules: ["No trade unless every rule passes."], note: "No AI.", disclaimer: "Not advice.",
    }} />);
    expect(screen.getByText("TAKE TRADE")).toBeTruthy();
    expect(screen.getByText("BULLISH (BUY)")).toBeTruthy();
    expect(screen.getByText("Entry")).toBeTruthy();
    expect(screen.getByText("Stop loss")).toBeTruthy();
    expect(screen.getByText(/2\.20 : 1/)).toBeTruthy();
    expect(screen.getByText(/All 9 entry rules passed/)).toBeTruthy();
    expect(decisionTone("TAKE_TRADE", "BULLISH")).toBe("bull");
    expect(decisionTone("TAKE_TRADE", "BEARISH")).toBe("bear");
  });

  it("hides levels on WAIT and explains why", () => {
    render(<DecisionCard cached={true} computedAt={null} watermark={null} decision={{
      action: "WAIT", direction: "NONE", side: null, headline: "WAIT · lean bearish",
      entry: null, stop_loss: null, take_profit: [], risk_reward: null,
      confidence: 0.41, confidence_label: "LOW",
      explanation: ["The setup is incomplete. Rules not yet satisfied: Playbook reached the entry zone."],
      checklist: [{ rule: "Playbook reached the entry zone", passed: false, value: "SWEEP", threshold: "IN_ENTRY_ZONE or later", detail: "" }],
      indicators: { agree: 2, disagree: 3, neutral: 1, votes: [], note: "" },
      risk_rules: [], note: "", disclaimer: "",
    }} />);
    expect(screen.getByText("WAIT")).toBeTruthy();
    expect(screen.queryByText("Entry")).toBeNull();
    expect(screen.getByText(/Levels appear only with a TAKE TRADE decision/)).toBeTruthy();
    expect(screen.getByText(/Rules not yet satisfied/)).toBeTruthy();
    expect(decisionTone("WAIT", "NONE")).toBe("wait");
  });
});

describe("manifest", () => {
  it("is an unpacked read-only extension", () => {
    const manifest = JSON.parse(readFileSync(resolve("public/manifest.json"), "utf8"));
    expect(manifest.manifest_version).toBe(3);
    expect(manifest.permissions).toEqual(["sidePanel", "storage"]);
    expect(manifest.host_permissions).toEqual(["http://127.0.0.1:8000/*", "http://localhost:8000/*"]);
    expect(manifest.permissions.join(" ")).not.toMatch(/orders|identity|scripting/);
  });
});
