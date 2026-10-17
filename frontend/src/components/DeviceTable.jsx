function age(ts, now) {
  const s = Math.max(0, now / 1000 - ts);
  if (s < 2) return "now";
  if (s < 60) return `${s.toFixed(0)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

export default function DeviceTable({ devices, now }) {
  if (devices.length === 0) {
    return <div className="empty">No devices yet. Scan the QR code with a phone to join.</div>;
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <table>
        <thead>
          <tr>
            <th>Device</th>
            <th>Status</th>
            <th>Protocol</th>
            <th className="tnum">Samples</th>
            <th>Last x / y / z (m/s²)</th>
            <th>Last seen</th>
          </tr>
        </thead>
        <tbody>
          {devices.map((d) => {
            const s = d.last_sample;
            return (
              <tr key={d.device_id} className={d.connected ? "" : "stale"}>
                <td>
                  <div>{d.name}</div>
                  <div className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>{d.device_id}</div>
                </td>
                <td>
                  <span className="status">
                    <span className={`dot ${d.connected ? "open" : "closed"}`} />
                    {d.connected ? "connected" : "disconnected"}
                  </span>
                </td>
                <td>
                  <span className={`badge ${d.protocol}`}>{d.protocol}</span>
                </td>
                <td className="tnum" title={`${d.dropped} dropped by the 1/s limit`}>
                  {d.accepted}
                  {d.dropped > 0 && <span style={{ color: "var(--muted)" }}> · {d.dropped} dropped</span>}
                </td>
                <td>
                  {s ? (
                    <span className="axes tnum mono">
                      <span>{s.x.toFixed(2)}</span>
                      <span>{s.y.toFixed(2)}</span>
                      <span>{s.z.toFixed(2)}</span>
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td>{age(d.last_seen, now)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
