"""真实模型上的静态预约和流式 A/B。不使用假后端。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from epochgate.http_backend import HttpToolBackend
from epochgate.http_streaming import HttpStreamingBackend
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.models import Task, TaskStatus
from epochgate.runtime import Runtime
from epochgate.scheduler import NearAdmissionScheduler

RESERVE_SYSTEM = (
    "You reserve inventory only by calling the reserve tool. "
    "The arguments must be a JSON object whose qty field is an integer. "
    "When the goal is to reserve one unit, qty is 1. "
    "Do not answer with markdown or prose."
)
RESERVE_TOOL_CHOICE: dict[str, Any] = {"type": "function", "function": {"name": "reserve"}}
STREAM_SYSTEM = (
    "Call the reserve tool with qty set to the integer 1 only after the explanation. "
    "The arguments must be a JSON object and qty must be an integer."
)
STREAM_GOAL = (
    "reserve one unit. Before the tool call, write at least 180 words explaining the reservation, "
    "one sentence at a time."
)


async def run_static_once(
    *,
    base_url: str,
    api_key: str,
    model: str,
    run_index: int,
) -> dict[str, Any]:
    """跑一次无外生变化的真实预约。

    输入：模型地址、密钥、模型名，以及这次在序列中的序号。
    输出：一行结果。成功指解析出 reserve、网关提交、库存从 3 变为 2。
    副作用：向模型发非流式请求。解析失败时同一请求最多再试一次。
    失败：运行时超出步数时把错误写进行，不把密钥写进行。
    """
    env = InventoryEnvironment(stock=3, schedule=[], seed=run_index, pace="step")
    backend = HttpToolBackend(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system_prompt=RESERVE_SYSTEM,
        tool_choice=RESERVE_TOOL_CHOICE,
        parse_retries=1,
    )
    ids = IdFactory(run_index)
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=f"static-{run_index}",
        inline_dispatch=True,
        max_steps=32,
        seed=run_index,
    )
    now = time.monotonic()
    task = Task(
        task_id="reserve-one",
        arrival_mono=now,
        deadline_mono=now + 30.0,
        goal="reserve one unit",
    )
    try:
        result = await runtime.run_task(task)
        status = result.status
        error = ""
    except RuntimeError as exc:
        status = TaskStatus.FAILED
        error = str(exc)
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    return _static_row(events, run_index=run_index, status=status, stock=env.stock, error=error)


async def run_static_series(
    *,
    base_url: str,
    api_key: str,
    model: str,
    runs: int,
    out_dir: str | Path,
) -> list[dict[str, Any]]:
    """连续跑多次静态预约，并写出原始行。

    输入：模型配置、次数和目录。
    输出：每一行的结果。六次都成功时额外写 ``baseline.jsonl``。
    副作用：写 ``static_runs.jsonl``。每次都打真实 API。
    失败：次数小于 1 时抛出 ``ValueError``。单次失败仍继续后面的次数。
    """
    if runs < 1:
        raise ValueError("runs must be >= 1")
    rows = []
    for index in range(1, runs + 1):
        rows.append(
            await run_static_once(base_url=base_url, api_key=api_key, model=model, run_index=index)
        )
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    _write_jsonl(directory / "static_runs.jsonl", rows)
    if runs == 6 and all(row["success"] for row in rows):
        _write_jsonl(directory / "baseline.jsonl", rows)
    return rows


async def run_stream_arm(
    *,
    base_url: str,
    api_key: str,
    model: str,
    seed: int,
    arm: str,
    first_delta_delay_s: float = 0.3,
) -> dict[str, Any]:
    """跑真实流式的一臂。

    输入：模型配置、种子、``A`` 或 ``B``，以及首个增量后的延迟秒数。
    输出：一行配对记录。本地词元来自客户端收到的增量。
    副作用：延迟到达后把库存改为 0。B 臂关闭响应，A 臂不关。
    失败：未知臂抛出 ``ValueError``。网络错误写进行，不写密钥。
    """
    if arm not in {"A", "B"}:
        raise ValueError("arm must be A or B")
    ids = IdFactory(seed)
    env = InventoryEnvironment(stock=3, schedule=[], seed=seed, pace="step", ids=ids)
    fired = {"done": False}

    def drop_stock() -> None:
        if not fired["done"]:
            fired["done"] = True
            env.apply_exogenous(-env.stock)

    backend = HttpStreamingBackend(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system_prompt=STREAM_SYSTEM,
        tool_choice="auto",
        stop_on_cancel=arm == "B",
        on_first_delta=drop_stock,
        first_delta_delay_s=first_delta_delay_s,
        ids=ids,
        timeout_s=45.0,
    )
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=f"stream-{seed}-{arm}",
        max_steps=4000,
        seed=seed,
    )
    now = time.monotonic()
    task = Task(
        task_id="reserve-one",
        arrival_mono=now,
        deadline_mono=now + 30.0,
        goal=STREAM_GOAL,
    )
    try:
        result = await runtime.run_task(task)
        status = result.status
        error = ""
    except RuntimeError as exc:
        status = TaskStatus.FAILED
        error = str(exc)
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    row = _stream_row(events, seed=seed, arm=arm, status=status, error=error)
    row["final_stock"] = env.stock
    row["stock_change_fired"] = fired["done"]
    row["first_delta_delay_s"] = first_delta_delay_s
    return row


async def run_stream_pairs(
    *,
    base_url: str,
    api_key: str,
    model: str,
    pairs: int,
    out_path: str | Path,
    first_delta_delay_s: float = 0.3,
) -> list[dict[str, Any]]:
    """按种子 1..pairs 跑 A 臂和 B 臂。

    输入：模型配置、对数、输出文件、首个增量后的延迟。
    输出：每个种子两行。
    副作用：写 JSONL。每臂各打一次真实流式 API。
    失败：对数小于 1 时抛出 ``ValueError``。
    """
    if pairs < 1:
        raise ValueError("pairs must be >= 1")
    rows: list[dict[str, Any]] = []
    for seed in range(1, pairs + 1):
        for arm in ("A", "B"):
            rows.append(
                await run_stream_arm(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    seed=seed,
                    arm=arm,
                    first_delta_delay_s=first_delta_delay_s,
                )
            )
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(path, rows)
    return rows


def _static_row(
    events: list,
    *,
    run_index: int,
    status: TaskStatus,
    stock: int,
    error: str,
) -> dict[str, Any]:
    committed = [event for event in events if event.event_type == "action_committed"]
    rejected = [event for event in events if event.event_type == "action_rejected"]
    failed = [event for event in events if event.event_type == "backend_failed"]
    retry_count = 0
    for event in events:
        raw = (event.usage or {}).get("retry_count")
        if isinstance(raw, int):
            retry_count = max(retry_count, raw)
    parameters = {}
    if committed:
        parameters = dict((committed[-1].usage or {}).get("action_parameters") or {})
    elif rejected:
        parameters = dict((rejected[-1].usage or {}).get("action_parameters") or {})
    tool_name = ""
    if committed or rejected:
        tool_name = "reserve" if "qty" in parameters or parameters else ""
        if parameters:
            tool_name = "reserve"
    reasons = [event.reason or "" for event in rejected]
    fail_reasons = [event.reason or "" for event in failed]
    if error:
        failure_class = "runtime_error"
    elif committed and stock == 2 and parameters.get("qty") == 1:
        failure_class = ""
    elif any("parse_error" in reason or "no tool_calls" in reason for reason in fail_reasons):
        failure_class = "model_no_tool"
    elif rejected:
        failure_class = "gateway_reject"
    elif failed:
        failure_class = "model_no_tool" if any("parse_error" in reason for reason in fail_reasons) else "http_error"
    else:
        failure_class = "model_no_tool"
    success = failure_class == "" and status == TaskStatus.SUCCEEDED
    return {
        "run_index": run_index,
        "backend": "http_tool",
        "task_status": status.value,
        "success": success,
        "tool_name": tool_name if success or parameters else "",
        "arguments": parameters,
        "committed": bool(committed),
        "stock_before": 3,
        "stock_after": stock,
        "retry_count": retry_count,
        "failure_class": failure_class,
        "reject_reasons": reasons,
        "backend_reasons": fail_reasons,
        "error": error,
    }


def _stream_row(
    events: list,
    *,
    seed: int,
    arm: str,
    status: TaskStatus,
    error: str,
) -> dict[str, Any]:
    revoked = next((event for event in events if event.event_type == "cancel_requested"), None)
    if revoked is None:
        attempt_id = ""
        before = _local_count(events, attempt_id="", before_mono=None)
        extra = 0
        revoke_mono = None
    else:
        attempt_id = revoked.identity.get("attempt_id", "")
        revoke_mono = float(revoked.mono_time)
        own = _delta_events(events, attempt_id)
        before = sum(1 for event in own if float(event.mono_time) < revoke_mono)
        extra = sum(1 for event in own if float(event.mono_time) >= revoke_mono)
    replan = 0 if revoked is None else sum(
        1
        for event in events
        if event.event_type in {"token_delta", "tool_call_delta"}
        and event.identity.get("attempt_id") != attempt_id
    )
    timed = _delta_events(events, attempt_id) if attempt_id else [
        event
        for event in events
        if event.event_type in {"token_delta", "tool_call_delta"}
    ]
    span = None
    if len(timed) >= 2:
        span = float(timed[-1].mono_time) - float(timed[0].mono_time)
    reclaimed = next((event for event in events if event.event_type == "resource_reclaimed"), None)
    latency = None if reclaimed is None or revoke_mono is None else float(reclaimed.mono_time) - revoke_mono
    committed = any(event.event_type == "action_committed" for event in events)
    old_submitted = bool(attempt_id) and any(
        event.event_type in {"action_committed", "action_rejected"}
        and event.identity.get("attempt_id") == attempt_id
        for event in events
    )
    provider = None
    for event in events:
        raw = (event.usage or {}).get("provider_usage")
        if isinstance(raw, dict) and raw:
            provider = raw
    sources = sorted(
        {
            str((event.usage or {}).get("usage_source"))
            for event in events
            if event.event_type in {"token_delta", "tool_call_delta"}
        }
    )
    return {
        "seed": seed,
        "arm": arm,
        "backend": "http_streaming",
        "exploratory": True,
        "measured": "client_local_tokens",
        "server_compute_after_cancel": "unknown",
        "task_status": status.value,
        "success": status == TaskStatus.SUCCEEDED and committed,
        "revoke_happened": revoked is not None,
        "local_tokens_at_revoke": before,
        "local_tokens_before_revoke": before,
        "extra_tokens_until_local_stop": extra,
        "full_generated_tokens": before + extra,
        "client_stream_span_s": span,
        "replan_tokens": replan,
        "provider_usage": provider,
        "local_stop_latency_s": latency,
        "local_loop_stopped": reclaimed is not None,
        "committed": committed,
        "old_result_submitted": old_submitted,
        "unknown_compute": True,
        "usage_sources_on_deltas": sources,
        "error": error,
    }


def _delta_events(events: list, attempt_id: str) -> list:
    return [
        event
        for event in events
        if event.event_type in {"token_delta", "tool_call_delta"}
        and event.identity.get("attempt_id") == attempt_id
    ]


def _local_count(events: list, *, attempt_id: str, before_mono: float | None) -> int:
    chosen = [
        event
        for event in events
        if event.event_type in {"token_delta", "tool_call_delta"}
        and (not attempt_id or event.identity.get("attempt_id") == attempt_id)
    ]
    if before_mono is None:
        return len(chosen)
    return sum(1 for event in chosen if float(event.mono_time) < before_mono)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [json.dumps(row, ensure_ascii=False, default=str) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
