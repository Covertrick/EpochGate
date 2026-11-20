"""Read the target ledger. This module is the auditor, not the path under test."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def read_target_ledger(db_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT effect_key, payload, payload_hash, world_version, authority_token,
                   authority_epoch, receipt_id, logical_effect_count
            FROM target_effects
            ORDER BY effect_key
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "effect_key": row["effect_key"],
            "payload": bytes(row["payload"]),
            "payload_hash": row["payload_hash"],
            "world_version": int(row["world_version"]),
            "authority_token": int(row["authority_token"]),
            "authority_epoch": int(row["authority_epoch"]),
            "receipt_id": row["receipt_id"],
            "logical_effect_count": int(row["logical_effect_count"]),
        }
        for row in rows
    ]


def effect_for(db_path: Path, effect_key: str) -> dict[str, Any] | None:
    for row in read_target_ledger(db_path):
        if row["effect_key"] == effect_key:
            return row
    return None
