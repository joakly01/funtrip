export type Bar = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  volume_raw?: number;
};
export type ChartPoint = { time: string; price: number };
export type Drawing = {
  id: string;
  kind: "horizontal" | "trend" | "zone";
  points: ChartPoint[];
  label: string;
  origin?: { algorithm_version: string; mode: 'trend' | 'range' | 'levels'; lookback: 40 | 80 | 160; as_of: string };
};
export const plot = {
  left: 16,
  right: 714,
  top: 24,
  bottom: 310,
  width: 780,
  height: 420,
};
export function sameDrawing(a: Drawing, b: Drawing) {
  return a.kind === b.kind && a.points.length === b.points.length && a.points.every((p, i) =>
    (a.kind === 'horizontal' || p.time === b.points[i].time) && Math.abs(p.price - b.points[i].price) < 1e-6);
}
export function chartScale(bars: Bar[], extraPrices: number[] = []) {
  const valid = extraPrices.filter(p => Number.isFinite(p) && p > 0);
  const lo = Math.min(...bars.map((b) => b.low), ...valid),
    hi = Math.max(...bars.map((b) => b.high), ...valid);
  const pad = Math.max((hi - lo) * 0.08, hi * 0.003);
  const min = lo - pad,
    max = hi + pad;
  return {
    min,
    max,
    x: (i: number) =>
      plot.left + ((i + 0.5) * (plot.right - plot.left)) / bars.length,
    y: (p: number) =>
      plot.bottom - ((p - min) / (max - min)) * (plot.bottom - plot.top),
  };
}
export function pointAt(bars: Bar[], x: number, y: number, extraPrices: number[] = []): ChartPoint | null {
  if (
    !bars.length ||
    x < plot.left ||
    x > plot.right ||
    y < plot.top ||
    y > plot.bottom
  )
    return null;
  const scale = chartScale(bars, extraPrices);
  const i = Math.min(
    bars.length - 1,
    Math.floor(((x - plot.left) / (plot.right - plot.left)) * bars.length),
  );
  return {
    time: bars[i].time,
    price: Number(
      (
        scale.max -
        ((y - plot.top) / (plot.bottom - plot.top)) * (scale.max - scale.min)
      ).toFixed(4),
    ),
  };
}
export function drawingCoordinates(d: Drawing, bars: Bar[], extraPrices: number[] = []) {
  const scale = chartScale(bars, extraPrices);
  const points = d.points.map((p) => ({
    index: bars.findIndex((b) => b.time === p.time),
    price: p.price,
  }));
  if (d.kind !== "horizontal" && points.some((p) => p.index < 0)) return null;
  return points.map((p) => ({
    x: scale.x(Math.max(0, p.index)),
    y: scale.y(p.price),
  }));
}
