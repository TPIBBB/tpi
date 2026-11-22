import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { EquilibriumBand, TickMetrics } from "../api/types";

/** 找出每次天災的開始與結束回合，用來在圖上畫紅色區塊。 */
function disasterRanges(metrics: TickMetrics[]) {
  const ranges: { id: number; start: number; end: number }[] = [];
  for (const m of metrics) {
    if (m.phase !== "disaster" || m.disaster_id === null) continue;
    const last = ranges[ranges.length - 1];
    if (last && last.id === m.disaster_id) last.end = m.tick;
    else ranges.push({ id: m.disaster_id, start: m.tick, end: m.tick });
  }
  return ranges;
}

export function PriceChart({ metrics, band }: { metrics: TickMetrics[]; band: EquilibriumBand | null }) {
  const ranges = disasterRanges(metrics);
  return (
    <section>
      <h2>糧價與天災</h2>
      <p className="hint">紅色區塊：天災期間　綠色帶：均衡區間</p>
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={metrics} margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="tick" type="number" domain={["dataMin", "dataMax"]} />
          <YAxis />
          <Tooltip
            labelFormatter={(tick) => `第 ${tick} 回合`}
            formatter={(value) => [Number(value).toFixed(2), "糧價"]}
          />
          {ranges.map((r) => (
            <ReferenceArea key={r.id} x1={r.start} x2={r.end} fill="red" fillOpacity={0.15}
              label={{ value: `天災 ${r.id}`, position: "insideTop" }} />
          ))}
          {band && <ReferenceArea y1={band.low} y2={band.high} fill="green" fillOpacity={0.12} />}
          <Line type="monotone" dataKey="price" stroke="#2563eb" dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </section>
  );
}