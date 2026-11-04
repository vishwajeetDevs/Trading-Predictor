export type Verdict = "PASS" | "FAIL" | "BLOCKED" | "NOT VERIFIED";

export interface Check {
  id: string;
  label: string;
  state: Verdict;
  evidence: string;
}

export interface Snapshot {
  reachable: boolean;
  databaseConnected: boolean | null;
  revision: string | null;
  tablesOk: boolean | null;
  productSymbol: string | null;
  productContract: string | null;
  candleTotal: number | null;
  minuteCount: number | null;
  websocketConnected: boolean | null;
  duplicateConstraint: boolean | null;
  endpointsOk: boolean | null;
  recoverySucceeded: number | null;
  missingIntervals: number | null;
}

export const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;

export function priceCaption(candle: { is_closed: boolean } | null): string {
  if (candle === null) return "No stored candle returned";
  if (candle.is_closed) return "Latest completed close";
  return "Latest stored close, candle still forming";
}

export function buildChecklist(snapshot: Snapshot, pending = false): Check[] {
  if (pending) {
    const waiting = "The first request has not finished.";
    return [
      unverified("backend", "Backend startup", waiting),
      unverified("postgres", "PostgreSQL connectivity", waiting),
      unverified("migrations", "Database migrations", waiting),
      unverified("product", "Product discovery", waiting),
      unverified("history", "Historical candle persistence", waiting),
      unverified("socket", "Live WebSocket", waiting),
      unverified("persist", "Candle persistence", waiting),
      unverified("unique", "Duplicate prevention", waiting),
      unverified("recovery", "REST gap recovery", "Recovery is not replayed by this screen."),
      unverified("api", "API responses", waiting),
    ];
  }
  if (!snapshot.reachable) {
    return [
      fail("backend", "Backend startup", "The dashboard could not reach the API."),
      blocked("postgres", "PostgreSQL connectivity"),
      blocked("migrations", "Database migrations"),
      blocked("product", "Product discovery"),
      blocked("history", "Historical candle persistence"),
      blocked("socket", "Live WebSocket"),
      blocked("persist", "Candle persistence"),
      blocked("unique", "Duplicate prevention"),
      unverified("recovery", "REST gap recovery", "Recovery is not replayed by this screen."),
      blocked("api", "API responses"),
    ];
  }
  return [
    pass("backend", "Backend startup", "The health endpoint answered."),
    snapshot.databaseConnected === true
      ? pass("postgres", "PostgreSQL connectivity", "Health reports database.connected true.")
      : snapshot.databaseConnected === false
        ? fail("postgres", "PostgreSQL connectivity", "Health reports the database is not connected.")
        : unverified("postgres", "PostgreSQL connectivity", "Health did not include a database connection flag."),
    migrationState(snapshot),
    snapshot.productSymbol && snapshot.productContract === "perpetual_futures"
      ? pass("product", "Product discovery", `${snapshot.productSymbol} perpetual_futures was returned.`)
      : snapshot.productSymbol === null && snapshot.productContract === null
        ? fail("product", "Product discovery", "The BTC product endpoint did not return a perpetual.")
        : fail("product", "Product discovery", "The BTC product endpoint did not return a perpetual."),
    snapshot.minuteCount === null
      ? unverified("history", "Historical candle persistence", "Candle counts were not returned.")
      : snapshot.minuteCount > 0
        ? pass("history", "Historical candle persistence", `${snapshot.minuteCount} one-minute candles are stored.`)
        : fail("history", "Historical candle persistence", "The database reports no one-minute candles."),
    snapshot.websocketConnected === true
      ? pass("socket", "Live WebSocket", "The backend reports the public socket is connected.")
      : snapshot.websocketConnected === false
        ? fail("socket", "Live WebSocket", "The backend reports the public socket is not connected.")
        : unverified("socket", "Live WebSocket", "The backend did not report socket state."),
    snapshot.candleTotal === null
      ? unverified("persist", "Candle persistence", "A candle count was not returned.")
      : snapshot.candleTotal > 0
        ? pass("persist", "Candle persistence", `${snapshot.candleTotal} candle rows are stored.`)
        : fail("persist", "Candle persistence", "The database reports zero candle rows."),
    snapshot.duplicateConstraint === true
      ? pass("unique", "Duplicate prevention", "The candles table reports unique constraint uq_candle_slot.")
      : snapshot.duplicateConstraint === false
        ? fail("unique", "Duplicate prevention", "uq_candle_slot was not reported on the candles table.")
        : unverified("unique", "Duplicate prevention", "Constraint status was not returned."),
    recoveryState(snapshot),
    snapshot.endpointsOk === true
      ? pass("api", "API responses", "The four read endpoints returned HTTP 200 on the last check.")
      : snapshot.endpointsOk === false
        ? fail("api", "API responses", "At least one read endpoint failed on the last check.")
        : unverified("api", "API responses", "The read endpoints have not been checked yet."),
  ];
}

function recoveryState(snapshot: Snapshot): Check {
  if (snapshot.missingIntervals === null || snapshot.recoverySucceeded === null) {
    return unverified("recovery", "REST gap recovery", "Recovery counts were not returned.");
  }
  if (snapshot.missingIntervals > 0) {
    return fail("recovery", "REST gap recovery", `${snapshot.missingIntervals} closed one-minute intervals are still missing.`);
  }
  if (snapshot.recoverySucceeded > 0) {
    return pass("recovery", "REST gap recovery", "A REST recovery succeeded and the recent closed series has no gap.");
  }
  return unverified("recovery", "REST gap recovery", "No successful recovery has been recorded in this process.");
}

function migrationState(snapshot: Snapshot): Check {
  if (snapshot.revision === null || snapshot.tablesOk === null) {
    return unverified("migrations", "Database migrations", "The diagnostics payload did not include a revision.");
  }
  if (
    (snapshot.revision === "0001_initial" || snapshot.revision === "0002_analysis" || snapshot.revision === "0003_sampling" || snapshot.revision === "0004_derivatives" || snapshot.revision === "0005_verdict_runs") &&
    snapshot.tablesOk
  ) {
    return pass(
      "migrations",
      "Database migrations",
      `Alembic revision ${snapshot.revision} and the five market-data tables are present.`,
    );
  }
  return fail("migrations", "Database migrations", `Revision ${snapshot.revision} did not match the expected schema.`);
}

function pass(id: string, label: string, evidence: string): Check {
  return { id, label, state: "PASS", evidence };
}

function fail(id: string, label: string, evidence: string): Check {
  return { id, label, state: "FAIL", evidence };
}

function blocked(id: string, label: string): Check {
  return { id, label, state: "BLOCKED", evidence: "Waiting for a reachable backend." };
}

function unverified(id: string, label: string, evidence: string): Check {
  return { id, label, state: "NOT VERIFIED", evidence };
}
