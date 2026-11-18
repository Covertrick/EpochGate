"""旧 E4 格子的补充状态向量。不改原来的终态正确率，也不改格子文件。"""

from __future__ import annotations

import json
from pathlib import Path


def outcome_known(record: dict) -> bool:
    """只看效果行、对账和拒绝事件。目标文件还在，不能单独把结果算成已知。"""
    effects = record.get("effects") or []
    reconcile = record.get("reconcile") or []
    events = record.get("event_types") or []
    if any(item.get("status") == "unknown" for item in reconcile):
        return False
    if any(row.get("effect_state") == "OUTCOME_UNKNOWN" or row.get("phase") == "unknown" for row in effects):
        return False
    if any(row.get("effect_state") == "DISPATCHED" and not row.get("receipt") for row in effects):
        return False
    if any(row.get("phase") == "completed" and row.get("receipt") for row in effects):
        return True
    if "result_withheld" in events or "commit_rejected" in events:
        return True
    if record.get("eligible") is False and all(row.get("effect_state") in {None, "INTENT_RECORDED"} for row in effects):
        return True
    return False


def state_vector(record: dict) -> dict:
    stale = bool(record.get("stale_effect_accepted"))
    file_missing = record.get("file_text") is None
    fault = record.get("fault")
    note = "claimed_before_replace" if fault == "receipt_loss" else None
    return {
        "task": record.get("task"),
        "fault": fault,
        "mechanism_label": "file_successor_token_refresh" if fault == "version_mismatch" else fault,
        "historical_fault_note": note,
        "arm": record.get("arm"),
        "repeat": record.get("repeat"),
        "task_success": bool(record.get("terminal_correct")),
        "stale_effect": stale,
        "successor_completed": bool(record.get("eligible_successor_completed")),
        "safety": not stale,
        "correct_abstention": fault != "receipt_loss" and record.get("eligible") is False and file_missing and not stale,
        "outcome_known": outcome_known(record),
        "unknown_fraction": record.get("unknown_fraction"),
        "file_present": not file_missing,
        "eligible": record.get("eligible"),
        "duration_s": record.get("duration_s"),
        "tool_calls": record.get("tool_calls"),
    }


def summarize(vectors: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for row in vectors:
        key = row["arm"]
        bucket = groups.setdefault(
            key,
            {"n": 0, "task_success": 0, "stale_effect": 0, "successor_completed": 0, "safety": 0, "correct_abstention": 0, "outcome_known": 0},
        )
        bucket["n"] += 1
        for field in ("task_success", "stale_effect", "successor_completed", "safety", "correct_abstention", "outcome_known"):
            bucket[field] += int(bool(row[field]))
    return groups


def token_sums(records: list[dict]) -> dict:
    incoming = 0
    outgoing = 0
    reported = 0
    counted = 0
    for record in records:
        tokens = record.get("tokens") or {}
        if not tokens:
            continue
        counted += 1
        incoming += int(tokens.get("input") or 0)
        outgoing += int(tokens.get("output") or 0)
        reported += int(tokens.get("total") or 0)
    return {
        "cells_with_tokens": counted,
        "input": incoming,
        "output": outgoing,
        "input_plus_output": incoming + outgoing,
        "reported_total": reported,
        "usable_for_cost": False,
        "reason": "原始帧没有落盘。旧 total 把流式帧里的 usage 加在了一起，不能回算 API 成本。",
    }


def load_cells(directory: Path) -> list[dict]:
    rows = []
    for path in sorted(directory.glob("*.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def build_report(records: list[dict]) -> dict:
    vectors = [state_vector(row) for row in records if row.get("scored")]
    return {
        "cells_read": len(records),
        "scored": len(vectors),
        "cells_unchanged": True,
        "task_success_is_original_terminal_correct": True,
        "by_arm": summarize(vectors),
        "stored_token_sums": token_sums(records),
        "vectors": vectors,
    }
