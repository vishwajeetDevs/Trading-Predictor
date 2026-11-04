import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { defaultSettings, getJson, normalizeBase, postJson, verdictRequestFrom } from "./api";
import { Derivatives } from "./App";
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

describe("manifest", () => {
  it("is an unpacked read-only extension", () => {
    const manifest = JSON.parse(readFileSync(resolve("public/manifest.json"), "utf8"));
    expect(manifest.manifest_version).toBe(3);
    expect(manifest.permissions).toEqual(["sidePanel", "storage"]);
    expect(manifest.host_permissions).toEqual(["http://127.0.0.1:8000/*", "http://localhost:8000/*"]);
    expect(manifest.permissions.join(" ")).not.toMatch(/orders|identity|scripting/);
  });
});
