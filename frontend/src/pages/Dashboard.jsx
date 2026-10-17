import { useEffect, useMemo, useState } from "react";
import { connect } from "../lib/ws.js";
import StatTile from "../components/StatTile.jsx";
import DeviceTable from "../components/DeviceTable.jsx";
import ChartCard from "../components/ChartCard.jsx";
import JoinQr from "../components/JoinQr.jsx";

const HISTORY_LIMIT = 300; // points kept per device client-side (~5 min at 1 Hz)

export default function Dashboard() {
  const [status, setStatus] = useState("connecting");
  const [devices, setDevices] = useState({}); // device_id -> device (never removed)
  const [now, setNow] = useState(Date.now());  // drives the sliding chart window + "last seen"

  useEffect(() => {
    const sock = connect("/ws/dashboard", {
      onStatus: setStatus,
      onMessage: (msg) => {
        switch (msg.type) {
          case "snapshot":
            setDevices(Object.fromEntries(msg.devices.map((d) => [d.device_id, d])));
            break;
          case "device_connected":
            // Reconnect: keep whatever history we already have if the server sent none.
            setDevices((d) => ({
              ...d,
              [msg.device.device_id]: {
                ...msg.device,
                history: msg.device.history?.length ? msg.device.history : d[msg.device.device_id]?.history || [],
              },
            }));
            break;
          case "device_disconnected":
            setDevices((d) =>
              d[msg.device_id]
                ? { ...d, [msg.device_id]: { ...d[msg.device_id], connected: false, last_seen: msg.last_seen } }
                : d
            );
            break;
          case "motion":
            setDevices((d) => {
              const cur = d[msg.device_id];
              if (!cur) return d;
              return {
                ...d,
                [msg.device_id]: {
                  ...cur,
                  last_seen: msg.point.ts,
                  last_sample: msg.sample,
                  accepted: msg.accepted,
                  dropped: msg.dropped,
                  history: [...(cur.history || []), msg.point].slice(-HISTORY_LIMIT),
                },
              };
            });
            break;
          default:
        }
      },
    });
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      sock.close();
      clearInterval(timer);
    };
  }, []);

  const list = useMemo(() => Object.values(devices).sort((a, b) => a.first_seen - b.first_seen), [devices]);
  const live = useMemo(() => list.filter((d) => d.connected), [list]);
  const counts = useMemo(() => {
    const c = { wifi: 0, bluetooth: 0 };
    for (const d of live) c[d.protocol] = (c[d.protocol] || 0) + 1;
    return c;
  }, [live]);

  return (
    <div className="page">
      <div className="header">
        <h1>Motion Hub</h1>
        <span className="status">
          <span className={`dot ${status}`} /> backend {status}
        </span>
      </div>

      <div className="tiles">
        <StatTile label="Connected now" value={live.length} sub={`${list.length} known`} />
        <StatTile label="WiFi" value={counts.wifi} color="var(--wifi)" sub="WebSocket from browser" />
        <StatTile label="Bluetooth" value={counts.bluetooth} color="var(--bluetooth)" sub="Web Bluetooth / BLE" />
        <StatTile label="Sample rate" value="1 / s" sub="per device, extras dropped" />
      </div>

      <div className="grid">
        <div className="stack">
          <div className="card">
            <h2>Devices</h2>
            <DeviceTable devices={list} now={now} />
          </div>
          {list.length === 0 ? (
            <div className="card empty">Charts appear here as devices connect.</div>
          ) : (
            list.map((d) => <ChartCard key={d.device_id} device={d} now={now} />)
          )}
        </div>
        <div className="stack">
          <JoinQr />
        </div>
      </div>
    </div>
  );
}
