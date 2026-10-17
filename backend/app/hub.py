"""In-memory device registry + fan-out to dashboard clients.

Every ingest path (WebSocket over WiFi, the BLE GATT server, the BLE bridge)
talks to the hub through the same three calls: register / motion / unregister.
The hub tags each device with the protocol it arrived on, so the dashboard
never needs to know how a message got here.

Devices are never forgotten: a disconnect flips ``connected`` to False and the
next hello with the same id flips it back, keeping the sample history. Every
registration and accepted sample is also written through to SQLite (store.py),
and the registry is rebuilt from it on startup.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Literal, Optional

from fastapi import WebSocket

from .store import store

Protocol = Literal["wifi", "bluetooth"]

SAMPLE_INTERVAL_S = float(os.getenv("SAMPLE_INTERVAL_S", "1.0"))  # accept 1 sample / s / device
HISTORY_LEN = int(os.getenv("HISTORY_LEN", "120"))                # samples kept per device (~2 min)


@dataclass
class Device:
    device_id: str
    name: str
    protocol: Protocol
    user_agent: str = ""
    connected: bool = True
    first_seen: float = field(default_factory=time.time)
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    accepted: int = 0            # samples that passed the rate limit
    dropped: int = 0             # samples discarded by the rate limit
    last_sample: Optional[Dict[str, Any]] = None
    history: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=HISTORY_LEN), repr=False)
    _last_accept: float = field(default=0.0, repr=False)

    def accept(self, sample: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Apply the per-device rate limit. Returns the stored point, or None if dropped."""
        now = time.time()
        self.last_seen = now
        if now - self._last_accept < SAMPLE_INTERVAL_S:
            self.dropped += 1
            return None
        self._last_accept = now
        self.accepted += 1
        self.last_sample = sample
        point = {"ts": now, "x": sample.get("x", 0.0), "y": sample.get("y", 0.0), "z": sample.get("z", 0.0)}
        self.history.append(point)
        store.add_sample(self.device_id, now, point["x"], point["y"], point["z"], sample.get("source"))
        return point

    def to_dict(self, with_history: bool = False) -> Dict[str, Any]:
        d = {
            "device_id": self.device_id,
            "name": self.name,
            "protocol": self.protocol,
            "user_agent": self.user_agent,
            "connected": self.connected,
            "first_seen": self.first_seen,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "accepted": self.accepted,
            "dropped": self.dropped,
            "last_sample": self.last_sample,
        }
        if with_history:
            d["history"] = list(self.history)
        return d


class Hub:
    def __init__(self) -> None:
        self.devices: Dict[str, Device] = {}
        self.dashboards: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        """Rebuild known devices (all disconnected) and recent history from SQLite."""
        store.close_all_sessions()
        for row in store.load_devices():
            dev = Device(device_id=row["device_id"], name=row["name"], protocol=row["protocol"],
                         user_agent=row["user_agent"], connected=False,
                         first_seen=row["first_seen"], connected_at=row["first_seen"], last_seen=row["last_seen"])
            dev.accepted = store.sample_count(dev.device_id)
            for p in store.load_history(dev.device_id, HISTORY_LEN):
                dev.history.append(p)
            if dev.history:
                last = dev.history[-1]
                dev.last_sample = {"x": last["x"], "y": last["y"], "z": last["z"]}
            self.devices[dev.device_id] = dev

    # ---- device lifecycle -------------------------------------------------

    async def register(self, device_id: str, name: str, protocol: Protocol,
                       user_agent: str = "") -> Device:
        async with self._lock:
            dev = self.devices.get(device_id)
            if dev is None:
                dev = Device(device_id=device_id, name=name or device_id,
                             protocol=protocol, user_agent=user_agent)
                self.devices[device_id] = dev
            else:
                dev.name = name or dev.name
                dev.protocol = protocol
                dev.user_agent = user_agent or dev.user_agent
                dev.connected = True
                dev.connected_at = time.time()
                dev.last_seen = dev.connected_at
            store.upsert_device(dev.device_id, dev.name, dev.protocol, dev.user_agent, dev.connected_at)
        await self.broadcast({"type": "device_connected", "device": dev.to_dict(with_history=True)})
        return dev

    async def unregister(self, device_id: str, owner: Optional[Device] = None) -> None:
        """Mark a device disconnected. With ``owner`` given, only if that is still the
        registered object - a reconnecting phone re-registers under the same id, and
        the stale connection's cleanup must not knock the fresh one offline."""
        async with self._lock:
            dev = self.devices.get(device_id)
            if dev is None or not dev.connected or (owner is not None and dev is not owner):
                return
            dev.connected = False
            dev.last_seen = time.time()
            store.close_session(device_id, dev.last_seen)
        await self.broadcast({"type": "device_disconnected", "device_id": device_id, "last_seen": dev.last_seen})

    async def motion(self, device_id: str, sample: Dict[str, Any]) -> None:
        dev = self.devices.get(device_id)
        if dev is None or not dev.connected:
            return
        point = dev.accept(sample)
        if point is None:
            return
        await self.broadcast({
            "type": "motion",
            "device_id": device_id,
            "point": point,
            "sample": sample,
            "accepted": dev.accepted,
            "dropped": dev.dropped,
        })

    # ---- dashboard fan-out ------------------------------------------------

    @property
    def connected(self) -> list[Device]:
        return [d for d in self.devices.values() if d.connected]

    def snapshot(self) -> Dict[str, Any]:
        return {
            "type": "snapshot",
            "sample_interval_s": SAMPLE_INTERVAL_S,
            "devices": [d.to_dict(with_history=True) for d in self.devices.values()],
        }

    def counts(self) -> Dict[str, int]:
        by_proto = {"wifi": 0, "bluetooth": 0}
        for d in self.connected:
            by_proto[d.protocol] = by_proto.get(d.protocol, 0) + 1
        return {"total": len(self.connected), "known": len(self.devices), **by_proto}

    async def add_dashboard(self, ws: WebSocket) -> None:
        self.dashboards.add(ws)
        await ws.send_json(self.snapshot())

    def remove_dashboard(self, ws: WebSocket) -> None:
        self.dashboards.discard(ws)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        if not self.dashboards:
            return
        dead: list[WebSocket] = []
        for ws in list(self.dashboards):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.dashboards.discard(ws)


hub = Hub()
