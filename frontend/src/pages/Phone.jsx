import { useEffect, useRef, useState } from "react";
import { connect } from "../lib/ws.js";
import TouchPad from "../components/TouchPad.jsx";
import { bleSupported, connectBle } from "../lib/ble.js";

const SEND_INTERVAL_MS = 50; // ~20 Hz; the sensor itself fires ~60 Hz

function loadDeviceId() {
  let id = localStorage.getItem("device_id");
  if (!id) {
    id = "wifi-" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem("device_id", id);
  }
  return id;
}

function guessName() {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  const m = ua.match(/Android[^;]*;\s*([^)]+?)\s*(Build|\))/);
  if (m) return m[1].trim();
  return "Phone";
}

export default function Phone() {
  const [deviceId] = useState(loadDeviceId);
  const [name, setName] = useState(() => localStorage.getItem("device_name") || guessName());
  const [status, setStatus] = useState("idle");      // idle | connecting | open | closed
  const [streaming, setStreaming] = useState(false);
  const [sample, setSample] = useState({ x: 0, y: 0, z: 0 });
  const [sent, setSent] = useState(0);
  const [error, setError] = useState(null);
  // "wifi" = WebSocket to the backend; "bluetooth" = Web Bluetooth to the host's
  // GATT server. Bluetooth is only offered where the browser supports it
  // (Android Chrome), so iPhones see the WiFi path only.
  const canBle = bleSupported();
  const [transport, setTransport] = useState(() =>
    canBle && localStorage.getItem("transport") === "bluetooth" ? "bluetooth" : "wifi"
  );
  useEffect(() => localStorage.setItem("transport", transport), [transport]);

  const sockRef = useRef(null);   // WiFi: {send(obj), close()}
  const bleRef = useRef(null);    // Bluetooth: {send(x,y,z), close()}
  const lastSentRef = useRef(0);
  const wakeLockRef = useRef(null);
  const handlerRef = useRef(null); // stable reference for add/removeEventListener

  const secure = window.isSecureContext;
  // Browsers only expose DeviceMotion on HTTPS pages; over plain HTTP we fall
  // back to a touch pad so the phone can still connect and send data.
  const hasMotion = secure && typeof window.DeviceMotionEvent !== "undefined";
  const [httpsUrl, setHttpsUrl] = useState(null);

  useEffect(() => {
    if (hasMotion) return;
    fetch("/api/join-info")
      .then((r) => r.json())
      .then((j) => j.https_url && setHttpsUrl(j.https_url + "/phone"))
      .catch(() => {});
  }, [hasMotion]);

  useEffect(() => {
    localStorage.setItem("device_name", name);
  }, [name]);

  useEffect(() => () => stop(), []); // eslint-disable-line react-hooks/exhaustive-deps

  async function start() {
    setError(null);
    try {
      if (transport === "bluetooth") {
        // Shows the system device chooser; pick "Motion Hub" (the backend host).
        bleRef.current = await connectBle({ deviceId, name, onStatus: setStatus });
      }
      // iOS 13+ requires an explicit permission prompt, triggered from a tap.
      if (hasMotion && typeof DeviceMotionEvent.requestPermission === "function") {
        const res = await DeviceMotionEvent.requestPermission();
        if (res !== "granted") throw new Error("Motion permission denied");
      }
    } catch (e) {
      bleRef.current?.close();
      bleRef.current = null;
      setStatus("idle");
      if (e?.name === "NotFoundError") setError("No device chosen. Make sure the backend is running and Bluetooth is on, then pick \"Motion Hub\".");
      else setError(e.message || String(e));
      return;
    }

    if (transport === "wifi") {
      sockRef.current = connect("/ws/device", {
        onStatus: (s) => {
          setStatus(s);
          if (s === "open") {
            sockRef.current.send({
              type: "hello",
              device_id: deviceId,
              name,
              user_agent: navigator.userAgent,
            });
          }
        },
        onMessage: () => {},
      });
    }

    if (hasMotion) {
      handlerRef.current = onMotion;
      window.addEventListener("devicemotion", handlerRef.current);
    }
    setStreaming(true);

    try {
      wakeLockRef.current = await navigator.wakeLock?.request("screen");
    } catch {
      /* wake lock is a nice-to-have */
    }
  }

  function stop() {
    if (handlerRef.current) window.removeEventListener("devicemotion", handlerRef.current);
    handlerRef.current = null;
    sockRef.current?.close();
    sockRef.current = null;
    bleRef.current?.close();
    bleRef.current = null;
    wakeLockRef.current?.release?.();
    wakeLockRef.current = null;
    setStreaming(false);
    setStatus("idle");
  }

  // Shared sender for both the sensor handler and the touch pad.
  function publish(x, y, z, extra = {}, force = false) {
    setSample({ x, y, z });
    const now = performance.now();
    if (!force && now - lastSentRef.current < SEND_INTERVAL_MS) return;
    lastSentRef.current = now;
    if (bleRef.current) {
      bleRef.current.send(x, y, z);
    } else {
      sockRef.current?.send({
        type: "motion",
        x: +x.toFixed(3),
        y: +y.toFixed(3),
        z: +z.toFixed(3),
        t: Date.now(),
        ...extra,
      });
    }
    setSent((n) => n + 1);
  }

  function onMotion(e) {
    const a = e.acceleration || {};
    const g = e.accelerationIncludingGravity || {};
    // Some Android browsers only populate accelerationIncludingGravity.
    publish(a.x ?? g.x ?? 0, a.y ?? g.y ?? 0, a.z ?? g.z ?? 0, {
      gx: g.x == null ? undefined : +g.x.toFixed(3),
      gy: g.y == null ? undefined : +g.y.toFixed(3),
      gz: g.z == null ? undefined : +g.z.toFixed(3),
    });
  }

  const mag = Math.hypot(sample.x, sample.y, sample.z);

  return (
    <div className="phone">
      <h1>Motion Hub · Phone</h1>

      {!hasMotion && (
        <div className="card warn">
          {secure
            ? "This browser does not expose the motion sensor."
            : "Browsers only allow the motion sensor on https:// pages, so this http:// page can't read it."}{" "}
          You can still connect and send data with the touch pad below.
          {!secure && httpsUrl && (
            <>
              {" "}For real sensor data open <a href={httpsUrl}>{httpsUrl}</a> and accept the certificate
              warning once.
            </>
          )}
        </div>
      )}

      <div className="card">
        <h2>Device name</h2>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} disabled={streaming} />
        <div className="meta" style={{ marginTop: 8 }}>
          <span className="mono">{deviceId}</span>
          <span className="status">
            <span className={`dot ${status}`} /> {status}
          </span>
        </div>
        <div className="seg" style={{ marginTop: 12 }}>
          <button
            className={transport === "wifi" ? "on" : ""}
            onClick={() => setTransport("wifi")}
            disabled={streaming}
          >
            WiFi
          </button>
          <button
            className={transport === "bluetooth" ? "on" : ""}
            onClick={() => setTransport("bluetooth")}
            disabled={streaming || !canBle}
            title={canBle ? "" : "Web Bluetooth is not available in this browser"}
          >
            Bluetooth{canBle ? "" : " (n/a)"}
          </button>
        </div>
        {!canBle && (
          <p className="hint" style={{ margin: "8px 0 0" }}>
            Bluetooth sending needs Web Bluetooth (Android Chrome). This browser will use WiFi.
          </p>
        )}
      </div>

      <div className="card">
        <h2>{hasMotion ? "Accelerometer (m/s²)" : "Sending (m/s²)"}</h2>
        <div className="big">
          {["x", "y", "z"].map((k) => (
            <div className="ax" key={k}>
              <div className="k">{k}</div>
              <div className="v tnum">{sample[k].toFixed(2)}</div>
            </div>
          ))}
        </div>
        <div className="bar">
          <div style={{ width: `${Math.min(100, (mag / 20) * 100)}%` }} />
        </div>
        <div className="meta" style={{ marginTop: 8 }}>
          <span>|a| = {mag.toFixed(2)}</span>
          <span>sent: {sent}</span>
        </div>
      </div>

      {streaming && !hasMotion && (
        <div className="card">
          <h2>Touch pad (fallback input)</h2>
          <TouchPad onMove={(x, y, force) => publish(x, y, 0, { source: "touch" }, force)} />
          <p className="hint" style={{ margin: "8px 0 0" }}>
            Drag your finger — position maps to x / y in ±10 m/s².
          </p>
        </div>
      )}

      {error && <div className="card warn">{error}</div>}

      {!streaming ? (
        <button className="btn primary xl" onClick={start}>
          Start streaming
        </button>
      ) : (
        <button className="btn danger xl" onClick={stop}>
          Stop
        </button>
      )}

      <p className="hint">
        Keep this tab in the foreground — browsers pause sensors in background tabs. Data goes over{" "}
        {transport === "bluetooth" ? "Bluetooth (Web Bluetooth to the host)" : "WiFi (WebSocket)"} to the
        dashboard{hasMotion ? "" : " — from the touch pad while the sensor is unavailable"}.
      </p>
    </div>
  );
}
