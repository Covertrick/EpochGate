"""可控制的副作用接收器。接受次数以这里的持久行数为准。"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Any


class EffectSink:
    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._version = 1
        self._conn = sqlite3.connect(str(path or ":memory:"), check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accepted (
                idempotency_key TEXT PRIMARY KEY,
                state_version INTEGER NOT NULL,
                receipt TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accept_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                detail TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sink_epoch (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL
            )
            """
        )
        self._conn.execute("INSERT OR IGNORE INTO sink_epoch (id, version) VALUES (1, 1)")
        self._conn.commit()
        self._version = self._stored_version()

    def close(self) -> None:
        self._conn.close()

    def _stored_version(self) -> int:
        row = self._conn.execute("SELECT version FROM sink_epoch WHERE id = 1").fetchone()
        return 1 if row is None else int(row[0])

    def advance_to(self, target: int) -> int:
        """把版本镜像推进到账本写下的 pending。不接纳效果，也不把版本改小。"""
        with self._lock:
            current = self._stored_version()
            if target > current:
                self._conn.execute("UPDATE sink_epoch SET version = ? WHERE id = 1", (target,))
                self._conn.commit()
                current = target
            self._version = current
            return current

    def bump(self) -> None:
        with self._lock:
            current = self._stored_version() + 1
            self._conn.execute("UPDATE sink_epoch SET version = ? WHERE id = 1", (current,))
            self._conn.commit()
            self._version = current

    def accept(self, key: str, *, expected_version: int, payload: str) -> dict[str, Any]:
        with self._lock:
            self._version = self._stored_version()
            row = self._conn.execute(
                "SELECT receipt, payload FROM accepted WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                if row[1] != payload:
                    self._log(key, 0, "conflict")
                    return {"status": "conflict", "applied": False, "receipt": row[0], "reason": "payload"}
                self._log(key, 0, "replay")
                return {"status": "replayed", "applied": False, "receipt": row[0]}
            if expected_version != self._version:
                self._log(key, 0, "version")
                return {"status": "rejected", "applied": False, "reason": "version"}
            receipt = hashlib.sha256(f"{key}:{payload}".encode("utf-8")).hexdigest()
            self._conn.execute(
                "INSERT INTO accepted (idempotency_key, state_version, receipt, payload) VALUES (?, ?, ?, ?)",
                (key, expected_version, receipt, payload),
            )
            self._log(key, 1, "accepted")
            self._conn.commit()
            return {"status": "accepted", "applied": True, "receipt": receipt}

    def get(self, key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT receipt, payload, state_version FROM accepted WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return {"receipt": row[0], "payload": row[1], "state_version": row[2]}

    def accepted_count(self, key: str | None = None) -> int:
        if key is None:
            row = self._conn.execute("SELECT COUNT(*) FROM accept_log WHERE accepted = 1").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM accept_log WHERE accepted = 1 AND idempotency_key = ?",
                (key,),
            ).fetchone()
        return int(row[0])

    def _log(self, key: str, accepted: int, detail: str) -> None:
        self._conn.execute(
            "INSERT INTO accept_log (idempotency_key, accepted, detail) VALUES (?, ?, ?)",
            (key, accepted, detail),
        )
        self._conn.commit()
