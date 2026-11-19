"""记录这轮材料的来源。不读取 .env，不把密钥写进清单。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "paper" / "artifact-manifest.json"


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(args: list[str]) -> str:
    completed = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False)
    return (completed.stdout or "").strip()


def main() -> None:
    dirty = bool(git(["status", "--porcelain"]))
    manifest = {
        "git_sha": git(["rev-parse", "HEAD"]),
        "worktree_dirty": dirty,
        "reproducible_commit": False,
        "note": "工作区有未提交文件时，不把 HEAD 说成这轮的复现点。",
        "model": "deepseek-flash",
        "temperature": 0,
        "max_tokens": 8000,
        "budget": {"turns": 24, "tools": 40, "seconds": 420},
        "image": "python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d",
        "entries": {
            "verify-offline": "scripts/verify_offline.py",
            "verify-container": "scripts/verify_container.py",
            "run-model-evaluation": "scripts/run_model_evaluation.py",
        },
        "batches": [
            {"id": "v3-rescore", "dir": "results/runtime/correctness/v3-coding/rescore", "authoritative_for": "hidden tests", "add_to_other_batches": False},
            {"id": "v3-traces", "dir": "results/runtime/correctness/v3-coding", "authoritative_for": "original traces, including docker_unavailable", "add_to_other_batches": False},
            {"id": "v4-dev", "dir": "results/runtime/correctness/v4-coding/dev", "authoritative_for": "v4 development gate", "add_to_other_batches": False},
            {"id": "v4b", "dir": "results/runtime/correctness/v4b-coding", "authoritative_for": "V2 and V3 on the three development tasks", "add_to_other_batches": False},
            {"id": "e4-after", "dir": "results/runtime/correctness/after/e4", "authoritative_for": "old model cells", "add_to_other_batches": False},
            {"id": "e4-repeat", "dir": "results/runtime/correctness/e4-repeat", "authoritative_for": "new 120 cells", "add_to_other_batches": False},
        ],
        "hashes": {
            "coding_v4": sha256(ROOT / "scripts" / "coding_v4.py"),
            "coding_v4b": sha256(ROOT / "scripts" / "coding_v4b.py"),
            "v4_protocol": sha256(ROOT / "docs" / "开发文档" / "Coding-v4协议.md"),
            "frozen_tasks": sha256(ROOT / "results" / "runtime" / "correctness" / "v4-coding" / "frozen-tasks.json"),
        },
        "cannot_rebuild": [
            "v3 runner was not in commit b6dd92b until those files are committed",
            "no complete pip lock for the v3 model run",
            "batches use different versions and fault mixes",
            "some model_no_effect cells have no tool trace",
            "historical token totals cannot be reconstructed from later logs",
            "some model batches did not run in a container",
            "M5 receiver behavior belongs to the external receiver, not to LangGraph",
        ],
    }
    OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(OUT.relative_to(ROOT)), "dirty": dirty}, ensure_ascii=False))


if __name__ == "__main__":
    main()
