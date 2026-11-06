"""原始事件 JSONL 与只追加的 SQLite 索引。索引不能改写原始事件。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "eg2.events.v1"
REQUIRED = (
    "schema_version",
    "episode_id",
    "session_id",
    "event_seq",
    "event_type",
    "timestamp_utc",
    "status",
    "payload",
)
STATUSES = {"success", "error", "refused", "started"}


def make_event(
    *,
    episode_id: str,
    session_id: str,
    event_seq: int,
    event_type: str,
    status: str,
    payload: dict[str, Any],
    tool_call_id: str = "",
) -> dict[str, Any]:
    event = {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "session_id": session_id,
        "event_seq": event_seq,
        "event_type": event_type,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "monotonic_ns": time.monotonic_ns(),
        "status": status,
        "payload": payload,
    }
    if tool_call_id:
        event["tool_call_id"] = tool_call_id
    return event


def validate_event(event: dict[str, Any]) -> list[str]:
    errors = [f"missing {key}" for key in REQUIRED if key not in event]
    if event.get("schema_version") not in (None, SCHEMA_VERSION) and "schema_version" in event:
        errors.append("schema_version")
    if event.get("schema_version") != SCHEMA_VERSION and "schema_version" in event:
        errors.append("schema_version")
    if not isinstance(event.get("event_seq"), int) or event.get("event_seq", 0) < 1:
        errors.append("event_seq")
    if event.get("status") not in STATUSES:
        errors.append("status")
    if not isinstance(event.get("payload"), dict):
        errors.append("payload")
    return errors


def spill_large_output(event: dict[str, Any], artifact_dir: Path, limit: int = 2000) -> dict[str, Any]:
    payload = dict(event["payload"])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for field in ("stdout", "stderr"):
        text = payload.get(field)
        if not isinstance(text, str) or len(text) <= limit:
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        target = artifact_dir / f"{event['event_seq']}-{field}-{digest[:12]}.txt"
        target.write_text(text, encoding="utf-8")
        payload.pop(field)
        payload[f"{field}_ref"] = target.name
        payload[f"{field}_sha256"] = digest
    copied = dict(event)
    copied["payload"] = payload
    return copied


def append_event(jsonl_path: Path, event: dict[str, Any]) -> None:
    errors = validate_event(event)
    if errors:
        raise ValueError(",".join(errors))
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def next_seq(jsonl_path: Path) -> int:
    if not jsonl_path.exists():
        return 1
    lines = [line for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return len(lines) + 1


def index_event(db_path: Path, event: dict[str, Any]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                episode_id TEXT NOT NULL,
                event_seq INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (episode_id, event_seq)
            )
            """
        )
        conn.execute(
            "INSERT INTO events (episode_id, event_seq, event_type, status) VALUES (?, ?, ?, ?)",
            (event["episode_id"], event["event_seq"], event["event_type"], event["status"]),
        )
        conn.commit()
