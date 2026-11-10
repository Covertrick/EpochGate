"""runtime.v1 事件在离开账本前要过 schema。观测插件不参与授权。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema


def schema_path() -> Path:
    override = Path(__file__).resolve()
    candidates = [Path.cwd() / "packages" / "contracts" / "schemas" / "runtime-event.schema.json"]
    candidates.extend(parent / "packages" / "contracts" / "schemas" / "runtime-event.schema.json" for parent in override.parents)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("runtime-event.schema.json")


def load_schema() -> dict[str, Any]:
    return json.loads(schema_path().read_text(encoding="utf-8"))


def validate_runtime_event(event: dict[str, Any], schema: dict[str, Any] | None = None) -> None:
    jsonschema.validate(event, schema or load_schema())


def envelope(run_id: str, row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") or {}
    effect = payload.get("effect_id") or payload.get("idempotency_key")
    event = {
        "schema_version": "runtime.v1",
        "event_id": f"{run_id}:{row['seq']}",
        "run_id": run_id,
        "attempt_id": payload.get("attempt_id"),
        "effect_id": effect,
        "epoch": payload.get("fencing_token"),
        "event_type": row["event_type"],
        "timestamp": row["recorded_at"],
        "ledger_sequence": row["seq"],
    }
    validate_runtime_event(event)
    return event
