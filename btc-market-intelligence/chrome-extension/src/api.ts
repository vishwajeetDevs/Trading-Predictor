export const DEFAULT_BASE = "http://127.0.0.1:8000";

export type Settings = {
  baseUrl: string;
  refreshSeconds: number;
  timeframe: string;
  collapsed: Record<string, boolean>;
};

export const defaultSettings = (): Settings => ({
  baseUrl: DEFAULT_BASE,
  refreshSeconds: 30,
  timeframe: "1h",
  collapsed: {},
});

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

export async function loadBundle(base: string, timeframe: string, fetcher: typeof fetch = fetch) {
  const [health, diagnostics, snapshot, candles, derivatives] = await Promise.all([
    getJson(base, "/health", fetcher),
    getJson(base, "/diagnostics", fetcher),
    getJson(base, "/analysis/snapshot", fetcher),
    getJson(base, `/candles?timeframe=${encodeURIComponent(timeframe)}&limit=240`, fetcher),
    getJson(base, "/derivatives/snapshot", fetcher),
  ]);
  return { health, diagnostics, snapshot, candles, derivatives };
}

export async function loadSettings(): Promise<Settings> {
  const defaults = defaultSettings();
  const storage = globalThis.chrome?.storage?.local;
  if (!storage) return defaults;
  const stored = await storage.get(defaults);
  const base = typeof stored.baseUrl === "string" ? stored.baseUrl : defaults.baseUrl;
  return {
    baseUrl: normalizeBase(base),
    refreshSeconds: Number(stored.refreshSeconds) || defaults.refreshSeconds,
    timeframe: typeof stored.timeframe === "string" ? stored.timeframe : defaults.timeframe,
    collapsed: stored.collapsed && typeof stored.collapsed === "object" ? (stored.collapsed as Record<string, boolean>) : {},
  };
}

export async function saveSettings(settings: Settings): Promise<void> {
  const storage = globalThis.chrome?.storage?.local;
  if (!storage) return;
  await storage.set(settings);
}
