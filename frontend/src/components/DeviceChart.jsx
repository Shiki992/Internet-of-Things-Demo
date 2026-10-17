import { useMemo, useState } from "react";

// One line chart per device: x / y / z (m/s²) over the last WINDOW_S seconds.
// Samples arrive at 1 Hz (server-side rate limit), so 60 points per series.
const WINDOW_S = 60;
const W = 640, H = 200;
const PAD = { top: 12, right: 44, bottom: 24, left: 40 };
const SERIES = [
  { key: "x", color: "var(--s1)" },
  { key: "y", color: "var(--s2)" },
  { key: "z", color: "var(--s3)" },
];

function niceTicks(lo, hi, n = 4) {
  const span = hi - lo || 1;
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

export default function DeviceChart({ device, now }) {
  const [hover, setHover] = useState(null); // index into points

  const t1 = now / 1000;
  const t0 = t1 - WINDOW_S;
  const points = useMemo(
    () => (device.history || []).filter((p) => p.ts >= t0),
    [device.history, t0]
  );

  // y-scale: data extent padded, never narrower than ±2 so a still phone reads flat
  const [lo, hi] = useMemo(() => {
    let mn = -2, mx = 2;
    for (const p of points) for (const s of SERIES) {
      mn = Math.min(mn, p[s.key]);
      mx = Math.max(mx, p[s.key]);
    }
    const pad = (mx - mn) * 0.1;
    return [mn - pad, mx + pad];
  }, [points]);

  const iw = W - PAD.left - PAD.right;
  const ih = H - PAD.top - PAD.bottom;
  const sx = (ts) => PAD.left + ((ts - t0) / WINDOW_S) * iw;
  const sy = (v) => PAD.top + (1 - (v - lo) / (hi - lo)) * ih;

  const path = (key) =>
    points.map((p, i) => `${i ? "L" : "M"}${sx(p.ts).toFixed(1)},${sy(p[key]).toFixed(1)}`).join(" ");

  const yTicks = niceTicks(lo, hi);
  const xTicks = [60, 45, 30, 15, 0];
  const last = points[points.length - 1];
  const hp = hover != null ? points[hover] : null;

  function onMove(e) {
    if (!points.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const xv = ((e.clientX - rect.left) / rect.width) * W;
    const ts = t0 + ((xv - PAD.left) / iw) * WINDOW_S;
    let best = 0, bd = Infinity;
    points.forEach((p, i) => {
      const d = Math.abs(p.ts - ts);
      if (d < bd) { bd = d; best = i; }
    });
    setHover(best);
  }

  return (
    <div className="chart">
      <div className="chart-head">
        <span className="legend">
          {SERIES.map((s) => (
            <span key={s.key}>
              <i style={{ background: s.color }} /> {s.key}
              <b className="tnum">{last ? last[s.key].toFixed(2) : "—"}</b>
            </span>
          ))}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={sy(v)} y2={sy(v)} className={v === 0 ? "axis" : "grid"} />
            <text x={PAD.left - 6} y={sy(v)} className="tick" textAnchor="end" dominantBaseline="middle">{v}</text>
          </g>
        ))}
        {xTicks.map((s) => (
          <text key={s} x={sx(t1 - s)} y={H - 6} className="tick" textAnchor={s === 60 ? "start" : s === 0 ? "end" : "middle"}>
            {s ? `-${s}s` : "now"}
          </text>
        ))}
        {SERIES.map((s) => (
          <path key={s.key} d={path(s.key)} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {last && SERIES.map((s) => (
          <circle key={s.key} cx={sx(last.ts)} cy={sy(last[s.key])} r="3.5" fill={s.color} className="ring" />
        ))}
        {hp && (
          <g>
            <line x1={sx(hp.ts)} x2={sx(hp.ts)} y1={PAD.top} y2={H - PAD.bottom} className="crosshair" />
            {SERIES.map((s) => (
              <circle key={s.key} cx={sx(hp.ts)} cy={sy(hp[s.key])} r="4" fill={s.color} className="ring" />
            ))}
          </g>
        )}
        {!points.length && (
          <text x={W / 2} y={H / 2} className="tick" textAnchor="middle">waiting for samples…</text>
        )}
      </svg>
      {hp && (
        <div className="tooltip">
          <span className="mono">{new Date(hp.ts * 1000).toLocaleTimeString([], { hour12: false })}</span>
          {SERIES.map((s) => (
            <span key={s.key}><i style={{ background: s.color }} />{s.key} <b className="tnum">{hp[s.key].toFixed(2)}</b></span>
          ))}
        </div>
      )}
    </div>
  );
}
