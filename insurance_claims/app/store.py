"""SQLite session store. Identity slots stay here and are not returned by the API."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class SessionStore:
    """One row per call. A process lock keeps a turn from interleaving with itself."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        return payload if isinstance(payload, dict) else None

    def save(self, session: dict[str, Any]) -> None:
        encoded = json.dumps(session)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, payload) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (session["id"], encoded),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
