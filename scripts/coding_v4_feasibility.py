"""v4 开发集的模型前检查。不调用模型，不改 v4 协议。"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from coding_v3 import POOL, export_base, git, unified_diff
from coding_v3_rescore import EXPECTED_IMAGE, docker_bin, score
from reliability_contract import sha256_text

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "results" / "runtime" / "correctness" / "v4-coding" / "frozen-tasks.json"
OUT = ROOT / "results" / "runtime" / "correctness" / "v4-coding" / "feasibility"
DEV_IDS = (
    "code-more-itertools-172bcb3c",
    "code-more-itertools-20c4ee5c",
    "code-more-itertools-4aeb4338",
)


def tasks() -> list[dict[str, Any]]:
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    chosen = {row["task_id"]: row for row in frozen["dev"]}
    rows = []
    for task_id in DEV_IDS:
        row = dict(chosen[task_id])
        repo = POOL / row["repo"]
        test_patch = git(repo, "diff", row["base"], row["fix"], "--", *row["tests"]).stdout
        source_patch = git(repo, "diff", row["base"], row["fix"], "--", *row["sources"]).stdout
        row.update({
            "family": "coding",
            "form": "coding",
            "test_patch": test_patch,
            "source_patch": source_patch,
            "relative": "patch.diff",
            "constraints": ["workspace_version", "hidden_tests"],
            "event": {"set": {"version": 2}},
            "initial": {"version": 1},
            "successor_applicable": True,
            "s2_mode": "delivered",
            "phase_pool": "v4-dev",
        })
        rows.append(row)
    return rows


def comment_text(original: str) -> str:
    text = original if original.endswith("\n") or original == "" else original + "\n"
    return text + "# epochgate-concurrent workspace version 2\n"


def first_hunk(diff: str) -> tuple[str, int] | None:
    path = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = line.split()[-1][2:]
        elif line.startswith("@@ ") and path:
            old = line.split()[1]
            return path, int(old.split(",")[0][1:])
    return None


def raise_text(original: str, line_number: int) -> str:
    lines = original.splitlines(keepends=True)
    if line_number < 1 or line_number > len(lines):
        raise RuntimeError(f"hunk line {line_number} outside file")
    raw = lines[line_number - 1]
    indent = raw[: len(raw) - len(raw.lstrip(" \t"))]
    newline = "\n" if raw.endswith("\n") else ""
    lines[line_number - 1] = f'{indent}raise RuntimeError("epochgate-semantic-conflict"){newline}'
    return "".join(lines)


def materialize(base: Path, task: dict[str, Any], dest: Path, *, comment: bool, use_fix: bool) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(base, dest)
    for relative in task["sources"]:
        text = show_file(task, relative) if use_fix else (base / relative).read_text(encoding="utf-8")
        if comment and relative == task["sources"][0]:
            text = comment_text(text)
        (dest / relative).write_text(text, encoding="utf-8")


def show_file(task: dict[str, Any], relative: str) -> str:
    repo = POOL / task["repo"]
    return git(repo, "show", f"{task['fix']}:{relative}").stdout


def successor_diff(origin: Path, target: Path) -> str:
    return unified_diff(origin, target)


def parse_ok(text: str) -> bool:
    try:
        ast.parse(text)
    except SyntaxError:
        return False
    return True


def import_ok(root: Path, module: str) -> dict[str, Any]:
    binary = docker_bin()
    script = f"import ast, pathlib; ast.parse(pathlib.Path('{module}.py').read_text()) if False else None; import {module}"
    # Import the package name, not a file path. more_itertools lives at the repo root.
    script = f"import {module}"
    completed = subprocess.run(
        [binary, "run", "--rm", "-v", f"{root}:/src", "-w", "/src", "-e", "PYTHONPATH=src:.", EXPECTED_IMAGE, "python", "-c", script],
        text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60, check=False,
    )
    return {"ok": completed.returncode == 0, "tail": (completed.stdout + completed.stderr)[-300:]}


def main() -> None:
    binary = docker_bin()
    inspected = subprocess.run([binary, "image", "inspect", EXPECTED_IMAGE, "--format", "{{.Id}}"], text=True, capture_output=True, check=False)
    image_id = inspected.stdout.strip()
    if image_id != EXPECTED_IMAGE:
        raise SystemExit(f"image id {image_id} does not match {EXPECTED_IMAGE}")
    OUT.mkdir(parents=True, exist_ok=True)
    cache: dict[tuple, dict[str, Any]] = {}
    rows = []
    for task in tasks():
        print(task["task_id"], flush=True)
        base = export_base(task)
        source = task["sources"][0]
        original = (base / source).read_text(encoding="utf-8")
        gold = score(task, task["source_patch"], None, cache)
        bare = score(task, None, None, cache)
        comment_dir = POOL / "_score" / "v4-feas" / task["task_id"] / "comment"
        materialize(base, task, comment_dir, comment=True, use_fix=False)
        target = POOL / "_score" / "v4-feas" / task["task_id"] / "comment-fixed"
        materialize(base, task, target, comment=True, use_fix=True)
        comment_delta = unified_diff(base, comment_dir)
        comment_successor = successor_diff(comment_dir, target)
        comment_score = score(task, comment_successor, comment_delta, cache) if comment_successor.strip() else {"ok": False, "passed": False, "error": "empty_successor"}
        hunk = first_hunk(task["source_patch"])
        semantic: dict[str, Any] = {"hunk": None}
        if hunk is None:
            semantic["invalid"] = True
            semantic["reason"] = "no_hunk"
        else:
            path, line_number = hunk
            text = (base / path).read_text(encoding="utf-8")
            raised = raise_text(text, line_number)
            semantic["hunk"] = {"path": path, "line": line_number}
            semantic["parse_ok"] = parse_ok(raised)
            raised_dir = POOL / "_score" / "v4-feas" / task["task_id"] / "raise"
            materialize(base, task, raised_dir, comment=False, use_fix=False)
            (raised_dir / path).write_text(raised, encoding="utf-8")
            imported = import_ok(raised_dir, "more_itertools")
            semantic["import_ok"] = imported["ok"]
            semantic["import_tail"] = imported["tail"]
            fixed_dir = POOL / "_score" / "v4-feas" / task["task_id"] / "raise-fixed"
            materialize(base, task, fixed_dir, comment=False, use_fix=True)
            raised_delta = unified_diff(base, raised_dir)
            follow = successor_diff(raised_dir, fixed_dir)
            semantic["successor_sha256"] = sha256_text(follow) if follow.strip() else None
            semantic["concurrent_sha256"] = sha256_text(raised_delta)
            semantic["successor"] = score(task, follow, raised_delta, cache) if follow.strip() else {"ok": False, "passed": False, "error": "empty_successor"}
            semantic["old_patch_on_mutated"] = score(task, task["source_patch"], raised_delta, cache)
            semantic["invalid"] = not (semantic["parse_ok"] and semantic["import_ok"] and semantic["successor"].get("passed"))
            semantic["reason"] = None if not semantic["invalid"] else "unparseable_or_unimportable_or_no_legal_successor"
        card_blob = task["test_patch"][:40]
        row = {
            "task_id": task["task_id"],
            "base_hidden_passed": bare.get("passed"),
            "gold_hidden_passed": gold.get("passed"),
            "image_id": image_id,
            "toolchain": {"search": True, "read": True, "edit": True, "public_test_path_exists": (base / task["tests"][0]).is_file()},
            "data_isolation": card_blob not in (task.get("problem") or ""),
            "version_comment": {
                "import_relevant": True,
                "successor_hidden_passed": comment_score.get("passed"),
                "successor_sha256": sha256_text(comment_successor) if comment_successor.strip() else None,
                "tail": comment_score.get("tail"),
                "apply_error": comment_score.get("apply_error"),
                "legal_successor": bool(comment_score.get("passed")),
            },
            "semantic_raise": semantic,
        }
        row["ready_for_model"] = bool(row["base_hidden_passed"] is False and row["gold_hidden_passed"] is True and row["version_comment"]["legal_successor"])
        rows.append(row)
        (OUT / f"{task['task_id']}.json").write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"task": task["task_id"], "base": row["base_hidden_passed"], "gold": row["gold_hidden_passed"], "v1": row["version_comment"]["legal_successor"], "raise_invalid": semantic.get("invalid")}, ensure_ascii=False), flush=True)
    summary = {"image_id": image_id, "model_called": False, "rows": rows}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
