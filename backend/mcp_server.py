"""MCP server exposing the Motion Hub sensor database to Claude.

Runs locally over stdio and opens backend/data/motion.db read-only, so Claude
can list devices, pull samples, get per-window statistics, or run its own
SELECTs - without anything leaving the machine.

Registered for Claude Code by .mcp.json in the project root. For Claude Desktop
add to claude_desktop_config.json:

    "motion-hub": {
      "command": "C:/Users/hrish/src/iot-demo/backend/.venv/Scripts/python.exe",
      "args": ["C:/Users/hrish/src/iot-demo/backend/mcp_server.py"]
    }
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

DB_PATH = Path(os.getenv("MOTION_DB") or Path(__file__).resolve().parent / "data" / "motion.db")

mcp = MCPServer(
    "motion-hub",
    instructions=(
        "Read-only access to accelerometer samples collected by the Motion Hub demo. "
        "Devices are phones (protocol 'wifi' via WebSocket, 'bluetooth' via Web Bluetooth) "
        "or BLE peripherals. Samples are at most 1 per second per device; x/y/z are m/s^2 "
        "(acceleration without gravity where the phone provides it). Timestamps are unix seconds."
    ),
)


def _conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise ToolError(f"database not found at {DB_PATH}; start the backend once to create it")
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds") if ts else None


def _parse_when(value: Optional[str | float], default: float) -> float:
    """Accepts unix seconds, ISO-8601, or relative like '-10m' / '-2h' / '-1d'."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    v = value.strip()
    if v.startswith("-") and v[-1] in "smhd":
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[v[-1]]
        return time.time() - float(v[1:-1]) * mult
    try:
        return float(v)
    except ValueError:
        return datetime.fromisoformat(v).timestamp()


@mcp.tool()
def list_devices() -> List[Dict[str, Any]]:
    """Every device ever seen, with protocol, sample count, and first/last seen times."""
    with _conn() as c:
        rows = c.execute(
            """SELECT d.device_id, d.name, d.protocol, d.user_agent, d.first_seen, d.last_seen,
                      (SELECT COUNT(*) FROM samples s WHERE s.device_id=d.device_id) AS samples,
                      (SELECT COUNT(*) FROM sessions se WHERE se.device_id=d.device_id) AS sessions
               FROM devices d ORDER BY d.first_seen"""
        ).fetchall()
    return [
        {**dict(r), "first_seen_iso": _iso(r["first_seen"]), "last_seen_iso": _iso(r["last_seen"])}
        for r in rows
    ]


@mcp.tool()
def get_samples(
    device_id: str,
    since: Optional[str] = "-10m",
    until: Optional[str] = None,
    limit: int = 1000,
) -> Dict[str, Any]:
    """Raw x/y/z samples for one device in a time range.

    since/until accept unix seconds, ISO-8601, or relative offsets like '-30s', '-10m', '-2h', '-1d'.
    Returns at most `limit` rows (newest first if truncated).
    """
    t0 = _parse_when(since, 0.0)
    t1 = _parse_when(until, time.time())
    limit = max(1, min(int(limit), 20000))
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, x, y, z, source FROM samples WHERE device_id=? AND ts BETWEEN ? AND ? "
            "ORDER BY ts DESC LIMIT ?",
            (device_id, t0, t1, limit),
        ).fetchall()
        total = c.execute(
            "SELECT COUNT(*) FROM samples WHERE device_id=? AND ts BETWEEN ? AND ?", (device_id, t0, t1)
        ).fetchone()[0]
    samples = [dict(r) | {"iso": _iso(r["ts"])} for r in reversed(rows)]
    return {"device_id": device_id, "from": _iso(t0) if t0 else None, "to": _iso(t1),
            "total_in_range": total, "returned": len(samples), "samples": samples}


@mcp.tool()
def summarize(device_id: Optional[str] = None, since: Optional[str] = "-10m", until: Optional[str] = None) -> List[Dict[str, Any]]:
    """Per-device statistics over a window: count, mean/min/max per axis, mean and peak |a|.

    Omit device_id for all devices. since/until as in get_samples.
    """
    t0 = _parse_when(since, 0.0)
    t1 = _parse_when(until, time.time())
    with _conn() as c:
        q = "SELECT device_id, ts, x, y, z FROM samples WHERE ts BETWEEN ? AND ?"
        args: list = [t0, t1]
        if device_id:
            q += " AND device_id=?"
            args.append(device_id)
        rows = c.execute(q + " ORDER BY device_id, ts", args).fetchall()
        names = {r["device_id"]: (r["name"], r["protocol"]) for r in c.execute("SELECT device_id, name, protocol FROM devices")}

    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        s = out.setdefault(r["device_id"], {"n": 0, "sum": [0, 0, 0], "min": [math.inf] * 3, "max": [-math.inf] * 3,
                                            "mag_sum": 0.0, "mag_peak": 0.0, "first": r["ts"], "last": r["ts"]})
        v = (r["x"], r["y"], r["z"])
        s["n"] += 1
        for i in range(3):
            s["sum"][i] += v[i]
            s["min"][i] = min(s["min"][i], v[i])
            s["max"][i] = max(s["max"][i], v[i])
        mag = math.sqrt(sum(a * a for a in v))
        s["mag_sum"] += mag
        s["mag_peak"] = max(s["mag_peak"], mag)
        s["last"] = r["ts"]

    result = []
    for did, s in out.items():
        n = s["n"]
        name, proto = names.get(did, (did, "?"))
        result.append({
            "device_id": did, "name": name, "protocol": proto, "samples": n,
            "from": _iso(s["first"]), "to": _iso(s["last"]),
            "mean": {k: round(s["sum"][i] / n, 3) for i, k in enumerate("xyz")},
            "min": {k: round(s["min"][i], 3) for i, k in enumerate("xyz")},
            "max": {k: round(s["max"][i], 3) for i, k in enumerate("xyz")},
            "magnitude_mean": round(s["mag_sum"] / n, 3),
            "magnitude_peak": round(s["mag_peak"], 3),
        })
    return result


@mcp.tool()
def query(sql: str, limit: int = 500) -> Dict[str, Any]:
    """Run a read-only SQL SELECT against the database (tables: devices, samples, sessions).

    Schema: devices(device_id, name, protocol, user_agent, first_seen, last_seen);
    samples(id, device_id, ts, x, y, z, source); sessions(id, device_id, connected_at, disconnected_at).
    Times are unix seconds. Only a single SELECT/WITH statement is allowed.
    """
    stmt = sql.strip().rstrip(";")
    if ";" in stmt or not stmt.lower().lstrip().startswith(("select", "with")):
        raise ToolError("only a single SELECT statement is allowed (the database is opened read-only)")
    limit = max(1, min(int(limit), 5000))
    with _conn() as c:
        try:
            cur = c.execute(f"SELECT * FROM ({stmt}) LIMIT {limit}")
        except sqlite3.Error as exc:
            raise ToolError(f"SQL error: {exc}") from exc
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return {"columns": cols, "rows": rows, "returned": len(rows), "limit": limit}


@mcp.tool()
def status() -> Dict[str, Any]:
    """Database location, size, totals, and whether the backend is currently connected to anything."""
    with _conn() as c:
        devices = c.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        samples = c.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        live = c.execute("SELECT COUNT(*) FROM sessions WHERE disconnected_at IS NULL").fetchone()[0]
        newest = c.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
    return {
        "db_path": str(DB_PATH), "db_bytes": DB_PATH.stat().st_size,
        "devices": devices, "samples": samples, "open_sessions": live,
        "newest_sample": _iso(newest),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
