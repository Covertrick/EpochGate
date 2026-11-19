"""v4c：确认集不再接收隐藏测试失败行。不改 v4 的轨迹和通过数。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from coding_v3 import POOL, git
from coding_v3_rescore import score
from coding_v4 import mechanism, run_agent, save, v1_pair
from reliability_contract import sha256_text

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "correctness" / "v4c-isolated"
FROZEN = ROOT / "results" / "runtime" / "correctness" / "v4-coding" / "frozen-tasks.json"
WITHHELD = "本轮不提供隐藏测试的失败行、断言期望或异常文本。请只根据问题描述阅读源码。工作区里已有的测试可以用 run_public_tests。隐藏测试只在评分端运行。"


def confirm_tasks() -> list[dict[str, Any]]:
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    rows = []
    for row in frozen["confirm"]:
        item = dict(row)
        repo = POOL / item["repo"]
        item.update({
            "family": "coding",
            "form": "coding",
            "test_patch": git(repo, "diff", item["base"], item["fix"], "--", *item["tests"]).stdout,
            "source_patch": git(repo, "diff", item["base"], item["fix"], "--", *item["sources"]).stdout,
            "relative": "patch.diff",
            "constraints": ["workspace_version", "hidden_tests"],
            "event": {"set": {"version": 2}},
            "initial": {"version": 1},
            "successor_applicable": True,
            "s2_mode": "delivered",
            "phase_pool": "v4c-isolated",
        })
        rows.append(item)
    return rows


def isolated_card(task: dict[str, Any]) -> dict[str, Any]:
    message = git(POOL / task["repo"], "log", "-1", "--format=%B", task["fix"]).stdout.strip()
    kept = [line for line in message.splitlines() if not line.startswith("diff --git")]
    parts = [part.strip() for part in "\n".join(kept).split("\n\n") if part.strip()]
    problem = parts[0] if parts else ""
    expected = "\n\n".join(parts[1:])[:1200] if len(parts) > 1 else ""
    withheld = False
    if expected and any(len(piece) >= 12 and piece in task["test_patch"] for piece in expected.split()):
        expected = ""
        withheld = True
    card = {
        "task_id": task["task_id"],
        "problem": problem,
        "expected": expected,
        "symptom": WITHHELD,
        "symptom_status": "withheld_hidden_failure_lines",
        "expected_withheld": withheld,
        "protocol": "Coding-v4c",
    }
    blob = json.dumps(card, ensure_ascii=False)
    if "diff --git" in blob or task["test_patch"][:80] in blob:
        raise SystemExit("isolated prompt leaked hidden material")
    if "AssertionError" in card["symptom"] or "E   " in card["symptom"]:
        raise SystemExit("isolated prompt still contains a failure line")
    return card


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cache: dict[tuple, dict[str, Any]] = {}
    rows = []
    for task in confirm_tasks():
        card = isolated_card(task)
        print("isolated-initial", task["task_id"], flush=True)
        initial = run_agent(task, card, suffix="", prepare=None, require_reread=False)
        hidden = score(task, initial.get("diff") if initial.get("apply_check", {}).get("ok") else None, None, cache)
        initial["container"] = {key: hidden.get(key) for key in ("ok", "passed", "image_id", "returncode", "error")}
        initial["protocol"] = "Coding-v4c"
        if initial["label"] == "PATCH_FORMED":
            initial["label"] = "HIDDEN_PASSED" if hidden.get("passed") else "HIDDEN_FAILED" if hidden.get("ok") else "ENVIRONMENT_BLOCKED"
        save(OUT / task["task_id"] / "initial", initial)
        comment_delta, comment_successor = v1_pair(task)
        if sha256_text(comment_successor) and not comment_successor.strip():
            raise SystemExit("empty successor")
        old_bytes = (initial.get("diff") or task["source_patch"]).encode("utf-8")
        if not old_bytes.strip():
            old_bytes = task["source_patch"].encode("utf-8")
        mech = mechanism(task, comment_successor.encode("utf-8"), old_bytes, name="v1", phase="cells", root=OUT)
        print("isolated-successor", task["task_id"], flush=True)

        def prepare(workspace, source: str = task["sources"][0]) -> None:
            file = workspace.root / source
            text = file.read_text(encoding="utf-8")
            if not text.endswith("\n"):
                text += "\n"
            file.write_text(text + "# epochgate-concurrent workspace version 2\n", encoding="utf-8")

        agent = run_agent(
            task, card,
            suffix="\n\n上一次提交没有被接纳。源文件已经有并发修改。请重新 read_file 读取该文件，再提交一份新的 unified diff。",
            prepare=prepare, require_reread=True, reread_path=task["sources"][0],
        )
        if agent["label"] == "DID_NOT_REREAD":
            agent_hidden = {"ok": True, "passed": False, "error": "DID_NOT_REREAD"}
        else:
            patch = agent.get("diff") if agent.get("apply_check", {}).get("ok") else None
            agent_hidden = score(task, patch, comment_delta, cache)
        agent["container"] = {key: agent_hidden.get(key) for key in ("ok", "passed", "image_id", "returncode", "error")}
        agent["protocol"] = "Coding-v4c"
        if agent["label"] == "PATCH_FORMED":
            agent["label"] = "HIDDEN_PASSED" if agent_hidden.get("passed") else "HIDDEN_FAILED" if agent_hidden.get("ok") else "ENVIRONMENT_BLOCKED"
        save(OUT / task["task_id"] / "agent-successor", agent)
        rows.append({
            "task_id": task["task_id"],
            "expected_withheld": card["expected_withheld"],
            "initial_label": initial["label"],
            "initial_hidden_passed": initial.get("container", {}).get("passed"),
            "agent_successor_label": agent["label"],
            "agent_successor_passed": agent_hidden.get("passed"),
            "mechanism_v1": mech,
        })
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    passed = sum(1 for row in rows if row["initial_hidden_passed"] is True)
    successor = sum(1 for row in rows if row["agent_successor_passed"] is True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False).stdout.strip()
    summary = {
        "protocol": "Coding-v4c",
        "git_sha": sha,
        "denominator": len(rows),
        "initial_hidden_passed": passed,
        "agent_successor_passed": successor,
        "not_added_to_v4": True,
        "rows": rows,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
