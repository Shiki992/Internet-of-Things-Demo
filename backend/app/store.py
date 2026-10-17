"""SQLite persistence for devices and accepted samples.

Single-file, on-device database at backend/data/motion.db (override with
MOTION_DB). The hub writes every registration and every accepted sample here
and reloads known devices + recent history on startup, so the dashboard's
device list survives restarts. mcp_server.py reads the same file.

Volume is tiny (1 sample / s / device), so writes are synchronous on the event
loop with autocommit; WAL mode keeps concurrent readers (the MCP server) from
blocking the writer.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DB_PATH = Path(os.getenv("MOTION_DB") or Path(__file__).resolve().parent.parent / "data" / "motion.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    protocol    TEXT NOT NULL,          -- 'wifi' | 'bluetooth'
    user_agent  TEXT NOT NULL DEFAULT '',
    first_seen  REAL NOT NULL,          -- unix seconds
    last_seen   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
    id        INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    ts        REAL NOT NULL,            -- unix seconds, server receive time
    x         REAL NOT NULL,            -- m/s^2
    y         REAL NOT NULL,
    z         REAL NOT NULL,
    source    TEXT                      -- NULL = motion sensor, 'touch' = HTTP fallback pad
);
CREATE INDEX IF NOT EXISTS samples_device_ts ON samples(device_id, ts);
CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY,
    device_id    TEXT NOT NULL REFERENCES devices(device_id),
    connected_at REAL NOT NULL,
    disconnected_at REAL
);
"""


class Store:
    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._open_sessions: Dict[str, int] = {}

    # ---- writes ----------------------------------------------------------

    def upsert_device(self, device_id: str, name: str, protocol: str, user_agent: str, now: float) -> None:
        self.conn.execute(
            """INSERT INTO devices(device_id, name, protocol, user_agent, first_seen, last_seen)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(device_id) DO UPDATE SET
                 name=excluded.name, protocol=excluded.protocol,
                 user_agent=CASE WHEN excluded.user_agent='' THEN devices.user_agent ELSE excluded.user_agent END,
                 last_seen=excluded.last_seen""",
            (device_id, name, protocol, user_agent, now, now),
        )
        cur = self.conn.execute("INSERT INTO sessions(device_id, connected_at) VALUES(?,?)", (device_id, now))
        self._open_sessions[device_id] = cur.lastrowid

    def close_session(self, device_id: str, now: float) -> None:
        sid = self._open_sessions.pop(device_id, None)
        if sid is not None:
            self.conn.execute("UPDATE sessions SET disconnected_at=? WHERE id=?", (now, sid))
        self.conn.execute("UPDATE devices SET last_seen=? WHERE device_id=?", (now, device_id))

    def add_sample(self, device_id: str, ts: float, x: float, y: float, z: float, source: Optional[str]) -> None:
        self.conn.execute(
            "INSERT INTO samples(device_id, ts, x, y, z, source) VALUES(?,?,?,?,?,?)",
            (device_id, ts, x, y, z, source),
        )
        self.conn.execute("UPDATE devices SET last_seen=? WHERE device_id=?", (ts, device_id))

    def close_all_sessions(self, now: Optional[float] = None) -> None:
        """On startup: anything left open from a previous run ended when that run did."""
        self.conn.execute(
            "UPDATE sessions SET disconnected_at=COALESCE("
            "  (SELECT MAX(ts) FROM samples s WHERE s.device_id=sessions.device_id AND s.ts>=sessions.connected_at),"
            "  connected_at) WHERE disconnected_at IS NULL"
        )
        self._open_sessions.clear()

    # ---- reads (used by the hub on startup) ---------------------------------

    def load_devices(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT device_id, name, protocol, user_agent, first_seen, last_seen FROM devices ORDER BY first_seen"
        ).fetchall()
        return [dict(zip(("device_id", "name", "protocol", "user_agent", "first_seen", "last_seen"), r)) for r in rows]

    def load_history(self, device_id: str, limit: int) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT ts, x, y, z FROM samples WHERE device_id=? ORDER BY ts DESC LIMIT ?", (device_id, limit)
        ).fetchall()
        return [{"ts": r[0], "x": r[1], "y": r[2], "z": r[3]} for r in reversed(rows)]

    def sample_count(self, device_id: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM samples WHERE device_id=?", (device_id,)).fetchone()[0]


store = Store()
