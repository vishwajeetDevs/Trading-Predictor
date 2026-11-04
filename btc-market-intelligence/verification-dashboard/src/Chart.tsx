import type { Candle } from "./types";

export function CandleChart({ candles }: { candles: Candle[] }) {
  const numeric = candles.map((candle) => ({
    ...candle,
    high: Number(candle.high),
    low: Number(candle.low),
    open: Number(candle.open),
    close: Number(candle.close),
  }));
  if (numeric.length < 2 || numeric.some((candle) => !Number.isFinite(candle.high) || !Number.isFinite(candle.low))) {
    return <p className="muted">The chart is hidden because the response does not contain enough numeric candles.</p>;
  }
  const width = 720;
  const height = 220;
  const pad = 16;
  const high = Math.max(...numeric.map((candle) => candle.high));
  const low = Math.min(...numeric.map((candle) => candle.low));
  const span = high - low || 1;
  const slot = (width - pad * 2) / numeric.length;
  const y = (price: number) => pad + ((high - price) / span) * (height - pad * 2);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Candles returned by the API">
      {numeric.map((candle, index) => {
        const x = pad + index * slot + slot / 2;
        const up = candle.close >= candle.open;
        const color = up ? "#3ddc97" : "#ff6b6b";
        const bodyTop = y(Math.max(candle.open, candle.close));
        const bodyHeight = Math.max(1, Math.abs(y(candle.open) - y(candle.close)));
        return (
          <g key={candle.timestamp}>
            <line x1={x} x2={x} y1={y(candle.high)} y2={y(candle.low)} stroke={color} strokeWidth="1" />
            <rect x={x - Math.max(1, slot * 0.28)} y={bodyTop} width={Math.max(2, slot * 0.56)} height={bodyHeight} fill={color} />
          </g>
        );
      })}
    </svg>
  );
}
