"""Separate-process local receiver. Not a third-party service.

Submit with fence=True checks world version, authority, payload hash, and the
existing effect inside one SQLite transaction. Unfenced submit only applies
the idempotency rule. The path process does not open this database.
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mechanism_validation.runtime.hashing import sha256_bytes
from mechanism_validation.runtime.sqlite_util import open_sqlite

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "architecture" / "target_ledger_schema.sql"
RECEIVER_VERSION = "week5-local-receiver-1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    if conn.execute("SELECT COUNT(*) AS n FROM receiver_world").fetchone()["n"] == 0:
        conn.execute("INSERT INTO receiver_world (id, version) VALUES (1, 1)")
    if conn.execute("SELECT COUNT(*) AS n FROM receiver_authority").fetchone()["n"] == 0:
        conn.execute(
            "INSERT INTO receiver_authority (id, token, epoch, eligible) VALUES (1, 1, 1, 1)"
        )


def _effect_count(row: sqlite3.Row | None) -> int:
    if row is None:
        return 0
    return int(row["logical_effect_count"])


def apply_submit(conn: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    """One transaction. A rejected fence does not insert a target effect."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        world = conn.execute("SELECT version FROM receiver_world WHERE id = 1").fetchone()
        authority = conn.execute(
            "SELECT token, epoch, eligible FROM receiver_authority WHERE id = 1"
        ).fetchone()
        current_version = int(world["version"])
        payload = base64.b64decode(request["payload_b64"])
        payload_hash = sha256_bytes(payload)
        effect_key = str(request["effect_key"])
        fenced = bool(request.get("fence"))
        expected_version = request.get("expected_world_version")
        existing = conn.execute(
            """
            SELECT payload_hash, logical_effect_count, receipt_id
            FROM target_effects WHERE effect_key = ?
            """,
            (effect_key,),
        ).fetchone()
        outcome: str | None = None
        applied = False
        receipt = None
        if fenced:
            expected_auth = request.get("expected_authority") or {}
            expected_hash = request.get("expected_payload_hash")
            if expected_version is None:
                outcome = "UNKNOWN"
            elif int(expected_version) != current_version:
                outcome = "WORLD_VERSION_MISMATCH"
            elif (
                int(expected_auth.get("token", -1)) != int(authority["token"])
                or int(expected_auth.get("epoch", -1)) != int(authority["epoch"])
                or bool(expected_auth.get("eligible")) != bool(authority["eligible"])
            ):
                outcome = "AUTHORITY_SUPERSEDED"
            elif expected_hash != payload_hash:
                outcome = "PAYLOAD_HASH_MISMATCH"
        if outcome is None:
            if existing is None:
                receipt = sha256_bytes(f"{effect_key}:{payload_hash}:{current_version}".encode("utf-8"))
                conn.execute(
                    """
                    INSERT INTO target_effects (
                        effect_key, payload, payload_hash, world_version, authority_token,
                        authority_epoch, receipt_id, logical_effect_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        effect_key,
                        payload,
                        payload_hash,
                        current_version,
                        int(request.get("authority_token") or 0),
                        int(request.get("authority_epoch") or 0),
                        receipt,
                        _now(),
                    ),
                )
                outcome = "ACCEPTED"
                applied = True
                effect_count = 1
            elif existing["payload_hash"] == payload_hash:
                outcome = "REPLAY"
                receipt = existing["receipt_id"]
                effect_count = _effect_count(existing)
            else:
                conn.execute(
                    """
                    INSERT INTO target_conflicts (effect_key, payload_hash, existing_hash, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (effect_key, payload_hash, existing["payload_hash"], _now()),
                )
                outcome = "CONFLICT"
                effect_count = _effect_count(existing)
        else:
            effect_count = _effect_count(existing)
        conn.execute(
            """
            INSERT INTO target_attempts (
                attempt_id, effect_key, payload_hash, outcome, world_version_observed,
                expected_world_version, fenced, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(request["attempt_id"]),
                effect_key,
                payload_hash,
                outcome,
                current_version,
                None if expected_version is None else int(expected_version),
                1 if fenced else 0,
                _now(),
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return {
        "status": "PRESENT" if outcome in {"ACCEPTED", "REPLAY"} else outcome,
        "outcome": outcome,
        "applied": applied,
        "effect_count": effect_count,
        "payload_hash": payload_hash,
        "receipt": receipt,
        "world_version_observed": current_version,
    }


def apply_query(conn: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT payload_hash, logical_effect_count, receipt_id
        FROM target_effects WHERE effect_key = ?
        """,
        (str(request["effect_key"]),),
    ).fetchone()
    if row is None:
        return {"status": "ABSENT", "outcome": "ABSENT", "effect_count": 0, "applied": False}
    return {
        "status": "PRESENT",
        "outcome": "PRESENT",
        "effect_count": int(row["logical_effect_count"]),
        "payload_hash": row["payload_hash"],
        "receipt": row["receipt_id"],
        "applied": False,
    }


def apply_control(conn: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    op = request["op"]
    conn.execute("BEGIN IMMEDIATE")
    try:
        if op == "set_world_version":
            conn.execute("UPDATE receiver_world SET version = ? WHERE id = 1", (int(request["version"]),))
        elif op == "set_authority":
            conn.execute(
                "UPDATE receiver_authority SET token = ?, epoch = ?, eligible = ? WHERE id = 1",
                (int(request["token"]), int(request["epoch"]), 1 if request["eligible"] else 0),
            )
        elif op == "reset_effects":
            conn.execute("DELETE FROM target_effects")
            conn.execute("DELETE FROM target_attempts")
            conn.execute("DELETE FROM target_conflicts")
        else:
            raise ValueError(op)
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return {"status": "OK", "outcome": "OK"}


def apply_audit(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT effect_key, payload_hash, world_version, logical_effect_count, receipt_id
        FROM target_effects ORDER BY effect_key
        """
    ).fetchall()
    return {
        "status": "OK",
        "outcome": "AUDIT",
        "effects": [
            {
                "effect_key": row["effect_key"],
                "payload_hash": row["payload_hash"],
                "world_version": int(row["world_version"]),
                "logical_effect_count": int(row["logical_effect_count"]),
                "receipt_id": row["receipt_id"],
            }
            for row in rows
        ],
    }


def dispatch(conn: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    op = request.get("op")
    if op == "submit":
        return apply_submit(conn, request)
    if op == "query":
        return apply_query(conn, request)
    if op in {"set_world_version", "set_authority", "reset_effects"}:
        return apply_control(conn, request)
    if op == "audit":
        return apply_audit(conn)
    raise ValueError(f"unknown op {op}")


def _read_line(sock: socket.socket) -> str:
    data = b""
    while b"\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    return data.decode("utf-8").strip()


def serve(db_path: Path, portfile: Path) -> None:
    conn = open_sqlite(db_path)
    init_db(conn)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    port = listener.getsockname()[1]
    portfile.write_text(str(port), encoding="utf-8")
    try:
        while True:
            client, _addr = listener.accept()
            try:
                raw = _read_line(client)
                if not raw:
                    continue
                request = json.loads(raw)
                if request.get("op") == "shutdown":
                    client.sendall(b'{"status":"STOPPED","outcome":"STOPPED"}\n')
                    break
                response = dispatch(conn, request)
                client.sendall((json.dumps(response) + "\n").encode("utf-8"))
            finally:
                client.close()
    finally:
        listener.close()
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--portfile", required=True)
    args = parser.parse_args()
    serve(Path(args.db), Path(args.portfile))


if __name__ == "__main__":
    main()
