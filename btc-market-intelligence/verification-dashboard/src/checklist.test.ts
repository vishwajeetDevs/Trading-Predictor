import { describe, expect, it } from "vitest";

import { buildChecklist, priceCaption, type Snapshot } from "./checklist";

const empty: Snapshot = {
  reachable: true,
  databaseConnected: true,
  revision: "0001_initial",
  tablesOk: true,
  productSymbol: null,
  productContract: null,
  candleTotal: 0,
  minuteCount: 0,
  websocketConnected: false,
  duplicateConstraint: true,
  endpointsOk: true,
  recoverySucceeded: 0,
  missingIntervals: 0,
};

describe("verification checklist", () => {
  it("waits for the first response before calling the API down", () => {
    const items = buildChecklist({ ...empty, reachable: false }, true);
    expect(items.every((item) => item.state === "NOT VERIFIED")).toBe(true);
    expect(JSON.stringify(items)).not.toContain("BTCUSD");
  });

  it("marks the backend failed and the rest blocked when the API is down", () => {
    const items = buildChecklist({ ...empty, reachable: false });
    expect(items[0]).toMatchObject({ id: "backend", state: "FAIL" });
    expect(items.filter((item) => item.id !== "backend" && item.id !== "recovery").every((item) => item.state === "BLOCKED")).toBe(true);
    expect(JSON.stringify(items)).not.toContain("BTCUSD");
    expect(JSON.stringify(items)).not.toContain("77000");
  });

  it("does not treat an empty database as market data", () => {
    const items = buildChecklist(empty);
    expect(items.find((item) => item.id === "product")?.state).toBe("FAIL");
    expect(items.find((item) => item.id === "history")?.state).toBe("FAIL");
    expect(items.find((item) => item.id === "socket")?.state).toBe("FAIL");
    expect(items.find((item) => item.id === "recovery")?.state).toBe("NOT VERIFIED");
    expect(priceCaption(null)).toBe("No stored candle returned");
  });

  it("passes only the checks the payload actually supports", () => {
    const items = buildChecklist({
      ...empty,
      productSymbol: "BTCUSD",
      productContract: "perpetual_futures",
      candleTotal: 10,
      minuteCount: 8,
      websocketConnected: true,
    });
    expect(items.find((item) => item.id === "socket")?.state).toBe("PASS");
    expect(items.find((item) => item.id === "history")?.state).toBe("PASS");
    expect(items.find((item) => item.id === "recovery")?.state).toBe("NOT VERIFIED");
    const recovered = buildChecklist({ ...empty, productSymbol: "BTCUSD", productContract: "perpetual_futures", candleTotal: 10, minuteCount: 8, websocketConnected: true, recoverySucceeded: 1, missingIntervals: 0 });
    expect(recovered.find((item) => item.id === "recovery")?.state).toBe("PASS");
    const openHole = buildChecklist({ ...empty, missingIntervals: 9, recoverySucceeded: 0 });
    expect(openHole.find((item) => item.id === "recovery")?.state).toBe("FAIL");
    expect(buildChecklist({ ...empty, revision: "0002_analysis" }).find((item) => item.id === "migrations")?.state).toBe("PASS");
    expect(buildChecklist({ ...empty, revision: "0003_sampling" }).find((item) => item.id === "migrations")?.state).toBe("PASS");
    expect(buildChecklist({ ...empty, revision: "0004_derivatives" }).find((item) => item.id === "migrations")?.state).toBe("PASS");
    expect(buildChecklist({ ...empty, revision: "0005_verdict_runs" }).find((item) => item.id === "migrations")?.state).toBe("PASS");
    expect(priceCaption({ is_closed: true })).toBe("Latest completed close");
    expect(priceCaption({ is_closed: false })).not.toMatch(/live price/i);
  });
});
