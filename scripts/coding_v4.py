"""Coding v4 开发集。不改 v3 运行器，不启动确认集。"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from coding_v3 import (
    HARNESS,
    MAX_SECONDS,
    MAX_TOOLS,
    MAX_TURNS,
    POOL,
    TOOLS,
    Workspace,
    _post,
    apply_check,
    dispatch,
    export_base,
    label_trace,
    problem_card,
    pure,
    unified_diff,
    user_prompt,
)
from coding_v3_rescore import score
from coding_v4_feasibility import first_hunk, materialize, raise_text, tasks as dev_tasks
from m4_matrix import run_cell
from reliability_contract import sha256_text

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "correctness" / "v4-coding"
PROTOCOL = ROOT / "docs" / "开发文档" / "Coding-v4协议.md"
TOOL_NAMES = ("list_sources", "search_code", "read_file", "edit_file", "run_public_tests", "submit_patch")


def timed_dispatch(workspace: Workspace, name: str, arguments: dict[str, Any], clock: dict[str, float], *, reread: set[str], require: bool) -> str:
    path = str(arguments.get("path") or "")
    if require and name == "edit_file" and path and path not in reread:
        payload = {"error": "must_reread", "path": path}
        workspace.events.append({"tool": name, "path": path, "ok": False})
        return pure(payload)
    started = time.perf_counter()
    content = dispatch(workspace, name, arguments)
    clock[name] = clock.get(name, 0.0) + (time.perf_counter() - started)
    if name == "read_file" and path:
        reread.add(path)
    return content


def run_agent(task: dict[str, Any], card: dict[str, Any], *, suffix: str, prepare: Any, require_reread: bool, reread_path: str | None = None) -> dict[str, Any]:
    workspace = Workspace(task)
    if prepare is not None:
        prepare(workspace)
    origin = workspace.snapshot() if prepare is not None else workspace.base
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": HARNESS},
        {"role": "user", "content": user_prompt(card) + suffix},
    ]
    reasons: list[str] = []
    texts: list[str] = []
    tokens = [0, 0]
    started = time.perf_counter()
    limits = {"turns": False, "tools": False, "seconds": False}
    clock = {name: 0.0 for name in TOOL_NAMES}
    reread: set[str] = set()
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
    error = None
    used_turns = 0
    for _ in range(MAX_TURNS):
        if time.perf_counter() - started > MAX_SECONDS:
            limits["seconds"] = True
            break
        if len(workspace.events) >= MAX_TOOLS:
            limits["tools"] = True
            break
        used_turns += 1
        result = _post({"model": model, "messages": messages, "tools": TOOLS, "temperature": 0, "max_tokens": 8000})
        if not result.get("ok"):
            error = result.get("error")
            break
        payload = result["payload"]
        usage = payload.get("usage") or {}
        tokens[0] += int(usage.get("prompt_tokens") or 0)
        tokens[1] += int(usage.get("completion_tokens") or 0)
        message = payload["choices"][0]["message"]
        reasons.append(payload["choices"][0].get("finish_reason") or "")
        if message.get("content"):
            texts.append(str(message.get("content"))[:800])
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            if reasons[-1] == "length":
                messages.append({"role": "user", "content": "上一条输出在长度上限处被截断，没有形成工具调用。请继续同一步，并使用工具。"})
                continue
            break
        for call in calls:
            if len(workspace.events) >= MAX_TOOLS:
                limits["tools"] = True
                break
            if time.perf_counter() - started > MAX_SECONDS:
                limits["seconds"] = True
                break
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            content = timed_dispatch(workspace, name, arguments, clock, reread=reread, require=require_reread)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
        if limits["tools"] or limits["seconds"]:
            break
    else:
        limits["turns"] = True
    diff = workspace.frozen_diff(origin)
    checked = apply_check(origin if prepare is not None else workspace.base, diff)
    trace = {
        "task_id": task["task_id"],
        "model": model,
        "events": workspace.events,
        "finish_reasons": reasons,
        "texts": texts,
        "tokens": {"input": tokens[0], "output": tokens[1]},
        "elapsed_s": round(time.perf_counter() - started, 3),
        "budget_hit": any(limits.values()),
        "budget_limits": limits,
        "turns_used": used_turns,
        "tool_seconds": {name: round(value, 3) for name, value in clock.items()},
        "reread_paths": sorted(reread),
        "error": error,
        "diff": diff,
        "diff_sha256": sha256_text(diff) if diff else None,
        "apply_check": checked,
        "card": card,
        "protocol": "Coding-v4",
    }
    trace["label"] = label_trace(trace)
    required = reread_path or (task["sources"][0] if require_reread else None)
    if required and trace["label"] == "PATCH_FORMED" and required not in reread:
        trace["label"] = "DID_NOT_REREAD"
        trace["diff"] = ""
    return trace


def save(directory: Path, trace: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    stored = dict(trace)
    diff = stored.pop("diff", "") or ""
    (directory / "trace.json").write_text(json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if diff:
        (directory / "patch.diff").write_text(diff, encoding="utf-8")


def mechanism(task: dict[str, Any], successor: bytes, old: bytes, *, name: str, phase: str = "dev", root: Path | None = None) -> dict[str, Any]:
    copied = dict(task)
    copied["legal_payload"] = lambda world, text=successor.decode("utf-8"): text
    rows = {}
    base = root or OUT
    for arm in ("B2", "B3"):
        cell = base / phase / task["task_id"] / "mechanism" / name / arm
        cell.mkdir(parents=True, exist_ok=True)
        evidence = run_cell(copied, arm, "S1", cell, old_payload=old, next_payload=successor, model="mechanism-successor", end_reason="legal_successor")
        rows[arm] = {
            "old_attempt_rejected": not bool(evidence.get("stale_credential_accepted")),
            "successor_effect_accepted": bool(evidence.get("successor_completed")),
            "stale_credential_accepted": evidence.get("stale_credential_accepted"),
        }
        (cell / "summary.json").write_text(json.dumps(rows[arm], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rows


def v1_pair(task: dict[str, Any]) -> tuple[str, str]:
    base = export_base(task)
    root = POOL / "_score" / "v4-dev-diffs" / task["task_id"]
    commented = root / "comment"
    target = root / "comment-fixed"
    materialize(base, task, commented, comment=True, use_fix=False)
    materialize(base, task, target, comment=True, use_fix=True)
    return unified_diff(base, commented), unified_diff(commented, target)


def raise_pair(task: dict[str, Any]) -> tuple[str, str]:
    base = export_base(task)
    hunk = first_hunk(task["source_patch"])
    if hunk is None:
        return "", ""
    path, line_number = hunk
    root = POOL / "_score" / "v4-dev-diffs" / task["task_id"]
    raised = root / "raise"
    materialize(base, task, raised, comment=False, use_fix=False)
    (raised / path).write_text(raise_text((base / path).read_text(encoding="utf-8"), line_number), encoding="utf-8")
    fixed = root / "raise-fixed"
    materialize(base, task, fixed, comment=False, use_fix=True)
    return unified_diff(base, raised), unified_diff(raised, fixed)


def dev() -> None:
    feasibility = json.loads((OUT / "feasibility" / "summary.json").read_text(encoding="utf-8"))
    by_id = {row["task_id"]: row for row in feasibility["rows"]}
    cache: dict[tuple, dict[str, Any]] = {}
    report = []
    for task in dev_tasks():
        card = problem_card(task)
        blob = json.dumps(card, ensure_ascii=False)
        if "diff --git" in blob or task["test_patch"][:80] in blob:
            raise SystemExit("prompt leaked hidden material")
        print("initial", task["task_id"], flush=True)
        initial = run_agent(task, card, suffix="", prepare=None, require_reread=False)
        hidden = score(task, initial.get("diff") if initial.get("apply_check", {}).get("ok") else None, None, cache)
        initial["container"] = {key: hidden.get(key) for key in ("ok", "passed", "image_id", "returncode", "error")}
        if initial["label"] == "PATCH_FORMED":
            initial["label"] = "HIDDEN_PASSED" if hidden.get("passed") else "HIDDEN_FAILED" if hidden.get("ok") else "ENVIRONMENT_BLOCKED"
        save(OUT / "dev" / task["task_id"] / "initial", initial)
        check = by_id[task["task_id"]]
        comment_delta, comment_successor = v1_pair(task)
        if sha256_text(comment_successor) != check["version_comment"]["successor_sha256"]:
            raise SystemExit(f"V1 successor bytes changed for {task['task_id']}")
        old_bytes = (initial.get("diff") or task["source_patch"]).encode("utf-8")
        if not old_bytes.strip():
            old_bytes = task["source_patch"].encode("utf-8")
        old_on_comment = score(task, task["source_patch"], comment_delta, cache)
        mech = mechanism(task, comment_successor.encode("utf-8"), old_bytes, name="v1")
        agent_label = "NOT_RUN"
        agent_passed = None
        if initial["label"] != "ENVIRONMENT_BLOCKED":
            print("agent-successor", task["task_id"], flush=True)

            def prepare(workspace: Workspace, source: str = task["sources"][0]) -> None:
                file = workspace.root / source
                text = file.read_text(encoding="utf-8")
                if not text.endswith("\n"):
                    text += "\n"
                file.write_text(text + "# epochgate-concurrent workspace version 2\n", encoding="utf-8")

            suffix = "\n\n上一次提交没有被接纳。源文件已经有并发修改。请重新 read_file 读取该文件，再提交一份新的 unified diff。"
            agent = run_agent(task, card, suffix=suffix, prepare=prepare, require_reread=True, reread_path=task["sources"][0])
            if agent["label"] == "DID_NOT_REREAD":
                agent_hidden = {"ok": True, "passed": False, "error": "DID_NOT_REREAD"}
            else:
                patch = agent.get("diff") if agent.get("apply_check", {}).get("ok") else None
                agent_hidden = score(task, patch, comment_delta, cache)
            agent["container"] = {key: agent_hidden.get(key) for key in ("ok", "passed", "image_id", "returncode", "error")}
            agent["concurrent"] = "v1-comment"
            if agent["label"] == "PATCH_FORMED":
                agent["label"] = "HIDDEN_PASSED" if agent_hidden.get("passed") else "HIDDEN_FAILED" if agent_hidden.get("ok") else "ENVIRONMENT_BLOCKED"
            agent["events_named"] = {
                "old_attempt_rejected": True,
                "new_state_observed": task["sources"][0] in agent.get("reread_paths", []),
                "successor_patch_generated": bool(agent.get("diff_sha256")) and agent["label"] != "DID_NOT_REREAD",
                "successor_effect_accepted": None,
                "successor_task_passed": bool(agent_hidden.get("passed")),
                "recovery_cost": {"elapsed_s": agent["elapsed_s"], "tokens": agent["tokens"], "tool_seconds": agent["tool_seconds"], "budget_limits": agent["budget_limits"]},
            }
            save(OUT / "dev" / task["task_id"] / "agent-successor", agent)
            agent_label = agent["label"]
            agent_passed = agent_hidden.get("passed")
        semantic = check["semantic_raise"]
        semantic_agent = "INVALID_FAULT" if semantic.get("invalid") else "NOT_RUN"
        if not semantic.get("invalid"):
            raised_delta, raised_successor = raise_pair(task)
            if sha256_text(raised_successor) != semantic["successor_sha256"]:
                raise SystemExit(f"raise successor bytes changed for {task['task_id']}")
            mechanism(task, raised_successor.encode("utf-8"), old_bytes, name="v3-frozen-raise")
            print("semantic-successor", task["task_id"], flush=True)
            hunk = first_hunk(task["source_patch"])

            def prepare_raise(workspace: Workspace, hunk_path: tuple[str, int] = hunk) -> None:
                path, line_number = hunk_path
                file = workspace.root / path
                file.write_text(raise_text(file.read_text(encoding="utf-8"), line_number), encoding="utf-8")

            semantic_trace = run_agent(
                task,
                card,
                suffix="\n\n上一次提交没有被接纳。源文件里有一处会改变行为的并发修改。请重新 read_file 读取该文件，再提交一份新的 unified diff。",
                prepare=prepare_raise,
                require_reread=True,
                reread_path=hunk[0],
            )
            if semantic_trace["label"] == "DID_NOT_REREAD":
                semantic_hidden = {"ok": True, "passed": False, "error": "DID_NOT_REREAD"}
            else:
                patch = semantic_trace.get("diff") if semantic_trace.get("apply_check", {}).get("ok") else None
                semantic_hidden = score(task, patch, raised_delta, cache)
            semantic_trace["container"] = {key: semantic_hidden.get(key) for key in ("ok", "passed", "image_id", "returncode", "error")}
            if semantic_trace["label"] == "PATCH_FORMED":
                semantic_trace["label"] = "HIDDEN_PASSED" if semantic_hidden.get("passed") else "HIDDEN_FAILED" if semantic_hidden.get("ok") else "ENVIRONMENT_BLOCKED"
            semantic_trace["events_named"] = {
                "old_attempt_rejected": True,
                "new_state_observed": hunk[0] in semantic_trace.get("reread_paths", []),
                "successor_patch_generated": bool(semantic_trace.get("diff_sha256")) and semantic_trace["label"] != "DID_NOT_REREAD",
                "successor_task_passed": bool(semantic_hidden.get("passed")),
                "recovery_cost": {"elapsed_s": semantic_trace["elapsed_s"], "tokens": semantic_trace["tokens"]},
            }
            save(OUT / "dev" / task["task_id"] / "semantic-successor", semantic_trace)
            semantic_agent = semantic_trace["label"]
        report.append({
            "task_id": task["task_id"],
            "initial_label": initial["label"],
            "initial_hidden_passed": initial.get("container", {}).get("passed"),
            "old_patch_still_passes_on_comment": old_on_comment.get("passed"),
            "mechanism_v1": mech,
            "agent_successor_label": agent_label,
            "agent_successor_passed": agent_passed,
            "semantic_invalid": semantic.get("invalid"),
            "semantic_agent_label": semantic_agent,
            "version_legal_successor": check["version_comment"]["legal_successor"],
            "unnecessary_rejection_v1": bool(old_on_comment.get("passed") and mech["B2"]["old_attempt_rejected"] and mech["B3"]["old_attempt_rejected"]),
        })
        print(json.dumps(report[-1], ensure_ascii=False), flush=True)
    formed = [row for row in report if row["initial_label"] in {"HIDDEN_PASSED", "HIDDEN_FAILED"}]
    successors = [row["agent_successor_label"] for row in report]
    all_capped = bool(successors) and all(label == "BUDGET_EXCEEDED" for label in successors)
    not_demonstrated = (not formed) or all_capped
    gate = {
        "confirm_started": not not_demonstrated,
        "scorable_patches": len(formed),
        "agent_successors": successors,
        "agent_successor_not_demonstrated": not_demonstrated,
        "rows": report,
    }
    (OUT / "dev" / "gate.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"confirm": gate["confirm_started"], "not_demonstrated": not_demonstrated, "scorable": len(formed)}, ensure_ascii=False), flush=True)
    if gate["confirm_started"]:
        confirm()


def confirm_tasks() -> list[dict[str, Any]]:
    frozen = json.loads((OUT / "frozen-tasks.json").read_text(encoding="utf-8"))
    rows = []
    for row in frozen["confirm"]:
        item = dict(row)
        repo = POOL / item["repo"]
        from coding_v3 import git
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
            "phase_pool": "v4-confirm",
        })
        rows.append(item)
    return rows


def ensure_packaging() -> dict[str, Any]:
    dest = POOL / "packaging"
    if (dest / ".git").is_dir():
        return {"cloned": True, "already_present": True}
    dest.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["git", "-c", "http.proxy=http://127.0.0.1:7890", "clone", "--filter=blob:none", "https://github.com/pypa/packaging.git", str(dest)],
        text=True, encoding="utf-8", errors="replace", capture_output=True, check=False,
    )
    return {"cloned": completed.returncode == 0 and (dest / ".git").is_dir(), "already_present": False, "returncode": completed.returncode, "tail": (completed.stderr or "")[-400:]}


def confirm() -> None:
    """Same harness as the dev loop. Packaging is cloned only after the dev gate. A clone failure is an environment block, not a substitute task."""
    clone = ensure_packaging()
    blocked = OUT / "confirm" / "environment.json"
    blocked.parent.mkdir(parents=True, exist_ok=True)
    if not clone["cloned"]:
        blocked.write_text(json.dumps({"status": "ENVIRONMENT_BLOCKED", "reason": "packaging_clone_failed", "clone": clone}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"confirm": "ENVIRONMENT_BLOCKED"}, ensure_ascii=False), flush=True)
        return
    cache: dict[tuple, dict[str, Any]] = {}
    rows = []
    for task in confirm_tasks():
        card = problem_card(task)
        blob = json.dumps(card, ensure_ascii=False)
        if "diff --git" in blob or task["test_patch"][:80] in blob:
            raise SystemExit("confirm prompt leaked hidden material")
        print("confirm-initial", task["task_id"], flush=True)
        initial = run_agent(task, card, suffix="", prepare=None, require_reread=False)
        hidden = score(task, initial.get("diff") if initial.get("apply_check", {}).get("ok") else None, None, cache)
        initial["container"] = {key: hidden.get(key) for key in ("ok", "passed", "image_id", "returncode", "error")}
        if initial["label"] == "PATCH_FORMED":
            initial["label"] = "HIDDEN_PASSED" if hidden.get("passed") else "HIDDEN_FAILED" if hidden.get("ok") else "ENVIRONMENT_BLOCKED"
        save(OUT / "confirm" / task["task_id"] / "initial", initial)
        comment_delta, comment_successor = v1_pair(task)
        old_bytes = (initial.get("diff") or task["source_patch"]).encode("utf-8")
        if not old_bytes.strip():
            old_bytes = task["source_patch"].encode("utf-8")
        mech = mechanism(task, comment_successor.encode("utf-8"), old_bytes, name="v1", phase="confirm")
        print("confirm-successor", task["task_id"], flush=True)

        def prepare(workspace: Workspace, source: str = task["sources"][0]) -> None:
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
        if agent["label"] == "PATCH_FORMED":
            agent["label"] = "HIDDEN_PASSED" if agent_hidden.get("passed") else "HIDDEN_FAILED" if agent_hidden.get("ok") else "ENVIRONMENT_BLOCKED"
        save(OUT / "confirm" / task["task_id"] / "agent-successor", agent)
        rows.append({
            "task_id": task["task_id"],
            "initial_label": initial["label"],
            "initial_hidden_passed": initial.get("container", {}).get("passed"),
            "agent_successor_label": agent["label"],
            "agent_successor_passed": agent_hidden.get("passed"),
            "mechanism_v1": mech,
        })
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    (OUT / "confirm" / "summary.json").write_text(json.dumps({"rows": rows, "denominator": len(rows)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    dev()
