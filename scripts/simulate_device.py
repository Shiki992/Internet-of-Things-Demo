"""Fake WiFi phone for testing without a real handset.

    python scripts/simulate_device.py --name "Sim 1" --hz 20 --url ws://localhost:8000/ws/device
"""
import argparse
import asyncio
import json
import math
import time

import websockets  # installed with uvicorn[standard]


async def run(url: str, name: str, hz: float, device_id: str | None) -> None:
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "hello", "device_id": device_id, "name": name, "user_agent": "simulator"}))
        print("joined:", await ws.recv())
        t0 = time.time()
        while True:
            t = time.time() - t0
            await ws.send(json.dumps({
                "type": "motion",
                "x": round(2.0 * math.sin(t * 2.0), 3),
                "y": round(1.5 * math.cos(t * 1.3), 3),
                "z": round(9.81 + 0.5 * math.sin(t * 5.0), 3),
                "t": int(time.time() * 1000),
            }))
            await asyncio.sleep(1 / hz)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="ws://localhost:8000/ws/device")
    p.add_argument("--name", default="Simulated phone")
    p.add_argument("--id", default=None)
    p.add_argument("--hz", type=float, default=20)
    a = p.parse_args()
    asyncio.run(run(a.url, a.name, a.hz, a.id))
