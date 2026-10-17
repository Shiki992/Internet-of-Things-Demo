"""FastAPI entry point.

Endpoints
  GET  /api/health         liveness + device counts
  GET  /api/devices        current device list
  WS   /ws/device          phones (and any WiFi client) stream accelerometer data here
  WS   /ws/dashboard       React dashboard subscribes here for live events
  /*                       serves the built React app (dashboard at /, phone page at /phone)
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from .hub import hub

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("iot")

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


class Hello(BaseModel):
    type: str = Field(pattern="^hello$")
    device_id: Optional[str] = None
    name: str = "Phone"
    user_agent: str = ""


class Motion(BaseModel):
    type: str = Field(pattern="^motion$")
    x: float
    y: float
    z: float
    gx: Optional[float] = None   # acceleration including gravity, if available
    gy: Optional[float] = None
    gz: Optional[float] = None
    t: Optional[float] = None    # client timestamp (ms)
    source: Optional[str] = None  # "sensor" (default) or "touch" (HTTP fallback pad)


_ble_tasks: list = []
_lifespans = 0  # run.py serves HTTP + HTTPS from one app; start BLE once, not per listener


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _lifespans
    _lifespans += 1
    if _lifespans == 1:
        # GATT server for phones (Android Web Bluetooth writes to us). On by default.
        if os.getenv("BLE_SERVER", "1") == "1":
            from .ble_server import run_server
            _ble_tasks.append(asyncio.create_task(run_server()))
        # Central-role bridge for real BLE peripherals (ESP32 etc.). Opt-in.
        if os.getenv("BLE_ENABLED", "0") == "1":
            from .ble_bridge import run_bridge
            _ble_tasks.append(asyncio.create_task(run_bridge()))
    yield
    _lifespans -= 1
    if _lifespans == 0:
        for t in _ble_tasks:
            t.cancel()
        for t in _ble_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        _ble_tasks.clear()


app = FastAPI(title="IoT Accelerometer Demo", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "counts": hub.counts()}


def lan_ip() -> Optional[str]:
    """IP of the interface that routes to the internet (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


@app.get("/api/join-info")
async def join_info(request: Request) -> Dict[str, Any]:
    """Where phones on the same network should point; the dashboard builds its QR code from this."""
    ip = lan_ip()
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    scheme = request.url.scheme
    https_port = os.getenv("HTTPS_PORT") or (str(port) if scheme == "https" else "")
    http_url = f"http://{ip}:{port}" if ip and scheme == "http" else None
    https_url = f"https://{ip}:{https_port}" if ip and https_port else None
    return {
        "lan_ip": ip,
        "port": port,
        "http_url": http_url,
        "https_url": https_url,
        # Prefer HTTPS: phones only expose the motion sensor on secure pages.
        "lan_url": https_url or http_url,
        "secure": https_url is not None,
    }


@app.get("/api/devices")
async def devices() -> Dict[str, Any]:
    return {"counts": hub.counts(), "devices": [d.to_dict() for d in hub.devices.values()]}


@app.websocket("/ws/device")
async def ws_device(ws: WebSocket) -> None:
    await ws.accept()
    device_id: Optional[str] = None
    dev = None
    try:
        raw = await ws.receive_json()
        hello = Hello.model_validate(raw)
        device_id = hello.device_id or f"wifi-{uuid.uuid4().hex[:8]}"
        dev = await hub.register(device_id, hello.name, "wifi", hello.user_agent)
        await ws.send_json({"type": "welcome", "device_id": device_id})

        while True:
            raw = await ws.receive_json()
            try:
                m = Motion.model_validate(raw)
            except ValidationError:
                continue  # ignore malformed frames rather than dropping the phone
            await hub.motion(device_id, m.model_dump(exclude={"type"}, exclude_none=True))
    except (WebSocketDisconnect, ValidationError):
        pass
    except Exception as exc:
        log.warning("device socket error: %s", exc)
    finally:
        if device_id:
            await hub.unregister(device_id, owner=dev)


@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket) -> None:
    await ws.accept()
    await hub.add_dashboard(ws)
    try:
        while True:
            await ws.receive_text()  # dashboards only listen; keep the socket open
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove_dashboard(ws)


# ---- serve the built React app (run `npm run build` in frontend/) ----------
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
