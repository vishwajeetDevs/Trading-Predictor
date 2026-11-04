import { useEffect, useRef } from "react";
import { ColorType, CrosshairMode, LineStyle, createChart, type CandlestickData, type HistogramData, type IChartApi, type UTCTimestamp } from "lightweight-charts";

export type Candle = { time: number; open: number; high: number; low: number; close: number; volume: number; closed: boolean };

export const UP = "#22c55e";
export const DOWN = "#ef4444";
const UP_SOFT = "rgba(34, 197, 94, 0.35)";
const DOWN_SOFT = "rgba(239, 68, 68, 0.35)";

function parseCandle(row: unknown): Candle | null {
  if (!row || typeof row !== "object") return null;
  const item = row as Record<string, unknown>;
  const time = Date.parse(String(item.timestamp ?? item.open_time));
  const open = Number(item.open);
  const high = Number(item.high);
  const low = Number(item.low);
  const close = Number(item.close);
  const volume = Number(item.volume);
  if (![time, open, high, low, close, volume].every(Number.isFinite)) return null;
  return { time: Math.floor(time / 1000), open, high, low, close, volume, closed: item.is_closed !== false };
}

/** Closed candles from the backend, oldest first regardless of the response order. */
export function candleRows(payload: Record<string, unknown> | null): Candle[] {
  const rows = Array.isArray(payload?.candles) ? payload.candles : [];
  const seen = new Set<number>();
  return rows
    .map(parseCandle)
    .filter((row): row is Candle => row !== null && row.closed)
    .sort((a, b) => a.time - b.time)
    .filter((row) => {
      if (seen.has(row.time)) return false;
      seen.add(row.time);
      return true;
    });
}

/** The forming bar from /market/live, only when it is newer than the last closed bar. */
export function formingRow(live: Record<string, unknown> | null, closed: Candle[]): Candle | null {
  const bar = parseCandle(live?.forming_candle);
  if (!bar) return null;
  const last = closed[closed.length - 1];
  if (last && bar.time <= last.time) return null;
  return { ...bar, closed: false };
}

export function CandleChart({ rows, forming, lastPrice, liveLabel }: { rows: Candle[]; forming: Candle | null; lastPrice: number | null; liveLabel: string | null }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = ref.current;
    if (!host || rows.length === 0) return;
    const chart: IChartApi = createChart(host, {
      width: host.clientWidth,
      height: 280,
      layout: { background: { type: ColorType.Solid, color: "#0b1220" }, textColor: "#cbd5e1", fontSize: 11 },
      grid: { vertLines: { color: "#172033" }, horzLines: { color: "#172033" } },
      rightPriceScale: { borderColor: "#1f2a44" },
      timeScale: { borderColor: "#1f2a44", timeVisible: true, secondsVisible: false, rightOffset: 4 },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const candles = chart.addCandlestickSeries({
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    const series: CandlestickData<UTCTimestamp>[] = rows.map((row) => ({ time: row.time as UTCTimestamp, open: row.open, high: row.high, low: row.low, close: row.close }));
    if (forming) {
      // The forming bar is drawn hollow so it reads as "still moving", not as a closed candle.
      const hollow = forming.close >= forming.open ? UP : DOWN;
      series.push({ time: forming.time as UTCTimestamp, open: forming.open, high: forming.high, low: forming.low, close: forming.close, color: "rgba(0,0,0,0)", borderColor: hollow, wickColor: hollow });
    }
    candles.setData(series);
    const volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "" });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const volumes: HistogramData<UTCTimestamp>[] = rows.map((row) => ({ time: row.time as UTCTimestamp, value: row.volume, color: row.close >= row.open ? UP_SOFT : DOWN_SOFT }));
    if (forming) volumes.push({ time: forming.time as UTCTimestamp, value: forming.volume, color: forming.close >= forming.open ? UP_SOFT : DOWN_SOFT });
    volume.setData(volumes);
    if (lastPrice !== null && Number.isFinite(lastPrice)) {
      candles.createPriceLine({
        price: lastPrice,
        color: "#f59e0b",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: liveLabel ?? "last",
      });
    }
    chart.timeScale().scrollToRealTime();
    const observer = new ResizeObserver(() => chart.applyOptions({ width: host.clientWidth }));
    observer.observe(host);
    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [rows, forming, lastPrice, liveLabel]);
  return <div ref={ref} className="chart-host" />;
}

export function LineChart({ points, color = "#60a5fa" }: { points: { time: number; value: number }[]; color?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = ref.current;
    if (!host || points.length === 0) return;
    const chart = createChart(host, {
      width: host.clientWidth,
      height: 160,
      layout: { background: { type: ColorType.Solid, color: "#0b1220" }, textColor: "#cbd5e1", fontSize: 11 },
      grid: { vertLines: { color: "#172033" }, horzLines: { color: "#172033" } },
      rightPriceScale: { borderColor: "#1f2a44" },
      timeScale: { borderColor: "#1f2a44", timeVisible: true, secondsVisible: false },
    });
    const series = chart.addLineSeries({ color, lineWidth: 2 });
    series.setData(points.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
    chart.timeScale().fitContent();
    const observer = new ResizeObserver(() => chart.applyOptions({ width: host.clientWidth }));
    observer.observe(host);
    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [points, color]);
  return <div ref={ref} className="chart-host" />;
}

export function historyPoints(rows: unknown, valueKey: string): { time: number; value: number }[] {
  if (!Array.isArray(rows)) return [];
  const seen = new Set<number>();
  return rows
    .map((row) => {
      if (!row || typeof row !== "object") return null;
      const item = row as Record<string, unknown>;
      const time = Date.parse(String(item.observed_at));
      const value = Number(item[valueKey]);
      if (!Number.isFinite(time) || !Number.isFinite(value)) return null;
      return { time: Math.floor(time / 1000), value };
    })
    .filter((row): row is { time: number; value: number } => row !== null)
    .sort((a, b) => a.time - b.time)
    .filter((row) => {
      if (seen.has(row.time)) return false;
      seen.add(row.time);
      return true;
    });
}
