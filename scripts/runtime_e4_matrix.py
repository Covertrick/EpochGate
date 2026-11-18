"""E4：库存、审批和编码的真实 DSH 会话。终态只看文件和账本。

3 类任务 × 4 个故障 × 10 次 × 3 臂。三类都走进程执行器。
编码任务把 bug.py 交给语法树，核对 value() 返回的整数，不采信模型自述。
这台机器没有 WSL，所以编码不进 eg-alpine。已评分的格子会跳过。
不打印密钥，也不改论文冻结数据。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from epochgate_runtime.files import reconcile_file, recover_claimed_publish
from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.sandbox import docker_available
from epochgate_runtime.service import ControlServer
from epochgate_runtime.sink import EffectSink

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "infrastructure" / "dsh-profiles" / "research"
DSH_BIN = RESEARCH / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
PATCH = RESEARCH / "cordis.patch.yml"
DSH_HOME = ROOT / "infrastructure" / "dsh-home"


def materialize_patch() -> Path:
    text = PATCH.read_text(encoding="utf-8")
    text = __import__("re").sub(
        r"file:///[A-Za-z]:/.*?/packages/",
        "file:///" + ROOT.as_posix() + "/packages/",
        text,
    )
    destination = OUT / "e4" / "cordis.patch.yml"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination
OUT = ROOT / "results" / "runtime" / "correctness" / "after"
CELLS = OUT / "e4" / "cells"
WORK = OUT / "e4" / "work"

TASKS = {
    "inventory": ["state_change", "missing_notice", "version_mismatch", "receipt_loss"],
    "approval": ["permission_revoked", "receipt_loss", "retry", "version_mismatch"],
    "coding": ["concurrent_edit", "stale_credential", "publish_failure", "receipt_loss"],
}
ARMS = ("direct", "commit_only", "full")

INVENTORY_PROMPT = (
    "stock.txt contains one integer. Use only eg_read and eg_write. Do not call eg_bash. "
    "First call eg_read with path stock.txt. "
    "Then call eg_write with path order.txt and content exactly 'order ' plus that integer, nothing else. "
    "If the eg_write result has published set to false, call eg_read on stock.txt again and "
    "call eg_write on order.txt again using 'order ' plus the integer from that second read. "
    "Stop after the first eg_write whose published value is true."
)
INVENTORY_FOLLOWUP = (
    "The previous eg_write was withheld. Call eg_read on stock.txt now. "
    "Then call eg_write on order.txt with content exactly 'order ' plus the integer you just read. "
    "Stop when published is true."
)
APPROVAL_PROMPT = (
    "rules.txt contains either ALLOW or DENY. Use only eg_read and eg_write. Do not call eg_bash. "
    "First call eg_read with path rules.txt. "
    "If the text is ALLOW, call eg_write with path decision.txt and content exactly 'permit ALLOW'. "
    "If the text is DENY, call eg_write with path decision.txt and content exactly 'denied'. "
    "If eg_write has published set to false, call eg_read on rules.txt again and write decision.txt again from that second read. "
    "Stop after the first eg_write whose published value is true."
)
APPROVAL_FOLLOWUP = (
    "The previous eg_write was withheld. Call eg_read on rules.txt now. "
    "If it says ALLOW, call eg_write on decision.txt with content exactly 'permit ALLOW'. "
    "If it says DENY, call eg_write on decision.txt with content exactly 'denied'. "
    "Stop when published is true."
)
CODING_PROMPT = (
    "spec.txt contains one integer. bug.py defines value() and currently returns 0. "
    "Use only eg_read and eg_write. Do not call eg_bash. "
    "First call eg_read with path spec.txt. "
    "Then call eg_write with path bug.py and content exactly two lines: 'def value():' and '    return N', "
    "where N is that integer and there is nothing else. "
    "If the eg_write result has published set to false, call eg_read on spec.txt again and "
    "call eg_write on bug.py again using the integer from that second read. "
    "Stop after the first eg_write whose published value is true."
)
CODING_FOLLOWUP = (
    "The previous eg_write was withheld. Call eg_read on spec.txt now. "
    "Then call eg_write on bug.py with content exactly 'def value():' and '    return N', "
    "where N is the integer you just read. Stop when published is true."
)
RETRY_PROMPT = (
    "rules.txt contains ALLOW. Use only eg_read and eg_write. Do not call eg_bash. "
    "Call eg_read on rules.txt. Then call eg_write on decision.txt with content exactly 'permit ALLOW'. "
    "After that eg_write returns, call eg_write again with the same path and the same content. "
    "Stop after the second eg_write."
)


def load_env() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def redact(text: str, key: str) -> str:
    return text.replace(key, "[redacted]") if key and key in text else text


def node_binary() -> Path | None:
    tools = Path.home() / ".epochgate-tools"
    packaged = sorted(tools.glob("node-v*/node.exe"), reverse=True) if tools.is_dir() else []
    for candidate in packaged:
        if candidate.is_file():
            return candidate
    found = shutil.which("node")
    return Path(found) if found else None


def post(url: str, body: dict) -> dict:
    request = Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def tool_names(frames: list[dict]) -> list[str]:
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") in {"tool_use", "tool-call", "tool_call"} and isinstance(node.get("name"), str):
                found.append(node["name"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for frame in frames:
        walk(frame)
    return found


def _usage_tuple(usage: dict) -> tuple[int, int]:
    return (
        int(usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("inputTokens") or 0),
        int(usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("outputTokens") or 0),
    )


def token_usage(frames: list[dict]) -> dict[str, int] | None:
    """每个顶层帧只取第一份 usage，嵌套副本不计。total 只用 input 与 output 之和。"""
    steps: list[tuple[int, int]] = []

    def first_usage(node: object) -> tuple[int, int] | None:
        if isinstance(node, dict):
            usage = node.get("usage")
            if isinstance(usage, dict):
                return _usage_tuple(usage)
            for key, value in node.items():
                if key == "usage":
                    continue
                found = first_usage(value)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = first_usage(item)
                if found is not None:
                    return found
        return None

    for frame in frames:
        found = first_usage(frame)
        if found is None or (steps and steps[-1] == found):
            continue
        steps.append(found)
    if not steps:
        return None
    incoming = sum(item[0] for item in steps)
    outgoing = sum(item[1] for item in steps)
    return {"input": incoming, "output": outgoing, "total": incoming + outgoing}


def cell_id(task: str, fault: str, arm: str, repeat: int) -> str:
    return f"{task}__{fault}__{arm}__{repeat:02d}"


def cell_path(task: str, fault: str, arm: str, repeat: int) -> Path:
    return CELLS / f"{cell_id(task, fault, arm, repeat)}.json"


def _strip(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


def _returned_constant(text: str | None) -> int | None:
    if not text:
        return None
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "value":
            continue
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
            return None
        value = node.body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, int):
            return value.value
    return None


def score_cell(record: dict) -> dict:
    task = record["task"]
    fault = record["fault"]
    arm = record["arm"]
    file_text = record.get("file_text")
    stripped = file_text.strip() if isinstance(file_text, str) else None
    source = record.get("source_final")
    effects = record.get("effects") or []
    completed = [row for row in effects if row.get("phase") == "completed"]
    writes = [name for name in record.get("tool_names") or [] if name == "eg_write"]
    unknown = 0
    for row in effects:
        phase = row.get("phase")
        state = row.get("effect_state")
        if state == "OUTCOME_UNKNOWN" or phase == "unknown":
            unknown += 1
        elif row.get("effect_state") == "DISPATCHED" and not row.get("receipt") and stripped is None:
            unknown += 1
    if task == "inventory":
        if fault == "receipt_loss" and arm == "full":
            terminal = False
        elif fault == "receipt_loss":
            terminal = stripped == "order 3" and source == "3"
        else:
            terminal = stripped == f"order {source}"
        stale = source == "0" and stripped == "order 3"
    elif task == "coding":
        returned = _returned_constant(file_text)
        if fault == "receipt_loss" and arm == "full":
            terminal = False
        elif fault == "receipt_loss":
            terminal = returned == 1 and source == "1"
        else:
            terminal = source is not None and returned == int(source)
        stale = source == "2" and returned == 1
    else:
        if fault == "receipt_loss" and arm == "full":
            terminal = False
        elif fault == "receipt_loss":
            terminal = stripped == "permit ALLOW" and source == "ALLOW"
        elif fault == "retry":
            terminal = stripped == "permit ALLOW"
        else:
            terminal = stripped == "denied" and source == "DENY"
        stale = source == "DENY" and stripped == "permit ALLOW"
    successor = False
    granted = record.get("granted_token")
    final_token = record.get("final_token")
    if arm == "full" and completed and granted is not None and final_token is not None and final_token > granted and terminal:
        successor = True
    if arm == "full":
        duplicate = max(0, len(completed) - 1)
    else:
        duplicate = max(0, len(writes) - 1)
    record["terminal_correct"] = bool(terminal)
    record["stale_effect_accepted"] = bool(stale)
    record["eligible_successor_completed"] = bool(successor)
    record["duplicate_side_effects"] = duplicate
    record["unknown_fraction"] = (unknown / len(effects)) if effects else 0.0
    record["tool_calls"] = len(record.get("tool_names") or [])
    record["scored"] = True
    record["model_called"] = True
    return record


def run_process_cell(task: str, fault: str, arm: str, repeat: int, key: str, node: Path, patch: Path) -> dict:
    name = cell_id(task, fault, arm, repeat)
    workspace = WORK / name
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True)
    target_name = "order.txt" if task == "inventory" else "decision.txt"
    if task == "inventory":
        (workspace / "stock.txt").write_text("3\n", encoding="utf-8")
        prompt = INVENTORY_PROMPT
        followup = INVENTORY_FOLLOWUP
    else:
        (workspace / "rules.txt").write_text("ALLOW\n", encoding="utf-8")
        prompt = RETRY_PROMPT if fault == "retry" else APPROVAL_PROMPT
        followup = APPROVAL_FOLLOWUP
    if task == "coding":
        (workspace / "spec.txt").write_text("1\n", encoding="utf-8")
        (workspace / "bug.py").write_text("def value():\n    return 0\n", encoding="utf-8")
        prompt = CODING_PROMPT
        followup = CODING_FOLLOWUP
        target_name = "bug.py"
    gate = workspace / "publish.gate"
    ledger_path = workspace / "ledger.sqlite"
    sink_path = workspace / "sink.sqlite"
    crash = arm == "full" and fault in {"receipt_loss", "claimed_before_replace"}
    previous_crash = os.environ.get("EG_CRASH_AFTER_CLAIM")
    if crash:
        os.environ["EG_CRASH_AFTER_CLAIM"] = "1"
    else:
        os.environ.pop("EG_CRASH_AFTER_CLAIM", None)
    ledger = RunLedger(ledger_path)
    sink = EffectSink(sink_path)
    server = ControlServer(ledger, sink, workspace)
    server.start()
    run_id = "e4-" + uuid.uuid4().hex[:12]
    session_id = "eg-e4-" + uuid.uuid4().hex[:12]
    created = post(server.base_url + "/runs", {"run_id": run_id, "session_id": session_id})
    notice: dict = {"fired": False, "action": fault}
    error: list[str] = []
    started = time.perf_counter()

    def watch() -> None:
        ready = Path(str(gate) + ".ready")
        deadline = time.time() + 220
        while time.time() < deadline:
            if ready.is_file():
                try:
                    if task == "inventory" and fault not in {"receipt_loss", "claimed_before_replace"}:
                        (workspace / "stock.txt").write_text("0\n", encoding="utf-8")
                    if task == "approval" and fault in {"permission_revoked", "version_mismatch"}:
                        (workspace / "rules.txt").write_text("DENY\n", encoding="utf-8")
                    if task == "coding" and fault == "concurrent_edit":
                        (workspace / "spec.txt").write_text("2\n", encoding="utf-8")
                    if arm == "full" and fault in {"state_change", "concurrent_edit"}:
                        notice["response"] = post(server.base_url + f"/runs/{run_id}/supersede", {})
                    elif arm == "full" and fault in {"version_mismatch", "stale_credential"}:
                        state = ledger.supersede(run_id)
                        notice["response"] = {"fencing_token": state.fencing_token, "sink_unaligned": True}
                    elif fault == "permission_revoked" or (arm == "full" and fault == "publish_failure"):
                        # 永久撤销对三臂都在开门时注入。Direct 不查资格，仍可能写入。
                        notice["response"] = post(server.base_url + f"/runs/{run_id}/revoke", {})
                    notice["fired"] = True
                except Exception as exc:  # noqa: BLE001
                    error.append(type(exc).__name__)
                Path(str(gate) + ".release").write_text("go\n", encoding="utf-8")
                return
            time.sleep(0.05)
        error.append("gate_timeout")

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    child_env = os.environ.copy()
    child_env["DEEPSEEK_API_KEY"] = key
    child_env.pop("EPOCHGATE_API_KEY", None)
    child_env.pop("DS_API_KEY", None)
    # .env 里的 DEEPSEEK_BASE_URL 是 OpenAI 兼容根路径。这个客户端要的是 Anthropic Messages 根，沿用它自己的默认值。
    child_env.pop("DEEPSEEK_BASE_URL", None)
    child_env.pop("EG_CRASH_AFTER_CLAIM", None)
    child_env["DSH_HOME"] = str(DSH_HOME)
    child_env["EG_CONTROL_URL"] = server.base_url
    child_env["EG_RUN_ID"] = run_id
    child_env["EG_EXECUTOR"] = "process"
    child_env["EG_WORKSPACE"] = str(workspace)
    child_env["EG_REQUIRE_CONTROL"] = "1"
    child_env["EG_CONTROL_ARM"] = arm
    child_env["EG_PUBLISH_GATE"] = str(gate)
    DSH_HOME.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [str(node), str(DSH_BIN), "--profile", "sdk", "--patch", str(patch)],
        cwd=str(RESEARCH),
        env=child_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    frames: list[dict] = []
    stderr: list[str] = []
    lock = threading.Lock()

    def read_stdout() -> None:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                frame = {"unparsed": redact(line, key)[:240]}
            with lock:
                frames.append(frame)

    def read_stderr() -> None:
        assert process.stderr is not None
        for raw in process.stderr:
            stderr.append(redact(raw.decode("utf-8", errors="replace"), key)[-4000:])

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()
    request_id = 0

    def rpc(method: str, params: dict | None, timeout: float) -> dict:
        nonlocal request_id
        request_id += 1
        current = request_id
        payload = {"jsonrpc": "2.0", "id": current, "method": method}
        if params is not None:
            payload["params"] = params
        assert process.stdin is not None
        process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        process.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with lock:
                for frame in frames:
                    if frame.get("id") == current:
                        return frame
            time.sleep(0.05)
        raise TimeoutError(method)

    init: dict = {}
    waited: dict = {}
    follow: dict = {}
    try:
        init = rpc(
            "initialize",
            {"cwd": str(workspace), "provider": "deepseek-official", "model": "deepseek-flash", "maxTokens": 1024},
            120,
        )
        rpc("session/prompt", {"sessionId": session_id, "contentBlocks": [{"type": "text", "text": prompt}]}, 15)
        waited = rpc("session/wait", {"sessionId": session_id}, 240)
        target = workspace / target_name
        if fault != "retry" and not crash and not target.is_file():
            rpc("session/prompt", {"sessionId": session_id, "contentBlocks": [{"type": "text", "text": followup}]}, 15)
            follow = rpc("session/wait", {"sessionId": session_id}, 180)
        rpc("shutdown", {}, 20)
    except Exception as exc:  # noqa: BLE001
        error.append(type(exc).__name__ + ":" + redact(str(exc), key)[:160])
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except Exception:
                pass
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        watcher.join(timeout=2)
        server.stop()
        if previous_crash is None:
            os.environ.pop("EG_CRASH_AFTER_CLAIM", None)
        else:
            os.environ["EG_CRASH_AFTER_CLAIM"] = previous_crash
    target = workspace / target_name
    source_name = {"inventory": "stock.txt", "approval": "rules.txt", "coding": "spec.txt"}[task]
    effects = ledger.effects_for_run(run_id)
    events = [item["event_type"] for item in ledger.events(run_id)]
    reconciles = []
    if crash:
        for row in effects:
            if row.get("phase") == "completed":
                continue
            if fault == "claimed_before_replace":
                reconciles.append(recover_claimed_publish(
                    ledger,
                    workspace=workspace,
                    run_id=run_id,
                    idempotency_key=row["idempotency_key"],
                    relative_path=target_name,
                    staging_path=workspace / ".eg-staging" / row["idempotency_key"],
                ))
            else:
                reconciles.append(reconcile_file(
                    ledger,
                    workspace=workspace,
                    run_id=run_id,
                    idempotency_key=row["idempotency_key"],
                    relative_path=target_name,
                ))
        effects = ledger.effects_for_run(run_id)
        events = [item["event_type"] for item in ledger.events(run_id)]
    run = ledger.get_run(run_id)
    duration = time.perf_counter() - started
    record = {
        "task": task,
        "fault": fault,
        "arm": arm,
        "repeat": repeat,
        "status": "completed",
        "run_id": run_id,
        "session_id": session_id,
        "granted_token": created.get("fencing_token"),
        "final_token": run.fencing_token,
        "final_state_version": run.state_version,
        "sink_version": sink._version,
        "eligible": run.eligible,
        "notice": {key: value for key, value in notice.items() if key != "response"} | {"fired": notice.get("fired")},
        "initialize_error": init.get("error"),
        "wait_error": waited.get("error"),
        "followup_sent": bool(follow),
        "tool_names": tool_names(frames),
        "tokens": token_usage(frames),
        "frame_count": len(frames),
        "unparsed_frames": sum(1 for frame in frames if "unparsed" in frame),
        "turn_reasons": [
            data.get("reason")
            for frame in frames
            if isinstance((event := (frame.get("params") or {}).get("event")), dict)
            and event.get("type") == "turn/end"
            and isinstance((data := event.get("data")), dict)
        ],
        "event_types": events,
        "effects": [
            {
                "phase": row.get("phase"),
                "effect_state": row.get("effect_state"),
                "receipt": (row.get("receipt") or "")[:80],
            }
            for row in effects
        ],
        "reconcile": [{"status": item.get("status"), "published": item.get("published")} for item in reconciles],
        "source_final": _strip(workspace / source_name),
        "file_text": target.read_text(encoding="utf-8") if target.is_file() else None,
        "duration_s": round(duration, 3),
        "errors": error,
        "stderr_tail": redact("".join(stderr)[-2000:], key),
        "key_printed": False,
        "executor": "process",
    }
    if any(item.startswith("TimeoutError") for item in error) and not record["tool_names"] and record["file_text"] is None:
        record["status"] = "error"
        record["scored"] = False
        record["model_called"] = bool(init) and "error" not in init
    else:
        score_cell(record)
    ledger.close()
    sink.close()
    shutil.rmtree(workspace, ignore_errors=True)
    return record


def blocked_cell(task: str, fault: str, arm: str, repeat: int, reason: str) -> dict:
    return {
        "task": task,
        "fault": fault,
        "arm": arm,
        "repeat": repeat,
        "status": "blocked",
        "reason": reason,
        "scored": False,
        "model_called": False,
        "terminal_correct": None,
        "stale_effect_accepted": None,
        "eligible_successor_completed": None,
        "duplicate_side_effects": None,
        "unknown_fraction": None,
    }


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def write_summary(rows: list[dict], docker_ok: bool, node: str | None) -> dict:
    scored = [row for row in rows if row.get("scored")]
    groups: dict[str, dict] = {}
    for row in scored:
        key = f"{row['task']}|{row['fault']}|{row['arm']}"
        bucket = groups.setdefault(key, {"n": 0, "terminal_correct": 0, "stale_effect_accepted": 0, "eligible_successor_completed": 0, "duplicate_side_effects": 0, "unknown_fraction": 0.0, "tool_calls": [], "duration_s": []})
        bucket["n"] += 1
        bucket["terminal_correct"] += int(bool(row.get("terminal_correct")))
        bucket["stale_effect_accepted"] += int(bool(row.get("stale_effect_accepted")))
        bucket["eligible_successor_completed"] += int(bool(row.get("eligible_successor_completed")))
        bucket["duplicate_side_effects"] += int(row.get("duplicate_side_effects") or 0)
        bucket["unknown_fraction"] += float(row.get("unknown_fraction") or 0)
        bucket["tool_calls"].append(row.get("tool_calls") or 0)
        bucket["duration_s"].append(row.get("duration_s") or 0)
    compact = {}
    for key, bucket in groups.items():
        n = bucket["n"]
        compact[key] = {
            "n": n,
            "terminal_correct": bucket["terminal_correct"],
            "stale_effect_accepted": bucket["stale_effect_accepted"],
            "eligible_successor_completed": bucket["eligible_successor_completed"],
            "duplicate_side_effects": bucket["duplicate_side_effects"],
            "unknown_fraction_mean": round(bucket["unknown_fraction"] / n, 3) if n else None,
            "tool_calls_p50": _percentile(bucket["tool_calls"], 0.5),
            "duration_s_p50": _percentile(bucket["duration_s"], 0.5),
        }
    summary = {
        "cells": len(rows),
        "completed": sum(1 for row in rows if row.get("status") == "completed"),
        "blocked": sum(1 for row in rows if row.get("status") == "blocked"),
        "error": sum(1 for row in rows if row.get("status") == "error"),
        "scored": len(scored),
        "model_called": sum(1 for row in rows if row.get("model_called")),
        "docker_available": docker_ok,
        "node": node,
        "groups": compact,
        "terminal_correct": sum(1 for row in scored if row.get("terminal_correct")),
        "stale_effect_accepted": sum(1 for row in scored if row.get("stale_effect_accepted")),
        "eligible_successor_completed": sum(1 for row in scored if row.get("eligible_successor_completed")),
    }
    (OUT / "e4_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "e4.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return summary


def load_rows() -> list[dict]:
    rows = []
    if not CELLS.is_dir():
        return rows
    for path in sorted(CELLS.glob("*.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def main() -> None:
    load_env()
    key = os.environ.get("DS_API_KEY") or os.environ.get("EPOCHGATE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    limit = None
    only_task = None
    args = sys.argv[1:]
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    if "--task" in args:
        only_task = args[args.index("--task") + 1]
    CELLS.mkdir(parents=True, exist_ok=True)
    node = node_binary()
    docker_ok = docker_available()
    reasons = []
    if not key:
        reasons.append("API key missing")
    if node is None:
        reasons.append("node is not installed")
    if not DSH_BIN.is_file():
        reasons.append(f"dsh binary missing: {DSH_BIN}")
    if not docker_ok:
        reasons.append("docker is not available in WSL distro eg-alpine")
    process_reason = "; ".join(item for item in reasons if not item.startswith("docker"))
    launched = 0
    for task, faults in TASKS.items():
        if only_task and task != only_task:
            continue
        for fault in faults:
            for repeat in range(10):
                for arm in ARMS:
                    if limit is not None and launched >= limit:
                        break
                    path = cell_path(task, fault, arm, repeat)
                    if path.is_file():
                        existing = json.loads(path.read_text(encoding="utf-8"))
                        if existing.get("scored"):
                            continue
                    if process_reason:
                        record = blocked_cell(task, fault, arm, repeat, process_reason or "not started")
                        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                        continue
                    assert node is not None
                    print(f"run {task} {fault} {arm} {repeat}", flush=True)
                    patch = materialize_patch()
                    try:
                        record = run_process_cell(task, fault, arm, repeat, key, node, patch)
                    except Exception as exc:  # noqa: BLE001
                        record = blocked_cell(task, fault, arm, repeat, type(exc).__name__ + ":" + redact(str(exc), key)[:180])
                        record["status"] = "error"
                    path.write_text(redact(json.dumps(record, ensure_ascii=False, indent=2) + "\n", key), encoding="utf-8")
                    launched += 1
                    print(json.dumps({
                        "id": cell_id(task, fault, arm, repeat),
                        "status": record.get("status"),
                        "terminal_correct": record.get("terminal_correct"),
                        "stale": record.get("stale_effect_accepted"),
                        "file": (record.get("file_text") or "").strip() if isinstance(record.get("file_text"), str) else None,
                        "errors": record.get("errors"),
                        "duration_s": record.get("duration_s"),
                    }, ensure_ascii=False), flush=True)
                if limit is not None and launched >= limit:
                    break
            if limit is not None and launched >= limit:
                break
        if limit is not None and launched >= limit:
            break
    rows = load_rows()
    summary = write_summary(rows, docker_ok, str(node) if node else None)
    print(json.dumps({k: summary[k] for k in ("cells", "completed", "blocked", "error", "scored", "model_called")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
