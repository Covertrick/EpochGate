"""Byte-claim store.

This is the existing B3 behavior: authority is checked when the claim row is
committed, and recovery replays those stored bytes. It does not re-score the
payload. It is not a Semantic Claim protocol.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from frozen_proposals.db import read_authority


class ByteClaim:
    def commit(self, conn: sqlite3.Connection, request: Any, faults: Any) -> bool:
        faults.hit("before_db_transaction")
        conn.execute("BEGIN IMMEDIATE")
        try:
            faults.hit("inside_transaction")
            current = read_authority(conn)
            if (
                not current["eligible"]
                or current["token"] != request.authority_token
                or current["epoch"] != request.authority_epoch
            ):
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                """
                INSERT INTO byte_claims (
                    idempotency_key, payload, payload_sha256, authority_token, authority_epoch, status, receipt_id
                ) VALUES (?, ?, ?, ?, ?, 'CLAIMED', NULL)
                """,
                (
                    request.idempotency_key,
                    request.payload,
                    request.payload_sha256,
                    request.authority_token,
                    request.authority_epoch,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        faults.hit("after_commit_before_send")
        return True

    def has_row(self, conn: sqlite3.Connection, key: str) -> bool:
        row = conn.execute("SELECT 1 FROM byte_claims WHERE idempotency_key = ?", (key,)).fetchone()
        return row is not None

    def payload(self, conn: sqlite3.Connection, key: str) -> bytes:
        row = conn.execute("SELECT payload FROM byte_claims WHERE idempotency_key = ?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return bytes(row["payload"])

    def mark_sending(self, conn: sqlite3.Connection, key: str) -> None:
        conn.execute(
            "UPDATE byte_claims SET status = 'UNRESOLVED' WHERE idempotency_key = ?",
            (key,),
        )

    def acknowledge(self, conn: sqlite3.Connection, key: str, receipt_id: str | None) -> None:
        conn.execute(
            "UPDATE byte_claims SET status = 'ACKED', receipt_id = ? WHERE idempotency_key = ?",
            (receipt_id, key),
        )
