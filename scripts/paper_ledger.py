"""从原始格子重算总账。Markdown 里的数字若对不上，写进 conflicts，不改原始文件。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "论文整理"
CELLS = ROOT / "results" / "runtime" / "correctness"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cells(directory: Path) -> list[dict]:
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        rows.append(load(path))
    return rows


def e4(directory: Path) -> dict:
    rows = cells(directory)
    by_arm = {}
    for row in rows:
        arm = row.get("arm") or "missing"
        bucket = by_arm.setdefault(arm, Counter())
        bucket["n"] += 1
        bucket["terminal_correct"] += int(bool(row.get("terminal_correct")))
        bucket["stale_effect_accepted"] += int(bool(row.get("stale_effect_accepted")))
        bucket["eligible_successor_completed"] += int(bool(row.get("eligible_successor_completed")))
        bucket["scored"] += int(bool(row.get("scored")))
    return {"n": len(rows), "by_arm": {arm: dict(counts) for arm, counts in sorted(by_arm.items())}}


def count_status(directory: Path, field: str = "label") -> dict:
    rows = []
    for path in directory.rglob("summary.json"):
        rows.append(load(path))
    return {"summaries": len(rows)}


def main() -> None:
    v3 = load(CELLS / "v3-coding" / "rescore" / "summary.json")
    v4_gate = load(CELLS / "v4-coding" / "dev" / "gate.json")
    v4_confirm = load(CELLS / "v4-coding" / "confirm" / "summary.json")
    v4_audit = load(CELLS / "v4-coding" / "prompt-audit.json")["summary"]
    v4c = load(CELLS / "v4c-isolated" / "summary.json")
    v4b = load(CELLS / "v4b-coding" / "summary.json")
    historical = load(CELLS / "v4b-historical" / "summary.json")
    m5 = load(CELLS / "m5" / "summary.json")
    m6 = load(CELLS / "v2-m6" / "summary.json")
    outbox = load(CELLS / "outbox-baseline" / "summary.json")
    regression = load(CELLS / "final-regression" / "summary.json")
    repeat = load(CELLS / "e4-repeat" / "summary.json")
    old = e4(CELLS / "after" / "e4" / "cells")
    new = e4(CELLS / "e4-repeat" / "cells")
    m4_confirm = list((CELLS / "m4-confirm").glob("*"))
    ledger = {
        "e4_old_360": old,
        "e4_repeat_directory": new,
        "e4_repeat_summary_cells": repeat.get("cells"),
        "e4_repeat_summary_matches_directory": repeat.get("cells") == new["n"],
        "v3_rescore": {
            "assigned_confirm": v3["assigned_confirm"],
            "patches_scored": v3["model_patches_scored"],
            "hidden_passed": v3["passed_count"],
            "budget_exceeded": v3["budget_exceeded"],
            "git_note": "runner was not in b6dd92b; identity is the artifact sha256",
        },
        "v4_dev": {
            "assigned": len(v4_gate["rows"]),
            "scorable": v4_gate["scorable_patches"],
            "successor_labels": v4_gate["agent_successors"],
            "confirm_started": v4_gate["confirm_started"],
        },
        "v4_confirm": {
            "denominator": v4_confirm["denominator"],
            "initial_passed": sum(1 for row in v4_confirm["rows"] if row.get("initial_hidden_passed") is True),
            "successor_passed": sum(1 for row in v4_confirm["rows"] if row.get("agent_successor_passed") is True),
            "feedback": "FIXED_FAILURE_FEEDBACK",
            "tasks_with_failure_lines": v4_audit["symptom_extracted"],
            "tasks_with_assertion": v4_audit["saw_assertion_expectation"],
        },
        "v4c": {
            "denominator": v4c["denominator"],
            "initial_hidden_passed": v4c["initial_hidden_passed"],
            "agent_successor_passed": v4c["agent_successor_passed"],
            "git_sha": v4c["git_sha"],
            "not_added_to_v4": v4c["not_added_to_v4"],
        },
        "v4b": {
            "rows": len(v4b["rows"]),
            "invalid": sum(1 for row in v4b["rows"] if row.get("invalid")),
            "patch_rejected": sum(1 for row in v4b["rows"] if row.get("patch_rejected")),
            "content_invalid": sum(1 for row in v4b["rows"] if row.get("content_invalid")),
        },
        "v4b_historical": historical["rows"],
        "m5": {"n": m5["n"], "blocked": m5["blocked"], "coding_blocked": m5["coding"]["blocked"]},
        "m6": {
            "decision": m6["historical"]["historical_decision_unchanged"],
            "pairs": m6["historical"]["h5_mcnemar"],
            "precision": m6["precision_status"],
            "degenerate_bootstrap": m6["historical"]["h5_exact"]["degenerate_bootstrap"],
        },
        "outbox": outbox,
        "regression": {"result": regression["result"], "power_loss": regression["power_loss"], "h2": regression["h2_container_crash_hooks"]},
        "m4_confirm_dirs": len(m4_confirm),
    }
    (OUT / "experiment-ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"e4_old": old["n"], "e4_new": new["n"], "v4c": ledger["v4c"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
