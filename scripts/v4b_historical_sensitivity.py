"""用一条真实的后续提交检查内容是否仍有效。不调用模型，不改 v4b 的注入结果。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from coding_v3 import POOL, git
from coding_v3_rescore import score
from coding_v4_feasibility import tasks

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "correctness" / "v4b-historical"


def later_commit(task: dict[str, Any]) -> tuple[str, str] | None:
    repo = POOL / task["repo"]
    from coding_v3 import export_base
    import subprocess
    base = export_base(task)
    log = git(repo, "log", "-n", "200", "--format=%H", "HEAD", "--", *task["sources"]).stdout.split()
    for sha in log:
        if sha in {task["fix"], task["base"]}:
            continue
        parent = git(repo, "rev-parse", f"{sha}^").stdout.strip()
        diff = git(repo, "diff", parent, sha, "--", *task["sources"]).stdout
        if not diff.strip():
            continue
        checked = subprocess.run(["git", "apply", "--check", "--unsafe-paths", "-"], cwd=base, input=diff, text=True, capture_output=True, check=False)
        if checked.returncode == 0:
            return sha, diff
    return None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cache: dict[tuple, dict[str, Any]] = {}
    rows = []
    found = None
    for task in tasks():
        picked = later_commit(task)
        if picked is None:
            rows.append({"task_id": task["task_id"], "found": False})
            continue
        sha, diff = picked
        scored = score(task, task["source_patch"], diff, cache)
        row = {
            "task_id": task["task_id"],
            "found": True,
            "commit": sha,
            "patch_rejected": scored.get("tail") in {"patch_rejected", "concurrent_rejected"} or "apply_error" in scored,
            "old_patch_hidden_passed": scored.get("passed"),
            "tail": (scored.get("tail") or "")[-180:],
        }
        rows.append(row)
        if found is None and row["found"]:
            found = row
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if row["found"] and not row["patch_rejected"]:
            break
    summary = {
        "model_called": False,
        "injected_raise_unchanged": True,
        "note": "B2 and B3 reject a stale token before this historical diff is consulted. Their v4b safety column is the version check. This file only checks whether one real later edit leaves the old patch valid.",
        "rows": rows,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
