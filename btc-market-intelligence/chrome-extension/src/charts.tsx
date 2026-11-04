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
      layout: { background: { type: ColorType.Solid, color: "#101a2b" }, textColor: "#d7e0ea" },
      grid: { vertLines: { color: "#1c2c40" }, horzLines: { color: "#1c2c40" } },
      rightPriceScale: { borderColor: "#2a3b52" },
      timeScale: { borderColor: "#2a3b52" },
    });
    const candles = chart.addCandlestickSeries({ upColor: "#3ddc97", downColor: "#ff6b6b", borderVisible: false, wickUpColor: "#3ddc97", wickDownColor: "#ff6b6b" });
    candles.setData(rows.map((row) => ({ time: row.time as UTCTimestamp, open: row.open, high: row.high, low: row.low, close: row.close })));
    const volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "" });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volume.setData(rows.map((row) => ({ time: row.time as UTCTimestamp, value: row.volume, color: row.close >= row.open ? "#1f8f63" : "#8d3b3b" })));
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
      layout: { background: { type: ColorType.Solid, color: "#101a2b" }, textColor: "#d7e0ea" },
      grid: { vertLines: { color: "#1c2c40" }, horzLines: { color: "#1c2c40" } },
      rightPriceScale: { borderColor: "#2a3b52" },
      timeScale: { borderColor: "#2a3b52" },
    });
    const series = chart.addLineSeries({ color: "#7eb6ff" });
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
