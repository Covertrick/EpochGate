"""SQLite connection patterned on RunLedger: busy_timeout 5000, explicit transactions.

Week 2 uses isolation_level None so BEGIN/COMMIT boundaries are the benchmark's,
not the sqlite3 connection's implicit transactions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SYNC_NAMES = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def pragma_snapshot(conn: sqlite3.Connection) -> dict[str, str]:
    journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
    sync = int(conn.execute("PRAGMA synchronous").fetchone()[0])
    return {"journal_mode": journal, "synchronous": f"{sync}:{SYNC_NAMES.get(sync, 'UNKNOWN')}"}


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS authority (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            token INTEGER NOT NULL,
            epoch INTEGER NOT NULL,
            eligible INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS authority_log (
            seq INTEGER PRIMARY KEY,
            token INTEGER NOT NULL,
            epoch INTEGER NOT NULL,
            eligible INTEGER NOT NULL,
            event TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS business_effects (
            idempotency_key TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            payload BLOB NOT NULL,
            payload_sha256 TEXT NOT NULL,
            authority_token INTEGER NOT NULL,
            authority_epoch INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outbox (
            event_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload BLOB NOT NULL,
            payload_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL,
            receipt_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS byte_claims (
            idempotency_key TEXT PRIMARY KEY,
            payload BLOB NOT NULL,
            payload_sha256 TEXT NOT NULL,
            authority_token INTEGER NOT NULL,
            authority_epoch INTEGER NOT NULL,
            status TEXT NOT NULL,
            receipt_id TEXT
        )
        """
    )


def read_authority(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT token, epoch, eligible FROM authority WHERE id = 1").fetchone()
    if row is None:
        raise RuntimeError("authority row missing")
    return {"token": int(row["token"]), "epoch": int(row["epoch"]), "eligible": bool(row["eligible"])}


def authority_log(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT seq, token, epoch, eligible, event FROM authority_log ORDER BY seq"
    ).fetchall()
    return [
        {
            "seq": int(row["seq"]),
            "token": int(row["token"]),
            "epoch": int(row["epoch"]),
            "eligible": bool(row["eligible"]),
            "event": row["event"],
        }
        for row in rows
    ]
