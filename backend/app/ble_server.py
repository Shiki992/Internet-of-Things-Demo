"""Bluetooth ingest for phones: the host acts as a BLE *peripheral*.

Phones can't be BLE peripherals from a browser, but Android Chrome's Web
Bluetooth API can connect to one. So the host advertises a GATT service named
"Motion Hub"; the phone page connects to it and *writes* accelerometer samples
into a characteristic. No app needed on the phone. (iOS Safari has no Web
Bluetooth, so iPhones use the WiFi path.)

Service  FFE0
  FFE1  data   write / write-without-response, 16 bytes:
                uint32 LE device hash | float32 LE x | float32 LE y | float32 LE z
  FFE2  hello  write, <= 20 bytes (fits the default ATT MTU):
                uint32 LE device hash | utf-8 name (rest)

The hash is derived on the phone from its persistent device id; hello maps it to
a name so data frames stay small. A device with no frames for STALE_S seconds is
unregistered - GATT servers on Windows don't reliably report disconnects.

Enabled by default when `bless` imports; disable with BLE_SERVER=0.
"""
from __future__ import annotations

import asyncio
import logging
import os
import struct
import time
from typing import Any, Dict

from .hub import hub

log = logging.getLogger("ble-server")

SERVICE_UUID = os.getenv("BLE_SERVICE_UUID", "0000ffe0-0000-1000-8000-00805f9b34fb")
DATA_UUID = os.getenv("BLE_CHAR_UUID", "0000ffe1-0000-1000-8000-00805f9b34fb")
HELLO_UUID = os.getenv("BLE_HELLO_UUID", "0000ffe2-0000-1000-8000-00805f9b34fb")
LOCAL_NAME = os.getenv("BLE_NAME", "Motion Hub")
STALE_S = float(os.getenv("BLE_STALE_S", "10"))


class _State:
    loop: asyncio.AbstractEventLoop
    names: Dict[int, str] = {}       # hash -> name from hello
    last_seen: Dict[int, float] = {}


state = _State()


def _device_id(h: int) -> str:
    return f"ble-{h:08x}"


async def _on_hello(h: int, name: str) -> None:
    state.names[h] = name or _device_id(h)
    state.last_seen[h] = time.time()
    dev = hub.devices.get(_device_id(h))
    if dev is None or not dev.connected:
        await hub.register(_device_id(h), state.names[h], "bluetooth", user_agent="Web Bluetooth")
        log.info("BLE phone joined: %s", state.names[h])


async def _on_data(h: int, x: float, y: float, z: float) -> None:
    did = _device_id(h)
    dev = hub.devices.get(did)
    if dev is None or not dev.connected:
        # data before hello (e.g. server restarted mid-session): register with a placeholder name
        await _on_hello(h, state.names.get(h, ""))
    state.last_seen[h] = time.time()
    await hub.motion(did, {"x": x, "y": y, "z": z})


def _write_request(characteristic: Any, value: bytearray, **_: Any) -> None:
    """Called by bless from the WinRT thread; hop onto the asyncio loop."""
    data = bytes(value)
    uuid = str(characteristic.uuid).lower()
    try:
        if uuid == DATA_UUID and len(data) >= 16:
            h, x, y, z = struct.unpack("<Ifff", data[:16])
            coro = _on_data(h, x, y, z)
        elif uuid == HELLO_UUID and len(data) >= 4:
            (h,) = struct.unpack("<I", data[:4])
            coro = _on_hello(h, data[4:].decode("utf-8", "replace").strip("\x00"))
        else:
            return
    except struct.error:
        return
    state.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(coro))


async def _reaper() -> None:
    while True:
        await asyncio.sleep(2)
        cutoff = time.time() - STALE_S
        for h, seen in list(state.last_seen.items()):
            if seen < cutoff:
                state.last_seen.pop(h, None)
                await hub.unregister(_device_id(h))
                log.info("BLE phone timed out: %s", state.names.get(h, _device_id(h)))


async def run_server() -> None:
    try:
        from bless import BlessServer, GATTAttributePermissions, GATTCharacteristicProperties
    except ImportError as exc:
        log.error("BLE server disabled: %s (pip install bless)", exc)
        return

    state.loop = asyncio.get_running_loop()
    server = BlessServer(name=LOCAL_NAME)
    server.write_request_func = _write_request

    await server.add_new_service(SERVICE_UUID)
    await server.add_new_characteristic(
        SERVICE_UUID, DATA_UUID,
        GATTCharacteristicProperties.write | GATTCharacteristicProperties.write_without_response,
        None, GATTAttributePermissions.writeable,
    )
    await server.add_new_characteristic(
        SERVICE_UUID, HELLO_UUID,
        GATTCharacteristicProperties.write,
        None, GATTAttributePermissions.writeable,
    )
    await server.start()
    log.info("BLE GATT server advertising as %r (service %s)", LOCAL_NAME, SERVICE_UUID)

    reaper = asyncio.create_task(_reaper())
    try:
        await asyncio.Event().wait()  # run until cancelled
    finally:
        reaper.cancel()
        try:
            await server.stop()
        except Exception as exc:
            log.debug("BLE server stop: %s", exc)
