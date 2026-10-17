"""Optional Bluetooth Low Energy ingest.

A phone's browser cannot act as a BLE *peripheral*, so this path is for real BLE
devices: an ESP32 / Arduino / sensor tag, or a phone running a GATT-server app
(e.g. nRF Connect's "Server" tab) that advertises the service below.

The bridge runs on the machine hosting the backend, scans for peripherals that
advertise ``BLE_SERVICE_UUID``, subscribes to notifications on ``BLE_CHAR_UUID``
and pushes every notification into the hub tagged ``protocol="bluetooth"``.

Accepted notification payloads:
  * JSON text        -> {"x": 0.1, "y": 0.2, "z": 9.8}
  * 12 bytes         -> three little-endian float32 (x, y, z)
  * 6 bytes          -> three little-endian int16, milli-g

Enabled with ``BLE_ENABLED=1``. Requires ``pip install bleak`` and a BLE adapter.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
from typing import Any, Dict, Optional

from .hub import hub

log = logging.getLogger("ble")

SERVICE_UUID = os.getenv("BLE_SERVICE_UUID", "0000ffe0-0000-1000-8000-00805f9b34fb")
CHAR_UUID = os.getenv("BLE_CHAR_UUID", "0000ffe1-0000-1000-8000-00805f9b34fb")
SCAN_INTERVAL_S = float(os.getenv("BLE_SCAN_INTERVAL", "5"))


def parse_payload(data: bytes) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(data.decode("utf-8"))
        if isinstance(obj, dict) and {"x", "y", "z"} <= obj.keys():
            return {"x": float(obj["x"]), "y": float(obj["y"]), "z": float(obj["z"])}
    except (UnicodeDecodeError, ValueError):
        pass
    if len(data) == 12:
        x, y, z = struct.unpack("<fff", data)
        return {"x": x, "y": y, "z": z}
    if len(data) == 6:
        x, y, z = struct.unpack("<hhh", data)
        return {"x": x / 1000 * 9.81, "y": y / 1000 * 9.81, "z": z / 1000 * 9.81}
    return None


async def _serve_device(address: str, name: str) -> None:
    from bleak import BleakClient

    device_id = f"ble-{address.replace(':', '').lower()}"
    disconnected = asyncio.Event()

    def on_disconnect(_client: Any) -> None:
        disconnected.set()

    def on_notify(_handle: Any, data: bytearray) -> None:
        sample = parse_payload(bytes(data))
        if sample is not None:
            asyncio.get_running_loop().create_task(hub.motion(device_id, sample))

    try:
        async with BleakClient(address, disconnected_callback=on_disconnect) as client:
            await hub.register(device_id, name or address, "bluetooth", user_agent="BLE peripheral")
            await client.start_notify(CHAR_UUID, on_notify)
            log.info("BLE connected: %s (%s)", name, address)
            await disconnected.wait()
    except Exception as exc:  # keep the scanner alive whatever a peripheral does
        log.warning("BLE device %s dropped: %s", address, exc)
    finally:
        await hub.unregister(device_id)
        log.info("BLE disconnected: %s", address)


async def run_bridge() -> None:
    try:
        from bleak import BleakScanner
    except ImportError:
        log.error("BLE_ENABLED=1 but 'bleak' is not installed; skipping BLE bridge")
        return

    active: Dict[str, asyncio.Task[None]] = {}
    log.info("BLE bridge scanning for service %s", SERVICE_UUID)
    while True:
        try:
            found = await BleakScanner.discover(timeout=SCAN_INTERVAL_S, service_uuids=[SERVICE_UUID])
            for dev in found:
                task = active.get(dev.address)
                if task is None or task.done():
                    active[dev.address] = asyncio.create_task(_serve_device(dev.address, dev.name or ""))
        except Exception as exc:
            log.warning("BLE scan failed: %s", exc)
            await asyncio.sleep(SCAN_INTERVAL_S)
