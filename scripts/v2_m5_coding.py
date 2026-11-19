"""Coding 任务在 LangGraph 上的冒烟。不改框架内核。池子不足就写 PORTABILITY_LIMITED。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "results" / "runtime" / "correctness" / "v2-coding-pool" / "manifest.json"
OUT = ROOT / "results" / "runtime" / "correctness" / "v2-m5-coding"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {"status": "TASK_POOL_INSUFFICIENT"}
    kernel = False
    if manifest.get("status") != "FROZEN" or not manifest.get("tasks"):
        summary = {
            "status": "PORTABILITY_LIMITED",
            "reason": manifest.get("status", "TASK_POOL_INSUFFICIENT"),
            "kernel_modified": kernel,
            "rows": [],
            "note": "没有冻结的可评分 Coding 任务，不能把履约家族的 LangGraph 结果改写成编码任务的跨 Runtime 验证。",
        }
        (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": summary["status"], "reason": summary["reason"]}, ensure_ascii=False))
        return
    from m5_langgraph import run_mode
    from v2_coding_matrix import _git, run_hidden_tests
    task = manifest["tasks"][0]
    repo_patch = _git(Path(r"C:\Project\epochgate-pool") / task["repo"], "diff", task["base"], task["fix"], "--", *task["sources"])
    rows = []
    for fault in ("notice_missing", "notice_delivered", "response_lost", "interrupt_resume"):
        for mode in ("R0", "R1"):
            cell = OUT / f"{task['task_id']}__{mode}__{fault}"
            try:
                row = run_mode({"task_id": task["task_id"], "family": "coding", "relative": "patch.diff", "event": task["event"]}, fault, mode, repo_patch.encode("utf-8"), cell)
                row["blocked"] = False
            except Exception as exc:
                row = {"task_id": task["task_id"], "arm": mode, "fault": fault, "blocked": True, "error_type": type(exc).__name__, "claim": "NOT_SUPPORTED"}
            row["payload_source"] = "historical_fix_schedule"
            row["model_effect"] = False
            row["kernel_modified"] = False
            rows.append(row)
    scored = run_hidden_tests(task, repo_patch.encode("utf-8"))
    summary = {
        "status": "PORTABILITY_LIMITED",
        "reason": "冒烟使用历史修复补丁驱动 LangGraph 公开 API。没有模型工具轨迹，因此不能记成完整的跨 Runtime 编码验证。容器评分单独附在 scorer_check。",
        "kernel_modified": False,
        "task_id": task["task_id"],
        "base": task["base"],
        "fix": task["fix"],
        "scorer_check": scored,
        "rows": rows,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": summary["status"], "n": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
