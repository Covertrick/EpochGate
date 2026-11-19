"""Coding Pilot 与确认。任务清单来自筛选结果，按协议排序后冻结。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from m4_matrix import _post, git_sha, run_cell
from m4_tasks import ARMS, FAULTS
from reliability_contract import sha256_text
from runtime_deepseek import load_env
import os

ROOT = Path(__file__).resolve().parents[1]
POOL = Path(r"C:\Project\epochgate-pool")
SCREEN = ROOT / "results" / "runtime" / "correctness" / "v2-coding-pool" / "screen.json"
MANIFEST = ROOT / "results" / "runtime" / "correctness" / "v2-coding-pool" / "manifest.json"
PROTOCOL = ROOT / "docs" / "开发文档" / "M4-M6-v2协议.md"
DOCKER = r"C:\Users\金瑞翀\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"
IMAGE = "python:3.10-slim"
EXPECTED_ID = "sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d"

TOOLS = [
    {"type": "function", "function": {"name": "list_sources", "description": "列出可修改的源文件", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "读取一个源文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "submit_patch", "description": "提交 unified diff。不能改 tests。", "parameters": {"type": "object", "properties": {"diff": {"type": "string"}}, "required": ["diff"], "additionalProperties": False}}},
]
PROMPT = (
    "你在修复一个真实仓库的缺陷。先读取至少两个源文件，然后提交一次 unified diff。"
    "工作区里没有隐藏测试。不要修改 tests 目录，也不要声称测试已经通过。"
    "如果工具说版本已更新，仍提交你在更新前已经形成的补丁。"
)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-300:])
    return completed.stdout


def freeze_manifest() -> dict[str, Any]:
    if MANIFEST.is_file():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    screen = json.loads(SCREEN.read_text(encoding="utf-8"))
    if screen.get("status") != "READY":
        report = {"status": screen.get("status", "TASK_POOL_INSUFFICIENT"), "tasks": []}
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    chosen = screen["feasible"][:6]
    tasks = []
    for row in chosen:
        repo = POOL / row["repo"]
        test_patch = _git(repo, "diff", row["base"], row["fix"], "--", *row["tests"])
        source_patch = _git(repo, "diff", row["base"], row["fix"], "--", *row["sources"])
        task_id = f"code-{row['repo']}-{row['base'][:8]}"
        tasks.append({
            "task_id": task_id,
            "family": "coding",
            "form": "coding",
            "repo": row["repo"],
            "base": row["base"],
            "fix": row["fix"],
            "tests": row["tests"],
            "sources": row["sources"],
            "test_patch": test_patch,
            "source_patch_sha256": sha256_text(source_patch),
            "relative": "patch.diff",
            "constraints": ["workspace_version", "hidden_tests"],
            "event": {"set": {"version": 2}},
            "initial": {"version": 1},
            "successor_applicable": True,
            "s2_mode": "missing" if int(task_id[-1], 36) % 2 == 0 else "delayed",
            "phase_pool": "pilot" if len(tasks) < 2 else "confirm",
        })
    report = {
        "status": "FROZEN",
        "protocol_sha256": sha256_text(PROTOCOL.read_text(encoding="utf-8")),
        "pilot": [task["task_id"] for task in tasks[:2]],
        "confirm": [task["task_id"] for task in tasks[2:6]],
        "tasks": tasks,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def export_base(task: dict[str, Any]) -> Path:
    destination = POOL / "_tasks" / task["task_id"] / "base"
    if destination.exists():
        return destination
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "archive", task["base"]], cwd=POOL / task["repo"], capture_output=True, check=False)
    if archive.returncode != 0:
        raise RuntimeError(archive.stderr.decode("utf-8", "replace")[-300:])
    subprocess.run(["tar", "-x", "-C", str(destination)], input=archive.stdout, check=True)
    return destination


def source_files(root: Path) -> list[str]:
    found = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("tests/") or "/tests/" in f"/{relative}":
            continue
        found.append(relative)
    return sorted(found)[:60]


def classify_trace(trace: dict[str, Any]) -> str:
    error = trace.get("error") or ""
    if str(error).startswith("http_") or error in {"TimeoutError", "URLError"}:
        return "MODEL_API_FAILED"
    if error == "missing_key":
        return "ENVIRONMENT_BLOCKED"
    text = "\n".join(trace.get("texts") or [])
    if any(token in text for token in ("无法完成", "不能提交", "拒绝提交", "没有足够信息")):
        return "CORRECT_ABSTENTION"
    if trace.get("events") or trace.get("finish_reasons"):
        return "MODEL_DID_NOT_ACT"
    return "OUTCOME_UNKNOWN"


def propose(task: dict[str, Any], fault: str, root: Path) -> dict[str, Any]:
    load_env()
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
    files = {name: (root / name).read_text(encoding="utf-8", errors="replace") for name in source_files(root)}
    reads = 0
    injected = False
    events: list[dict[str, Any]] = []
    reasons: list[str] = []
    texts: list[str] = []
    messages: list[dict[str, Any]] = [{"role": "user", "content": PROMPT + "\n任务 " + task["task_id"]}]
    tokens = [0, 0]
    diff = None

    def tool_result(name: str, arguments: dict[str, Any]) -> str:
        nonlocal reads, injected, diff
        events.append({"tool": name, "path": arguments.get("path"), "diff_sha256": sha256_text(arguments.get("diff", "")) if name == "submit_patch" else None})
        if name == "list_sources":
            return json.dumps(sorted(files), ensure_ascii=False)
        if name == "read_file":
            path = arguments.get("path") or ""
            if path.startswith("tests/") or path not in files:
                return json.dumps({"error": "hidden_or_missing"}, ensure_ascii=False)
            reads += 1
            if reads >= 2 and not injected:
                injected = True
                marker = files.get("README.md") or files.get("README.rst") or ""
                if "README.md" in files:
                    files["README.md"] = files["README.md"] + "\n# epochgate-workspace\n"
                elif "README.rst" in files:
                    files["README.rst"] = files["README.rst"] + "\n.. epochgate-workspace\n"
            notice = None
            instruction = "继续读取一个源文件。"
            if injected and fault == "S1":
                notice = "通知已送达：工作区版本已更新。请提交你在通知前已经形成的补丁。"
            if reads > 2:
                instruction = "不要再读取。请立刻调用 submit_patch。"
                return json.dumps({"notice": notice, "instruction": instruction, "reads": reads}, ensure_ascii=False)
            if reads == 2:
                instruction = "两次读取已完成。请立刻调用 submit_patch，提交你已经形成的 unified diff。"
            body = files[path]
            return json.dumps({"text": body[:2500], "notice": notice, "instruction": instruction, "reads": reads}, ensure_ascii=False)
        patch = arguments.get("diff") or ""
        if reads < 2:
            return json.dumps({"error": "need_two_reads"}, ensure_ascii=False)
        if "\ntests/" in "\n" + patch or patch.startswith("diff --git a/tests/"):
            return json.dumps({"error": "tests_are_hidden"}, ensure_ascii=False)
        diff = patch
        return json.dumps({"status": "proposed"}, ensure_ascii=False)

    for _ in range(8):
        if diff:
            break
        result = _post({"model": model, "messages": messages, "tools": TOOLS, "temperature": 0, "max_tokens": 4000})
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error"), "events": events, "finish_reasons": reasons, "texts": texts, "tokens": tokens, "model": model}
        payload = result["payload"]
        usage = payload.get("usage") or {}
        tokens[0] += int(usage.get("prompt_tokens") or 0)
        tokens[1] += int(usage.get("completion_tokens") or 0)
        message = payload["choices"][0]["message"]
        reasons.append(payload["choices"][0].get("finish_reason") or "")
        if message.get("content"):
            texts.append(str(message.get("content"))[:400])
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            break
        for call in calls:
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": tool_result(name, arguments)})
    trace = {"ok": bool(diff), "error": None if diff else "model_no_effect", "events": events, "finish_reasons": reasons, "texts": texts, "tokens": tokens, "model": model, "diff": diff}
    if not diff:
        trace["label"] = classify_trace(trace)
    return trace


def image_id() -> str:
    completed = subprocess.run([DOCKER, "image", "inspect", IMAGE, "--format", "{{.Id}}"], text=True, capture_output=True, check=False)
    return completed.stdout.strip()


def run_hidden_tests(task: dict[str, Any], patch: bytes | None) -> dict[str, Any]:
    current = image_id()
    if current != EXPECTED_ID:
        return {"ok": False, "error": "image_mismatch", "image_id": current, "passed": False}
    work = POOL / "_score" / task["task_id"]
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(export_base(task), work)
    readme = next((name for name in ("README.md", "README.rst", "README") if (work / name).is_file()), None)
    if readme:
        with (work / readme).open("a", encoding="utf-8") as handle:
            handle.write("\n# epochgate-workspace\n")
    if patch:
        applied = subprocess.run(["git", "apply", "--unsafe-paths", "-"], cwd=work, input=patch.decode("utf-8", "replace"), text=True, capture_output=True, check=False)
        if applied.returncode != 0:
            return {"ok": True, "apply_error": (applied.stderr or "")[-300:], "passed": False, "image_id": current, "tail": "patch_rejected"}
    test_file = work / "_hidden.patch"
    test_file.write_text(task["test_patch"], encoding="utf-8")
    applied_tests = subprocess.run(["git", "apply", "--unsafe-paths", str(test_file)], cwd=work, text=True, capture_output=True, check=False)
    test_file.unlink(missing_ok=True)
    if applied_tests.returncode != 0:
        return {"ok": False, "error": "hidden_test_apply_failed", "image_id": current, "passed": False, "tail": (applied_tests.stderr or "")[-300:]}
    wheels = Path(r"C:\Project\epochgate-pool\_wheels")
    tests = " ".join(task["tests"])
    script = (
        "pip install -q --no-index --find-links /wheels pytest || { echo INSTALL_FAILED; exit 3; }; "
        "PYTHONPATH=src:. python -m pytest " + tests + " -q --tb=line -p no:cacheprovider"
    )
    completed = subprocess.run(
        [DOCKER, "run", "--rm", "-v", f"{wheels}:/wheels", "-v", f"{work}:/src", "-w", "/src", IMAGE, "bash", "-lc", script],
        text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=180, check=False,
    )
    text = (completed.stdout + "\n" + completed.stderr).strip()
    return {"ok": True, "passed": completed.returncode == 0 and "passed" in text.lower(), "image_id": current, "tail": "\n".join(text.splitlines()[-6:])[-800:], "returncode": completed.returncode}


def run_phase(phase: str) -> dict[str, Any]:
    manifest = freeze_manifest()
    out = ROOT / "results" / "runtime" / "correctness" / f"v2-coding-{phase}"
    out.mkdir(parents=True, exist_ok=True)
    if manifest.get("status") != "FROZEN":
        summary = {"phase": phase, "status": manifest.get("status", "TASK_POOL_INSUFFICIENT"), "rows": []}
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    if phase == "confirm":
        pilot_summary = ROOT / "results" / "runtime" / "correctness" / "v2-coding-pilot" / "summary.json"
        if not pilot_summary.is_file():
            summary = {"phase": phase, "status": "PILOT_NOT_RUN", "rows": []}
            (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            return summary
        pilot = json.loads(pilot_summary.read_text(encoding="utf-8"))
        rows_done = pilot.get("rows") or []
        crashes = sum(1 for row in rows_done if row.get("environment_blocked"))
        if rows_done and crashes * 2 > len(rows_done):
            summary = {"phase": phase, "status": "PILOT_ENVIRONMENT_UNSTABLE", "rows": []}
            (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            return summary
    tasks = [task for task in manifest["tasks"] if task["phase_pool"] == phase]
    rows = []
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    for task in tasks:
        base = export_base(task)
        task = dict(task)
        task["legal_payload"] = lambda world: ""
        for fault in FAULTS:
            key = (task["task_id"], fault)
            if key not in cache:
                cache[key] = propose(task, fault, base)
            proposal = cache[key]
            if not proposal.get("ok"):
                for arm in ARMS:
                    rows.append({
                        "task_id": task["task_id"], "family": "coding", "arm": arm, "fault": fault, "runtime": "epochgate",
                        "blocked": True, "error_type": proposal.get("error"), "end_reason": "model_unavailable",
                        "blocked_label": proposal.get("label"), "trace": {k: proposal.get(k) for k in ("events", "finish_reasons", "texts", "tokens", "model")},
                        "git_sha": git_sha(), "base": task["base"], "fix": task["fix"], "image_digest": image_id(),
                        "protocol_sha256": sha256_text(PROTOCOL.read_text(encoding="utf-8")),
                    })
                continue
            payload = proposal["diff"].encode("utf-8")
            for arm in ARMS:
                cell = out / f"{task['task_id']}__{arm}__{fault}"
                try:
                    evidence = run_cell(
                        task, arm, fault, cell, old_payload=payload, next_payload=None, model=proposal.get("model"),
                        tool_events_extra=proposal.get("events"), tokens=tuple(proposal.get("tokens") or (0, 0)), end_reason="model",
                    )
                    physical = (cell / "patch.diff").read_bytes() if (cell / "patch.diff").is_file() else None
                    scored = run_hidden_tests(task, physical)
                except (subprocess.TimeoutExpired, OSError, RuntimeError) as exc:
                    evidence = {
                        "task_id": task["task_id"], "family": "coding", "arm": arm, "fault": fault,
                        "blocked": True, "environment_blocked": True, "blocked_label": "ENVIRONMENT_BLOCKED",
                        "error_type": type(exc).__name__,
                    }
                    scored = {"ok": False, "error": type(exc).__name__, "passed": False}
                evidence["container"] = scored
                evidence["image_digest"] = scored.get("image_id") or image_id()
                evidence["base"] = task["base"]
                evidence["fix"] = task["fix"]
                evidence["git_sha"] = evidence.get("git_sha") or git_sha()
                evidence["protocol_sha256"] = sha256_text(PROTOCOL.read_text(encoding="utf-8"))
                if not scored.get("ok"):
                    evidence["blocked"] = True
                    evidence["environment_blocked"] = True
                    evidence["blocked_label"] = evidence.get("blocked_label") or "ENVIRONMENT_BLOCKED"
                    evidence["error_type"] = scored.get("error") or evidence.get("error_type")
                else:
                    evidence["container_tests_passed"] = bool(scored.get("passed"))
                    evidence["task_terminal_match"] = bool(scored.get("passed"))
                    evidence["stale_patch_published"] = bool(evidence.get("stale_credential_accepted"))
                (cell / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
                rows.append(evidence)
    summary = {"phase": phase, "status": "SCORED", "n": len(rows), "blocked": sum(1 for row in rows if row.get("blocked")), "rows": rows}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("pilot", "confirm"), required=True)
    args = parser.parse_args()
    summary = run_phase(args.phase)
    print(json.dumps({"phase": args.phase, "status": summary.get("status"), "n": summary.get("n"), "blocked": summary.get("blocked")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
