export const DEFAULT_BASE = "http://127.0.0.1:8000";

export type OpenPosition = {
  side: "long" | "short";
  entryPrice: number;
  size: number | null;
};

export type Settings = {
  baseUrl: string;
  refreshSeconds: number;
  timeframe: string;
  collapsed: Record<string, boolean>;
  verdictTimeframe: string;
  verdictHorizon: string;
  accountSize: number | null;
  riskPercent: number;
  position: OpenPosition | null;
};

export const defaultSettings = (): Settings => ({
  baseUrl: DEFAULT_BASE,
  refreshSeconds: 30,
  timeframe: "1h",
  collapsed: {},
  verdictTimeframe: "15m",
  verdictHorizon: "4h",
  accountSize: null,
  riskPercent: 1,
  position: null,
});

export type VerdictRequest = {
  focus_timeframe: string;
  horizon: string;
  position: { side: "long" | "short"; entry_price: number; size?: number } | null;
  account_size: number | null;
  risk_percent: number;
  force_refresh: boolean;
};

export function verdictRequestFrom(settings: Settings, forceRefresh = false): VerdictRequest {
  const position = settings.position && Number.isFinite(settings.position.entryPrice) && settings.position.entryPrice > 0
    ? { side: settings.position.side, entry_price: settings.position.entryPrice, ...(settings.position.size ? { size: settings.position.size } : {}) }
    : null;
  return {
    focus_timeframe: settings.verdictTimeframe,
    horizon: settings.verdictHorizon,
    position,
    account_size: settings.accountSize && settings.accountSize > 0 ? settings.accountSize : null,
    risk_percent: settings.riskPercent > 0 ? settings.riskPercent : 1,
    force_refresh: forceRefresh,
  };
}

export async function postJson(base: string, path: string, body: unknown, fetcher: typeof fetch = fetch): Promise<Record<string, unknown>> {
  const root = normalizeBase(base);
  let response: Response;
  try {
    response = await fetcher(`${root}${path}`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Backend is unreachable.");
  }
  if (!response.ok) {
    let detail = "";
    try {
      const failure = (await response.json()) as { detail?: unknown };
      detail = typeof failure.detail === "string" ? ` ${failure.detail}` : "";
    } catch {
      detail = "";
    }
    throw new Error(`Backend returned HTTP ${response.status}.${detail}`);
  }
  const payload: unknown = await response.json();
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("Backend response was not an object.");
  }
  return payload as Record<string, unknown>;
}

export function requestVerdict(base: string, request: VerdictRequest, fetcher: typeof fetch = fetch) {
  return postJson(base, "/analysis/verdict", request, fetcher);
}

export function normalizeBase(value: string): string {
  const url = new URL(value.trim());
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("Backend URL must use http or https.");
  }
  if (url.username || url.password) {
    throw new Error("Backend URL must not contain credentials.");
  }
  return url.origin;
}

export async function getJson(base: string, path: string, fetcher: typeof fetch = fetch): Promise<Record<string, unknown>> {
  const root = normalizeBase(base);
  let response: Response;
  try {
    response = await fetcher(`${root}${path}`, { headers: { Accept: "application/json" } });
  } catch {
    throw new Error("Backend is unreachable.");
  }
  if (!response.ok) {
    throw new Error(`Backend returned HTTP ${response.status}.`);
  }
  const body: unknown = await response.json();
  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    throw new Error("Backend response was not an object.");
  }
  return body as Record<string, unknown>;
}

export const CHART_CANDLES = 240;

export async function loadBundle(base: string, timeframe: string, fetcher: typeof fetch = fetch) {
  const [health, diagnostics, snapshot, candles, derivatives] = await Promise.all([
    getJson(base, "/health", fetcher),
    getJson(base, "/diagnostics", fetcher),
    getJson(base, "/analysis/snapshot", fetcher),
    // Newest closed candles first, so the chart ends at the current market, then sorted ascending for drawing.
    getJson(base, `/candles?timeframe=${encodeURIComponent(timeframe)}&limit=${CHART_CANDLES}&order=desc`, fetcher),
    getJson(base, "/derivatives/snapshot", fetcher),
  ]);
  return { health, diagnostics, snapshot, candles, derivatives };
}

export function loadLive(base: string, timeframe: string, fetcher: typeof fetch = fetch) {
  return getJson(base, `/market/live?timeframe=${encodeURIComponent(timeframe)}`, fetcher);
}

export async function loadSettings(): Promise<Settings> {
  const defaults = defaultSettings();
  const storage = globalThis.chrome?.storage?.local;
  if (!storage) return defaults;
  const stored = await storage.get(defaults);
  const base = typeof stored.baseUrl === "string" ? stored.baseUrl : defaults.baseUrl;
  const position = stored.position && typeof stored.position === "object" ? (stored.position as Partial<OpenPosition>) : null;
  const account = Number(stored.accountSize);
  const risk = Number(stored.riskPercent);
  return {
    baseUrl: normalizeBase(base),
    refreshSeconds: Number(stored.refreshSeconds) || defaults.refreshSeconds,
    timeframe: typeof stored.timeframe === "string" ? stored.timeframe : defaults.timeframe,
    collapsed: stored.collapsed && typeof stored.collapsed === "object" ? (stored.collapsed as Record<string, boolean>) : {},
    verdictTimeframe: typeof stored.verdictTimeframe === "string" ? stored.verdictTimeframe : defaults.verdictTimeframe,
    verdictHorizon: typeof stored.verdictHorizon === "string" ? stored.verdictHorizon : defaults.verdictHorizon,
    accountSize: Number.isFinite(account) && account > 0 ? account : null,
    riskPercent: Number.isFinite(risk) && risk > 0 ? risk : defaults.riskPercent,
    position:
      position && (position.side === "long" || position.side === "short") && Number.isFinite(Number(position.entryPrice)) && Number(position.entryPrice) > 0
        ? { side: position.side, entryPrice: Number(position.entryPrice), size: Number(position.size) > 0 ? Number(position.size) : null }
        : null,
  };
}

export async function saveSettings(settings: Settings): Promise<void> {
  const storage = globalThis.chrome?.storage?.local;
  if (!storage) return;
  await storage.set(settings);
}
