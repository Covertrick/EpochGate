"""M4 单元格。脚本路径供机制测试；模型路径才进入 Pilot 和确认。"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from reliability_contract import (
    collect_evidence,
    commit_effect,
    deliver_notice,
    inject_world_change,
    observe_tool_call,
    open_run,
    oracle_from_artifacts,
    resume_execution,
    sha256_bytes,
    sha256_text,
)
from runtime_deepseek import api_key, load_env

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs" / "开发文档" / "M4-M6独立确认协议.md"


def protocol_sha() -> str:
    return sha256_text(PROTOCOL.read_text(encoding="utf-8"))


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def notice_mode(task: dict[str, Any], fault: str) -> str:
    if fault == "S1":
        return "delivered"
    if fault == "S3":
        return "missing"
    return task["s2_mode"]


def _canonical(task: dict[str, Any], arguments: dict[str, Any]) -> bytes:
    if task["form"] == "order":
        body = {
            "config_version": arguments.get("config_version"),
            "customer": arguments.get("customer"),
            "price_version": arguments.get("price_version"),
            "qty": arguments.get("qty"),
            "sku": arguments.get("sku"),
        }
    else:
        body = {
            "id": arguments.get("id"),
            "price_version": arguments.get("price_version"),
            "version": arguments.get("version"),
        }
    return json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")


def run_cell(
    task: dict[str, Any],
    arm: str,
    fault: str,
    root: Path,
    *,
    old_payload: bytes,
    next_payload: bytes | None,
    runtime: str = "epochgate",
    model: str | None = None,
    tool_events_extra: list[dict[str, Any]] | None = None,
    tokens: tuple[int, int] = (0, 0),
    end_reason: str = "scripted",
    authority_kind: str = "supersede",
) -> dict[str, Any]:
    started = time.perf_counter()
    run_id = f"{task['task_id']}-{arm}-{fault}-{runtime}"
    ctx = open_run(root, run_id, task["relative"])
    attempt_id = f"{run_id}-old"
    for name in ("read_primary", "read_dependency"):
        observe_tool_call(ctx, name, attempt_id, {})
    world = inject_world_change(ctx, task["initial"], task["event"])
    mode = notice_mode(task, fault)
    if mode != "delayed":
        deliver_notice(ctx, mode, kind=authority_kind)
    if tool_events_extra:
        ctx.tool_events.extend(tool_events_extra)
    decision = commit_effect(
        ctx,
        arm,
        effect_key=f"{run_id}-old",
        attempt_id=attempt_id,
        payload=old_payload,
        interrupt=fault == "S3",
    )
    recovered = None
    if fault == "S3" and arm == "B3" and decision.get("claim") == "claimed":
        resumed = resume_execution(ctx)
        recovered = resumed.get("status") in {"completed", "accepted", "replayed"}
        decision = {**decision, "applied": bool(recovered), "status": resumed.get("status"), "recovery": resumed.get("status")}
    elif fault == "S3" and arm != "B3":
        recovered = False
    old_accepted = bool(decision.get("applied"))
    successor = False
    if (not old_accepted) and task.get("successor_applicable", True) and next_payload is not None and fault != "S3" and authority_kind != "revoke":
        state = ctx.ledger.get_run(run_id)
        if state.eligible:
            ctx.frozen_token = state.fencing_token
            ctx.notice_mode = "missing"
            ctx.claim = None
            follow = commit_effect(
                ctx,
                arm,
                effect_key=f"{run_id}-next",
                attempt_id=f"{run_id}-next",
                payload=next_payload,
                interrupt=False,
            )
            successor = bool(follow.get("applied"))
    oracle = oracle_from_artifacts(
        task,
        world,
        ctx.receiver,
        old_accepted=old_accepted,
        old_digest=sha256_bytes(old_payload) if old_accepted else None,
        token_was_stale=bool(decision.get("token_was_stale")),
        successor=successor,
        interrupted_recovered=recovered,
    )
    manifest = {key: task[key] for key in ("task_id", "family", "form", "event", "constraints", "s2_mode", "phase_pool")}
    record = {
        "task_id": task["task_id"],
        "family": task["family"],
        "form": task["form"],
        "run_id": run_id,
        "attempt_id": attempt_id,
        "arm": arm,
        "runtime": runtime,
        "fault": fault,
        "git_sha": git_sha(),
        "protocol_sha256": protocol_sha(),
        "task_manifest_sha256": sha256_text(json.dumps(manifest, sort_keys=True)),
        "image_digest": "not_applicable",
        "model": model,
        "end_reason": end_reason,
        "input_tokens": tokens[0],
        "output_tokens": tokens[1],
        "tool_calls": len(ctx.tool_events),
        "elapsed_s": time.perf_counter() - started,
        "blocked": False,
        "error_type": None,
        "timeout": False,
        "environment_blocked": False,
        "oracle": oracle,
        **oracle,
    }
    evidence = collect_evidence(ctx, record)
    ctx.close()
    return evidence


def _post(body: dict[str, Any]) -> dict[str, Any]:
    load_env()
    key = api_key()
    if not key:
        return {"ok": False, "error": "missing_key"}
    base = os.environ.get("EPOCHGATE_BASE_URL", "https://api.deepseek.com").rstrip("/")
    url = base + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        if key in raw:
            raw = raw.replace(key, "[redacted]")
        return {"ok": False, "error": f"http_{exc.code}: {raw[:300]}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": type(exc.reason).__name__}
    return {"ok": True, "payload": payload}


TOOLS = [
    {"type": "function", "function": {"name": name, "description": text, "parameters": {"type": "object", "properties": props, "additionalProperties": False}}}
    for name, text, props in (
        ("read_order", "读取订单", {}),
        ("read_inventory", "读取该订单商品的库存", {"sku": {"type": "string"}}),
        ("read_permission", "读取客户权限", {"customer": {"type": "string"}}),
        ("read_price", "读取价格版本", {}),
        ("read_config", "读取配置版本", {}),
        ("submit_effect", "提交持久效果", {
            "sku": {"type": "string"},
            "qty": {"type": "integer"},
            "customer": {"type": "string"},
            "price_version": {"type": "integer"},
            "config_version": {"type": "integer"},
            "id": {"type": "string"},
            "version": {"type": "integer"},
        }),
    )
]


def model_proposals(task: dict[str, Any], fault: str) -> dict[str, Any]:
    """模型自己选择工具并构造载荷。注入发生在第二次读取之后。"""
    from m4_tasks import PROMPT

    load_env()
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
    world = json.loads(json.dumps(task["initial"]))
    injected = False
    reads = 0
    notice = None
    messages: list[dict[str, Any]] = [{"role": "user", "content": PROMPT + "\n任务编号 " + task["task_id"]}]
    input_tokens = 0
    output_tokens = 0
    events: list[dict[str, Any]] = []
    proposals: list[bytes] = []

    def tool_result(name: str, arguments: dict[str, Any]) -> str:
        nonlocal world, injected, reads, notice
        events.append({"tool": name, "arguments": arguments})
        if name == "submit_effect":
            if reads < 2:
                return json.dumps({"error": "need_two_reads"}, ensure_ascii=False)
            proposals.append(_canonical(task, arguments))
            return json.dumps({"status": "proposed"}, ensure_ascii=False)
        reads += 1
        snapshot = json.loads(json.dumps(world))
        if reads >= 2 and not injected:
            injected = True
            for key, value in task["event"]["set"].items():
                cursor = world
                parts = key.split(".")
                for part in parts[:-1]:
                    cursor = cursor[part]
                cursor[parts[-1]] = value
            if fault == "S1":
                notice = "通知已送达：权威版本已更新。请先提交你在通知前已经形成的效果。"
        view = {
            "read_order": snapshot["order"],
            "read_inventory": {"sku": arguments.get("sku") or snapshot["order"]["sku"], "qty": snapshot["inventory"].get(arguments.get("sku") or snapshot["order"]["sku"])},
            "read_permission": snapshot["permission"],
            "read_price": snapshot["price"],
            "read_config": snapshot["config"],
        }[name]
        return json.dumps({
            "observation": view,
            "notice": notice,
            "instruction": "两次读取已完成，请立刻调用 submit_effect，载荷只使用这次返回之前的观察。" if reads >= 2 else "继续读取一个依赖项。",
        }, ensure_ascii=False)

    for _ in range(8):
        if proposals:
            break
        body = {"model": model, "messages": messages, "tools": TOOLS, "temperature": 0, "max_tokens": 1500}
        result = _post(body)
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error"), "events": events, "tokens": (input_tokens, output_tokens), "model": model}
        payload = result["payload"]
        usage = payload.get("usage") or {}
        input_tokens += int(usage.get("prompt_tokens") or 0)
        output_tokens += int(usage.get("completion_tokens") or 0)
        message = payload["choices"][0]["message"]
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            break
        for call in calls:
            name = call["function"]["name"]
            raw = call["function"].get("arguments") or "{}"
            try:
                arguments = json.loads(raw)
            except json.JSONDecodeError:
                arguments = {}
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": tool_result(name, arguments),
            })
    if not proposals:
        return {"ok": False, "error": "model_no_effect", "events": events, "tokens": (input_tokens, output_tokens), "model": model}
    return {
        "ok": True,
        "old": proposals[0],
        "events": events,
        "tokens": (input_tokens, output_tokens),
        "model": model,
    }


def successor_proposal(task: dict[str, Any], world: dict[str, Any]) -> dict[str, Any]:
    load_env()
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
    messages = [{
        "role": "user",
        "content": "旧尝试已被拒绝。下面是当前业务状态。请调用 submit_effect 一次，提交与当前约束一致的后继效果。\n" + json.dumps(world, ensure_ascii=False),
    }]
    body = {"model": model, "messages": messages, "tools": TOOLS, "temperature": 0, "max_tokens": 1500}
    result = _post(body)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error"), "model": model, "tokens": (0, 0)}
    payload = result["payload"]
    usage = payload.get("usage") or {}
    message = payload["choices"][0]["message"]
    calls = message.get("tool_calls") or []
    for call in calls:
        if call["function"]["name"] != "submit_effect":
            continue
        try:
            arguments = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        return {
            "ok": True,
            "payload": _canonical(task, arguments),
            "model": model,
            "tokens": (int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)),
        }
    return {"ok": False, "error": "model_no_successor", "model": model, "tokens": (int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))}


def run_phase(phase: str, out: Path, *, live: bool) -> dict[str, Any]:
    from m4_tasks import ARMS, FAULTS, legal_payload, observed_payload, pool

    out.mkdir(parents=True, exist_ok=True)
    rows = []
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    for task in pool(phase):
        for fault in FAULTS:
            key = (task["task_id"], fault)
            if live:
                if key not in cache:
                    cache[key] = model_proposals(task, fault)
                proposal = cache[key]
                if not proposal.get("ok"):
                    for arm in ARMS:
                        rows.append({
                            "task_id": task["task_id"], "family": task["family"], "arm": arm, "fault": fault,
                            "runtime": "epochgate", "blocked": True, "error_type": proposal.get("error"),
                            "end_reason": "model_unavailable", "task_terminal_match": False,
                            "protocol_sha256": protocol_sha(), "model": proposal.get("model"),
                        })
                    continue
                old_payload = proposal["old"]
                world = json.loads(json.dumps(task["initial"]))
                for path, value in task["event"]["set"].items():
                    cursor = world
                    parts = path.split(".")
                    for part in parts[:-1]:
                        cursor = cursor[part]
                    cursor[parts[-1]] = value
                follow = successor_proposal(task, world)
                next_payload = follow.get("payload") if follow.get("ok") else None
                tokens = proposal["tokens"]
                extra_tokens = follow.get("tokens") or (0, 0)
                tokens = (tokens[0] + extra_tokens[0], tokens[1] + extra_tokens[1])
                events = proposal["events"]
                model = proposal["model"]
                end_reason = "model"
            else:
                old_payload = observed_payload(task)
                world = json.loads(json.dumps(task["initial"]))
                for path, value in task["event"]["set"].items():
                    cursor = world
                    parts = path.split(".")
                    for part in parts[:-1]:
                        cursor = cursor[part]
                    cursor[parts[-1]] = value
                text = legal_payload(task, world)
                next_payload = text.encode("utf-8") if text else None
                tokens = (0, 0)
                events = []
                model = None
                end_reason = "scripted"
            for arm in ARMS:
                cell_root = out / f"{task['task_id']}__{arm}__{fault}"
                if (cell_root / "evidence.json").is_file():
                    rows.append(json.loads((cell_root / "evidence.json").read_text(encoding="utf-8")))
                    continue
                evidence = run_cell(
                    task, arm, fault, cell_root,
                    old_payload=old_payload, next_payload=next_payload, model=model,
                    tool_events_extra=events, tokens=tokens, end_reason=end_reason,
                )
                (cell_root / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
                rows.append(evidence)
    summary = {"phase": phase, "live": live, "n": len(rows), "blocked": sum(1 for row in rows if row.get("blocked")), "rows": rows}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("pilot", "confirm", "m5"), required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    summary = run_phase(args.phase, args.out, live=args.live)
    print(json.dumps({"phase": summary["phase"], "n": summary["n"], "blocked": summary["blocked"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
