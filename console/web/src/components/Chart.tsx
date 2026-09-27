import { useMemo, useRef, useState } from "react";

export interface Series { name: string; color: string; values: (number | null)[]; dashed?: boolean }

/** 轻量折线图（SVG）：多条线共用一个纵轴，悬停显示那一列的值。 */
export function LineChart({ series, labels, height = 180, format = (v: number) => String(Math.round(v)) }: {
  series: Series[]; labels: (string | number)[]; height?: number; format?: (v: number) => string;
}) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const W = 800, H = height, P = { l: 44, r: 12, t: 10, b: 22 };
  const n = labels.length;
  const { max, min } = useMemo(() => {
    let mx = -Infinity, mn = Infinity;
    for (const s of series) for (const v of s.values) if (v != null) { mx = Math.max(mx, v); mn = Math.min(mn, v); }
    if (!Number.isFinite(mx)) return { max: 1, min: 0 };
    mn = Math.min(0, mn);
    return { max: mx === mn ? mx + 1 : mx * 1.08, min: mn };
  }, [series]);
  const x = (i: number) => P.l + (n <= 1 ? 0 : (i / (n - 1)) * (W - P.l - P.r));
  const y = (v: number) => P.t + (1 - (v - min) / (max - min)) * (H - P.t - P.b);
  const ticks = 4;
  return (
    <div style={{ position: "relative" }}>
      <svg ref={ref} className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ height }}
        onMouseMove={(e) => {
          const r = ref.current!.getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          const i = Math.round(((px - P.l) / (W - P.l - P.r)) * (n - 1));
          setHover(i >= 0 && i < n ? i : null);
        }}
        onMouseLeave={() => setHover(null)}>
        {Array.from({ length: ticks + 1 }, (_, k) => {
          const v = min + ((max - min) * k) / ticks;
          return (
            <g key={k}>
              <line className="axis" x1={P.l} x2={W - P.r} y1={y(v)} y2={y(v)} strokeDasharray={k ? "3 4" : undefined} />
              <text x={P.l - 6} y={y(v) + 3} textAnchor="end">{format(v)}</text>
            </g>
          );
        })}
        {n > 1 && [0, Math.floor(n / 2), n - 1].map((i) => (
          <text key={i} x={x(i)} y={H - 6} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}>{labels[i]}</text>
        ))}
        {series.map((s) => {
          let d = "";
          s.values.forEach((v, i) => {
            if (v == null) return;
            d += `${d ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
          });
          return <path key={s.name} d={d} fill="none" stroke={s.color} strokeWidth={1.8} strokeDasharray={s.dashed ? "5 4" : undefined} vectorEffect="non-scaling-stroke" />;
        })}
        {hover != null && <line x1={x(hover)} x2={x(hover)} y1={P.t} y2={H - P.b} stroke="var(--text-3)" strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />}
      </svg>
      {hover != null && (
        <div className="glass" style={{ position: "absolute", top: 6, left: `${(x(hover) / W) * 100}%`, transform: `translateX(${x(hover) > W * 0.7 ? "-105%" : "8px"})`, padding: "6px 10px", fontSize: 12, pointerEvents: "none", minWidth: 120 }}>
          <div className="faint" style={{ marginBottom: 2 }}>{labels[hover]}</div>
          {series.map((s) => (
            <div key={s.name} className="row" style={{ gap: 6 }}>
              <i style={{ width: 8, height: 8, borderRadius: 2, background: s.color, display: "inline-block" }} />
              <span className="muted">{s.name}</span>
              <b className="right">{s.values[hover] == null ? "—" : format(s.values[hover]!)}</b>
            </div>
          ))}
        </div>
      )}
      <div className="legend" style={{ marginTop: 6 }}>
        {series.map((s) => <span key={s.name}><i style={{ background: s.color }} />{s.name}</span>)}
      </div>
    </div>
  );
}
