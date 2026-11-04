import { useEffect, useRef } from "react";
import { ColorType, createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";

type Candle = { time: number; open: number; high: number; low: number; close: number; volume: number };

export function candleRows(payload: Record<string, unknown> | null): Candle[] {
  const rows = Array.isArray(payload?.candles) ? payload.candles : [];
  return rows
    .map((row) => {
      if (!row || typeof row !== "object") return null;
      const item = row as Record<string, unknown>;
      const time = Date.parse(String(item.timestamp));
      const open = Number(item.open);
      const high = Number(item.high);
      const low = Number(item.low);
      const close = Number(item.close);
      const volume = Number(item.volume);
      if (![time, open, high, low, close, volume].every(Number.isFinite)) return null;
      return { time: Math.floor(time / 1000), open, high, low, close, volume };
    })
    .filter((row): row is Candle => row !== null)
    .sort((a, b) => a.time - b.time);
}

export function CandleChart({ rows }: { rows: Candle[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = ref.current;
    if (!host || rows.length === 0) return;
    const chart: IChartApi = createChart(host, {
      width: host.clientWidth,
      height: 240,
      layout: { background: { type: ColorType.Solid, color: "#111111" }, textColor: "#e5e5e5" },
      grid: { vertLines: { color: "#1f1f1f" }, horzLines: { color: "#1f1f1f" } },
      rightPriceScale: { borderColor: "#333333" },
      timeScale: { borderColor: "#333333" },
    });
    const candles = chart.addCandlestickSeries({ upColor: "#f5f5f5", downColor: "#666666", borderVisible: true, borderUpColor: "#f5f5f5", borderDownColor: "#8c8c8c", wickUpColor: "#f5f5f5", wickDownColor: "#8c8c8c" });
    candles.setData(rows.map((row) => ({ time: row.time as UTCTimestamp, open: row.open, high: row.high, low: row.low, close: row.close })));
    const volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "" });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volume.setData(rows.map((row) => ({ time: row.time as UTCTimestamp, value: row.volume, color: row.close >= row.open ? "#525252" : "#2e2e2e" })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [rows]);
  return <div ref={ref} />;
}

export function LineChart({ points }: { points: { time: number; value: number }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = ref.current;
    if (!host || points.length === 0) return;
    const chart = createChart(host, {
      width: host.clientWidth,
      height: 160,
      layout: { background: { type: ColorType.Solid, color: "#111111" }, textColor: "#e5e5e5" },
      grid: { vertLines: { color: "#1f1f1f" }, horzLines: { color: "#1f1f1f" } },
      rightPriceScale: { borderColor: "#333333" },
      timeScale: { borderColor: "#333333" },
    });
    const series = chart.addLineSeries({ color: "#e5e5e5" });
    series.setData(points.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points]);
  return <div ref={ref} />;
}

export function historyPoints(rows: unknown, valueKey: string): { time: number; value: number }[] {
  if (!Array.isArray(rows)) return [];
  return rows
    .map((row) => {
      if (!row || typeof row !== "object") return null;
      const item = row as Record<string, unknown>;
      const time = Date.parse(String(item.observed_at));
      const value = Number(item[valueKey]);
      if (!Number.isFinite(time) || !Number.isFinite(value)) return null;
      return { time: Math.floor(time / 1000), value };
    })
    .filter((row): row is { time: number; value: number } => row !== null);
}
