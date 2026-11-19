"""v4b 开发集故障。不改 v4 的 raise 行，不使用确认任务，不调用模型。"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path
from typing import Any

from coding_v3 import POOL, export_base, unified_diff
from coding_v3_rescore import score
from coding_v4_feasibility import first_hunk, import_ok, materialize, parse_ok, tasks
from m4_matrix import run_cell
from reliability_contract import sha256_text

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "correctness" / "v4b-coding"


def insert_line(text: str, line_number: int, inserted: str) -> str:
    lines = text.splitlines(keepends=True)
    if line_number < 1 or line_number > len(lines) + 1:
        raise RuntimeError(f"line {line_number} outside file")
    lines.insert(line_number - 1, inserted)
    return "".join(lines)


def indented_comment(text: str, line_number: int) -> str:
    lines = text.splitlines(keepends=True)
    raw = lines[line_number - 1]
    indent = raw[: len(raw) - len(raw.lstrip(" \t"))]
    return insert_line(text, line_number + 1, f"{indent}# epochgate-related-comment\n")


def function_raise(text: str, line_number: int) -> str | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    host = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= line_number <= end and (host is None or node.lineno >= host.lineno):
                host = node
    if host is None:
        return None
    body_line = host.body[0].lineno
    raw = text.splitlines(keepends=True)[body_line - 1]
    indent = raw[: len(raw) - len(raw.lstrip(" \t"))]
    return insert_line(text, body_line, f'{indent}raise RuntimeError("epochgate-semantic-conflict")\n')


def mutate(task: dict[str, Any], kind: str) -> dict[str, Any]:
    base = export_base(task)
    hunk = first_hunk(task["source_patch"])
    if hunk is None:
        return {"invalid": True, "reason": "no_hunk"}
    path, line_number = hunk
    original = (base / path).read_text(encoding="utf-8")
    if kind == "V2":
        changed = indented_comment(original, line_number)
    else:
        changed = function_raise(original, line_number)
        if changed is None:
            return {"invalid": True, "reason": "hunk_not_inside_function", "hunk": {"path": path, "line": line_number}}
    if not parse_ok(changed):
        return {"invalid": True, "reason": "unparseable", "hunk": {"path": path, "line": line_number}}
    root = POOL / "_score" / "v4b" / task["task_id"] / kind
    if root.exists():
        shutil.rmtree(root)
    materialize(base, task, root, comment=False, use_fix=False)
    (root / path).write_text(changed, encoding="utf-8")
    imported = import_ok(root, "more_itertools")
    if not imported["ok"]:
        return {"invalid": True, "reason": "unimportable", "hunk": {"path": path, "line": line_number}, "import_tail": imported["tail"]}
    fixed = POOL / "_score" / "v4b" / task["task_id"] / f"{kind}-fixed"
    materialize(base, task, fixed, comment=False, use_fix=True)
    if kind == "V2":
        try:
            current = (fixed / path).read_text(encoding="utf-8")
            (fixed / path).write_text(indented_comment(current, line_number), encoding="utf-8")
        except (RuntimeError, OSError):
            return {"invalid": True, "reason": "comment_not_kept_on_fix"}
    concurrent = unified_diff(base, root)
    successor = unified_diff(root, fixed)
    return {
        "invalid": False,
        "hunk": {"path": path, "line": line_number},
        "concurrent": concurrent,
        "successor": successor,
        "successor_sha256": sha256_text(successor) if successor.strip() else None,
    }


def show_fix(task: dict[str, Any], relative: str) -> str:
    from coding_v3 import git
    return git(POOL / task["repo"], "show", f"{task['fix']}:{relative}").stdout


def admit(task: dict[str, Any], payload: bytes, legal: bytes, arm: str, fault_name: str) -> dict[str, Any]:
    copied = dict(task)
    copied["legal_payload"] = lambda world, text=legal.decode("utf-8"): text
    cell = OUT / task["task_id"] / fault_name / arm
    if cell.exists():
        shutil.rmtree(cell)
    cell.mkdir(parents=True, exist_ok=True)
    evidence = run_cell(copied, arm, "S1", cell, old_payload=payload, next_payload=legal, model="v4b-mechanism", end_reason=fault_name)
    return {
        "stale_credential_accepted": bool(evidence.get("stale_credential_accepted")),
        "successor_effect_accepted": bool(evidence.get("successor_completed")),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cache: dict[tuple, dict[str, Any]] = {}
    rows = []
    for task in tasks():
        for kind in ("V2", "V3"):
            print(task["task_id"], kind, flush=True)
            built = mutate(task, kind)
            if built.get("invalid"):
                rows.append({"task_id": task["task_id"], "kind": kind, "invalid": True, "reason": built.get("reason")})
                continue
            old = score(task, task["source_patch"], built["concurrent"], cache)
            follow = score(task, built["successor"], built["concurrent"], cache) if built["successor"].strip() else {"passed": False, "ok": False, "tail": "empty_successor"}
            rejected = old.get("tail") in {"patch_rejected", "concurrent_rejected"} or "apply_error" in old
            content_invalid = (not rejected) and old.get("ok") is True and old.get("passed") is False
            content_valid = old.get("passed") is True
            arms = {}
            if content_invalid or content_valid:
                legal = built["successor"].encode("utf-8") if follow.get("passed") else task["source_patch"].encode("utf-8")
                for arm in ("B1", "B2", "B3"):
                    arms[arm] = admit(task, task["source_patch"].encode("utf-8"), legal, arm, kind)
            rows.append({
                "task_id": task["task_id"],
                "kind": kind,
                "invalid": False,
                "patch_rejected": rejected,
                "old_tail": (old.get("tail") or "")[-240:],
                "content_invalid": content_invalid,
                "content_still_valid": content_valid,
                "legal_successor_passed": bool(follow.get("passed")),
                "successor_tail": (follow.get("tail") or "")[-240:],
                "old_hidden_passed": old.get("passed"),
                "arms": arms,
                "unsafe_admission": {arm: bool(content_invalid and row["stale_credential_accepted"]) for arm, row in arms.items()},
                "unnecessary_rejection": {arm: bool(content_valid and not row["stale_credential_accepted"]) for arm, row in arms.items()},
                "successor_accepted": {arm: row["successor_effect_accepted"] for arm, row in arms.items()},
            })
            print(json.dumps({"task": task["task_id"], "kind": kind, "rejected": rejected, "content_invalid": content_invalid, "content_valid": content_valid, "successor": bool(follow.get("passed"))}, ensure_ascii=False), flush=True)
    (OUT / "summary.json").write_text(json.dumps({"model_called": False, "confirm_tasks_used": False, "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
