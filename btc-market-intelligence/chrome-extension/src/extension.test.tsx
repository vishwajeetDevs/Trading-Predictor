import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { getJson, normalizeBase } from "./api";
import { Derivatives } from "./App";

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

describe("manifest", () => {
  it("is an unpacked read-only extension", () => {
    const manifest = JSON.parse(readFileSync(resolve("public/manifest.json"), "utf8"));
    expect(manifest.manifest_version).toBe(3);
    expect(manifest.permissions).toEqual(["sidePanel", "storage"]);
    expect(manifest.host_permissions).toEqual(["http://127.0.0.1:8000/*", "http://localhost:8000/*"]);
    expect(manifest.permissions.join(" ")).not.toMatch(/orders|identity|scripting/);
  });
});
