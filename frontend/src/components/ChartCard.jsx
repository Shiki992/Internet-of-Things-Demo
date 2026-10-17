import { useEffect, useState } from "react";
import DeviceChart from "./DeviceChart.jsx";

// Wraps a device chart in a collapsible card. Connected devices auto-expand,
// disconnected ones auto-collapse to a header row; a click overrides either
// until the next status change. Nothing is removed, so history stays visible.
export default function ChartCard({ device, now }) {
  const [open, setOpen] = useState(device.connected);
  useEffect(() => setOpen(device.connected), [device.connected]);

  // While disconnected, freeze the 60 s window at the last sample so the
  // data doesn't scroll out of view.
  const lastTs = device.history?.length ? device.history[device.history.length - 1].ts : device.last_seen;
  const chartNow = device.connected ? now : lastTs * 1000 + 1000;

  return (
    <div className={`card chart-card ${device.connected ? "" : "offline"}`}>
      <button className="chart-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="name">
          <span className={`badge ${device.protocol}`}>{device.protocol}</span>
          {device.name}
        </span>
        <span className="status">
          <span className={`dot ${device.connected ? "open" : "closed"}`} />
          {device.connected ? "connected" : "disconnected"}
          <span className="chev">{open ? "▾" : "▸"}</span>
        </span>
      </button>
      {open && <DeviceChart device={device} now={chartNow} />}
    </div>
  );
}
