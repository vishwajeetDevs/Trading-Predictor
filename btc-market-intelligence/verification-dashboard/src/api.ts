import type { ApiResult } from "./types";

export async function callApi(path: string): Promise<ApiResult> {
  const started = performance.now();
  const testedAt = new Date().toISOString();
  try {
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    const text = await response.text();
    let body: unknown = text;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = text;
    }
    return {
      path,
      status: response.status,
      ms: Math.round(performance.now() - started),
      ok: response.ok,
      body,
      error: response.ok ? null : `HTTP ${response.status}`,
      testedAt,
    };
  } catch (error) {
    return {
      path,
      status: null,
      ms: Math.round(performance.now() - started),
      ok: false,
      body: null,
      error: error instanceof Error ? error.message : "request failed",
      testedAt,
    };
  }
}
