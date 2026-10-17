# Motion Hub — phone accelerometer IoT demo

Phones stream accelerometer data to a FastAPI backend with **no app install**:
they open a web page (scan a QR code), tap *Start*, and the browser's
`DeviceMotion` API streams over a WebSocket. A React dashboard shows how many
devices are connected, over which protocol, and the live message feed.

```
iPhone / any browser ──WebSocket (WiFi)──────────┐
Android Chrome ──Web Bluetooth──▶ host GATT server ├──▶ FastAPI hub ──WebSocket──▶ React dashboard
ESP32 etc. ──BLE notify──▶ bleak bridge (opt-in) ─┘
```

## Layout

```
backend/app/main.py        FastAPI: /ws/device, /ws/dashboard, /api/*, serves frontend/dist
backend/app/hub.py         device registry, per-protocol counts, fan-out to dashboards
backend/app/ble_server.py  BLE GATT server: Android phones write samples via Web Bluetooth
backend/app/ble_bridge.py  optional central-role ingest for real BLE peripherals (bleak)
backend/app/store.py       SQLite persistence (backend/data/motion.db): devices, samples, sessions
backend/mcp_server.py      local MCP server giving Claude read-only access to that database
frontend/src/pages/Dashboard.jsx   the dashboard  (route /)
frontend/src/pages/Phone.jsx       the phone sender page (route /phone)
scripts/simulate_device.py fake WiFi phone for testing
```

## Run it

```bash
# backend
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # or .venv/bin/pip on mac/linux
cd ..
python scripts/make_cert.py          # once: self-signed cert with your LAN IP -> HTTPS on :8443
backend/.venv/Scripts/python backend/run.py   # HTTP on :8000 and HTTPS on :8443, one process

# frontend (production build, served by FastAPI at http://localhost:8000)
cd frontend
npm install && npm run build
```

For frontend hot reload use `npm run dev` (Vite on :5173, proxies `/api` and `/ws` to :8000).

Test without a phone:

```bash
python scripts/simulate_device.py --name "Sim 1" --hz 20
```

## Getting phones on — the sensor needs HTTPS

Mobile browsers only expose motion sensors on a **secure context**; over plain
`http://` the API simply isn't there (Chrome hides it, Safari never prompts).
iOS additionally shows a permission prompt, which the *Start* button triggers.

The dashboard's QR code points at the backend's LAN IP (auto-detected via
`/api/join-info`), preferring the HTTPS listener. Options, in order of ease:

1. **Self-signed LAN cert (default).** `python scripts/make_cert.py` writes
   `backend/certs/` with your LAN IP in the SAN; `run.py` then also listens on
   `https://<lan-ip>:8443`. The phone shows a "connection not private" warning
   once — tap *Advanced → Proceed*. Regenerate the cert if your IP changes.
2. **Tunnel** (real cert, works off-network): `cloudflared tunnel --url http://localhost:8000`
   or `ngrok http 8000`, then paste the `https://` URL into the override box on
   the dashboard.
3. **Plain HTTP fallback.** `http://<lan-ip>:8000/phone` still connects and
   counts as a device; since the sensor is unavailable the page shows a touch pad
   that sends x/y from your finger (`source: "touch"` on each message).

## Bluetooth

A phone's browser cannot *be* a BLE peripheral, so the roles are flipped: the
**backend host advertises as a BLE peripheral** ("Motion Hub", GATT service
`FFE0`), and the phone page connects to it with **Web Bluetooth** and writes
accelerometer samples into a characteristic. No app on the phone.

* **Android (Chrome)** — the phone page shows a WiFi / Bluetooth switch. Pick
  Bluetooth, tap *Start*, choose "Motion Hub" in the chooser. Device appears on
  the dashboard with the `bluetooth` badge. Web Bluetooth needs the HTTPS page.
* **iOS** — Safari has no Web Bluetooth, so the switch is disabled and iPhones
  send over WiFi. There is no app-free way around this on iOS.

The GATT server (`backend/app/ble_server.py`, using `bless`) starts with the app
whenever the host has a BLE adapter; set `BLE_SERVER=0` to disable. Windows
needs `pysetupdi` (in requirements). A phone that stops writing for
`BLE_STALE_S` (10 s) is dropped from the list.

Frames (little-endian): hello `FFE2` = `uint32 id + utf-8 name`; data `FFE1` =
`uint32 id + float32 x, y, z` (16 bytes, fits the default ATT MTU).

### Real BLE peripherals (ESP32, sensor tags)

The original central-role bridge is still there for hardware that advertises
`FFE0` and *notifies* on `FFE1` — opt in with `BLE_ENABLED=1`:

```powershell
$env:BLE_ENABLED="1"; backend\.venv\Scripts\python backend\run.py
```

`firmware/esp32_ble_accel/esp32_ble_accel.ino` is a matching ESP32 sketch
(synthetic data by default; MPU6050 on pins 21/22 with `USE_MPU6050`). Accepted
notification payloads: JSON `{"x","y","z"}`, three float32, or three int16
milli-g. Advertising (server) and scanning (bridge) at the same time works on
most adapters but is not guaranteed.

## Data storage & giving Claude access

Every registration and accepted sample is written to **`backend/data/motion.db`**
(SQLite, on this machine; `MOTION_DB` overrides the path). The device list and
recent history are reloaded from it on startup, so nothing is lost across
restarts. Tables: `devices`, `samples(device_id, ts, x, y, z, source)`,
`sessions(connected_at, disconnected_at)`.

`backend/mcp_server.py` is a local **MCP server** (stdio, read-only) so Claude
can analyse the data without it leaving the laptop. Tools: `list_devices`,
`get_samples`, `summarize`, `query` (single SELECT), `status`.

* **Claude Code** — picked up automatically from `.mcp.json` in the project root
  (approve it when prompted). Then ask e.g. *"summarize the last 10 minutes of
  motion data per device"*.
* **Claude Desktop** — add to `claude_desktop_config.json`:

  ```json
  "motion-hub": {
    "command": "C:/Users/hrish/src/iot-demo/backend/.venv/Scripts/python.exe",
    "args": ["C:/Users/hrish/src/iot-demo/backend/mcp_server.py"]
  }
  ```

The database can also be opened directly with any SQLite tool, or dumped:
`sqlite3 backend/data/motion.db ".mode csv" "SELECT * FROM samples"`.

## Protocol (WiFi path)

Client → `/ws/device`:

```json
{"type":"hello","device_id":"wifi-abc123","name":"iPhone","user_agent":"..."}
{"type":"motion","x":0.12,"y":-0.03,"z":9.79,"gx":0.1,"gy":0.0,"gz":9.8,"t":1712345678901}
```

Dashboard ← `/ws/dashboard`: `snapshot` on connect, then `device_connected`,
`device_disconnected`, `motion`.
