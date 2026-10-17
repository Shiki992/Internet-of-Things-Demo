import { useEffect, useState } from "react";
import { QRCodeCanvas } from "qrcode.react";

// The QR code defaults to the backend's LAN address (from /api/join-info) so a
// phone on the same network can reach it. Phones need HTTPS for motion sensors,
// so when the backend is exposed through a tunnel (ngrok / cloudflared) paste
// that URL here; the override is remembered in this browser.
export default function JoinQr() {
  const [lan, setLan] = useState(null);                         // from /api/join-info
  const [override, setOverride] = useState(() => localStorage.getItem("join_base") || "");

  useEffect(() => {
    fetch("/api/join-info")
      .then((r) => r.json())
      .then(setLan)
      .catch(() => setLan({}));
  }, []);

  const base = override || lan?.lan_url || window.location.origin;
  const url = base.replace(/\/$/, "") + "/phone";
  const https = url.startsWith("https://");

  function setBase(v) {
    setOverride(v);
    if (v) localStorage.setItem("join_base", v);
    else localStorage.removeItem("join_base");
  }

  return (
    <div className="card">
      <h2>Join from a phone</h2>
      <div className="qr">
        <QRCodeCanvas value={url} size={180} level="M" includeMargin={false} />
        <a className="url" href={url} target="_blank" rel="noreferrer">{url}</a>
      </div>
      <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
        <input
          type="text"
          value={override}
          onChange={(e) => setBase(e.target.value.trim())}
          placeholder="Override, e.g. https://your-tunnel.trycloudflare.com"
        />
        <div className="meta">
          <span>
            LAN: {lan === null ? "detecting…" : lan.lan_ip ? <span className="mono">{lan.lan_ip}:{lan.port}</span> : "not found"}
          </span>
          {override && (
            <button className="btn" style={{ padding: "2px 8px", fontSize: 12 }} onClick={() => setBase("")}>
              Use LAN address
            </button>
          )}
        </div>
      </div>
      {!https && (
        <p className="hint" style={{ marginBottom: 0 }}>
          Phones need <b>https://</b> to read motion sensors. Expose the backend with a tunnel and paste its URL above.
        </p>
      )}
    </div>
  );
}
