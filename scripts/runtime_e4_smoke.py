"""12 格功能验收，以及验收通过后的初步重复。

这一批不是确认性实验。预期写在 EXPECTATIONS 里，不按结果回改。
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

from epochgate_runtime.sandbox import docker_available
from runtime_e4_matrix import ARMS, DSH_BIN, load_env, materialize_patch, node_binary, redact, run_process_cell

ROOT = Path(__file__).resolve().parents[1]
GRID = (
    ("approval", "permission_revoked"),
    ("inventory", "state_change"),
    ("inventory", "missing_notice"),
    ("inventory", "claimed_before_replace"),
)


def wilson(successes: int, n: int, z: float = 1.96) -> list[float] | None:
    if n <= 0:
        return None
    proportion = successes / n
    denominator = 1 + z * z / n
    center = (proportion + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / n + z * z / (4 * n * n)) / denominator
    return [round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4)]


def expectation(record: dict) -> tuple[bool, str]:
    """事先固定的边界。可能发生的行为记为通过，并在原因里写明它不是必达。"""
    if "failed to import" in (record.get("stderr_tail") or ""):
        return False, "workspace plugin did not load"
    for reason in record.get("turn_reasons") or []:
        if isinstance(reason, dict) and reason.get("kind") == "error":
            error = reason.get("error") if isinstance(reason.get("error"), dict) else {}
            return False, "model request failed: " + str(error.get("code") or error.get("status") or "error")
    if record.get("status") != "completed" or not record.get("scored"):
        return False, "cell did not score"
    key = (record.get("task"), record.get("fault"), record.get("arm"))
    file_absent = record.get("file_text") is None
    if key == ("approval", "permission_revoked", "direct"):
        return True, "direct may still write after a permanent revoke"
    if key == ("approval", "permission_revoked", "commit_only"):
        return file_absent, "commit-only must withhold after a permanent revoke"
    if key == ("approval", "permission_revoked", "full"):
        return file_absent, "full must withhold after a permanent revoke"
    if key == ("inventory", "state_change", "direct"):
        return True, "direct may write the value it read before supersede"
    if key == ("inventory", "state_change", "commit_only"):
        return True, "commit-only may still write the old value; admit is not fencing"
    if key == ("inventory", "state_change", "full"):
        ok = record.get("stale_effect_accepted") is False and record.get("eligible_successor_completed") is True and record.get("terminal_correct") is True
        return ok, "full must reject the old token and let the successor finish"
    if key[0] == "inventory" and key[1] == "missing_notice":
        return record.get("stale_effect_accepted") is True, "no arm is notified, so the old value is written"
    if key == ("inventory", "claimed_before_replace", "direct"):
        return not file_absent, "direct writes without a claim"
    if key == ("inventory", "claimed_before_replace", "commit_only"):
        return not file_absent, "commit-only writes when eligibility remains"
    if key == ("inventory", "claimed_before_replace", "full"):
        recovered = any(item.get("status") == "completed" and item.get("published") is True for item in record.get("reconcile") or [])
        ok = recovered and record.get("terminal_correct") is True
        return ok, "full recovers the claimed publish before replace"
    return False, "unexpected cell"


def git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def cell_path(directory: Path, task: str, fault: str, arm: str, repeat: int) -> Path:
    return directory / "cells" / f"{task}__{fault}__{arm}__{repeat:02d}.json"


def judge_rows(rows: list[dict]) -> list[dict]:
    judged = []
    for row in rows:
        passed, reason = expectation(row)
        judged.append({
            "task": row.get("task"),
            "fault": row.get("fault"),
            "arm": row.get("arm"),
            "repeat": row.get("repeat"),
            "passed": passed,
            "reason": reason,
            "task_success": row.get("terminal_correct"),
            "stale_effect": row.get("stale_effect_accepted"),
            "successor_completed": row.get("eligible_successor_completed"),
            "file_present": row.get("file_text") is not None,
            "status": row.get("status"),
        })
    return judged


def write_summary(directory: Path, rows: list[dict], *, kind: str, rounds: int) -> dict:
    judged = judge_rows(rows)
    groups: dict[str, dict] = {}
    for item in judged:
        key = f"{item['task']}|{item['fault']}|{item['arm']}"
        bucket = groups.setdefault(key, {"n": 0, "passed": 0, "task_success": 0, "stale_effect": 0, "successor_completed": 0})
        bucket["n"] += 1
        bucket["passed"] += int(bool(item["passed"]))
        bucket["task_success"] += int(bool(item["task_success"]))
        bucket["stale_effect"] += int(bool(item["stale_effect"]))
        bucket["successor_completed"] += int(bool(item["successor_completed"]))
    for bucket in groups.values():
        n = bucket["n"]
        bucket["task_success_wilson95"] = wilson(bucket["task_success"], n)
        bucket["passed_wilson95"] = wilson(bucket["passed"], n)
    summary = {
        "kind": kind,
        "not_a_confirmatory_experiment": True,
        "rounds": rounds,
        "cells": len(rows),
        "passed": all(item["passed"] for item in judged) if judged else False,
        "git_head": git_head(),
        "docker_available": docker_available(),
        "docker_note": "未测" if not docker_available() else "available",
        "protocol": {
            "commit_only": "门后 admit 一次。admit 与写入之间没有第二次校验，也没有 fencing。",
            "direct": "不检查资格。",
            "full_claimed_before_replace": "Claim 后、替换前返回，再调用 recover_claimed_publish。",
            "commit_only_label": "受限对照，不是完整的 commit-time authorization。",
        },
        "groups": groups,
        "cells_judged": judged,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    load_env()
    import os
    key = os.environ.get("DS_API_KEY") or os.environ.get("EPOCHGATE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    args = sys.argv[1:]
    rounds = 1
    kind = "smoke"
    destination = ROOT / "results" / "runtime" / "correctness" / "e4-smoke"
    if "--rounds" in args:
        rounds = int(args[args.index("--rounds") + 1])
    if "--out" in args:
        destination = Path(args[args.index("--out") + 1])
    if "--kind" in args:
        kind = args[args.index("--kind") + 1]
    if "--require-smoke" in args:
        smoke = json.loads(Path(args[args.index("--require-smoke") + 1]).read_text(encoding="utf-8"))
        if not smoke.get("passed"):
            raise SystemExit("smoke has not passed; repeat was not started")
    cells = destination / "cells"
    cells.mkdir(parents=True, exist_ok=True)
    node = node_binary()
    if not key or node is None or not DSH_BIN.is_file():
        if not key:
            blocked = "API key missing"
        elif node is None:
            blocked = "node is not installed"
        else:
            blocked = f"dsh binary missing: {DSH_BIN}"
        summary = {
            "kind": kind,
            "passed": False,
            "blocked": blocked,
            "not_a_confirmatory_experiment": True,
        }
        (destination / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise SystemExit(summary["blocked"])
    patch = materialize_patch()
    rows = []
    for repeat in range(rounds):
        for task, fault in GRID:
            for arm in ARMS:
                path = cell_path(destination, task, fault, arm, repeat)
                if path.is_file():
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    if existing.get("scored"):
                        rows.append(existing)
                        continue
                print(f"run {task} {fault} {arm} {repeat}", flush=True)
                record = run_process_cell(task, fault, arm, repeat, key, node, patch)
                path.write_text(redact(json.dumps(record, ensure_ascii=False, indent=2) + "\n", key), encoding="utf-8")
                rows.append(record)
                print(json.dumps({
                    "id": path.stem,
                    "status": record.get("status"),
                    "terminal_correct": record.get("terminal_correct"),
                    "stale": record.get("stale_effect_accepted"),
                    "successor": record.get("eligible_successor_completed"),
                    "errors": record.get("errors"),
                }, ensure_ascii=False), flush=True)
    if not rows:
        for path in sorted(cells.glob("*.json")):
            rows.append(json.loads(path.read_text(encoding="utf-8")))
    summary = write_summary(destination, rows, kind=kind, rounds=rounds)
    print(json.dumps({"kind": kind, "passed": summary["passed"], "cells": summary["cells"]}, ensure_ascii=False), flush=True)
    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
