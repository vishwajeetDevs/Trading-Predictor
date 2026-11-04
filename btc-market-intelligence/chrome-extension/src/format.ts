export function text(value: unknown, empty = "Unavailable"): string {
  if (value === null || value === undefined || value === "") return empty;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  return empty;
}

export function percent(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Unavailable";
  const number = Number(value);
  if (!Number.isFinite(number)) return "Unavailable";
  return `${(number * 100).toFixed(4)}%`;
}

export function tone(value: unknown): "up" | "down" | "flat" {
  const number = Number(value);
  if (!Number.isFinite(number) || number === 0) return "flat";
  return number > 0 ? "up" : "down";
}

export const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;
export const HORIZONS = ["15m", "30m", "1h", "4h", "24h"] as const;

export const INDICATOR_HELP: Record<string, string> = {
  rsi: "Wilder RSI(14). It measures recent close-to-close gains against losses. It is not a trade signal.",
  sma: "Simple moving average of closes.",
  ema: "Exponential moving average of closes.",
  macd: "MACD line, signal line, and histogram from the documented periods.",
  vwap: "Volume-weighted average price for the UTC day of the bar.",
  bollinger: "Bollinger bands around a 20-close average using population standard deviation.",
  atr: "Wilder average true range. It measures recent range, not direction.",
  realized_volatility: "Sample standard deviation of recent log returns. It is not annualized.",
};
