"""阶段一矩阵的一次臂。本地增量按事件计，不按 tokenizer。"""

from __future__ import annotations

import time
from typing import Any

from epochgate.http_streaming import HttpStreamingBackend
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.models import Task, TaskStatus
from epochgate.runtime import Runtime
from epochgate.scheduler import NearAdmissionScheduler

PROMPT_VERSION = "phase1-words-v1"
STREAM_SYSTEM = (
    "Call the reserve tool with qty set to the integer 1 only after the explanation. "
    "The arguments must be a JSON object and qty must be an integer."
)
DEADLINE_S = 120.0
TIMEOUT_S = 180.0


def goal_for(words: int) -> str:
    """返回要求先写够指定词数再调用工具的任务文本。

    输入：目标词数。
    输出：A/B 共用的 goal 字符串。
    副作用：无。
    失败：词数小于 1 时抛出 ``ValueError``。
    """
    if words < 1:
        raise ValueError("words must be >= 1")
    return (
        f"reserve one unit. Before the tool call, write at least {words} words "
        "explaining the reservation, one sentence at a time."
    )


async def run_matrix_arm(
    *,
    base_url: str,
    api_key: str,
    model: str,
    seed: int,
    arm: str,
    words: int,
    delay_s: float,
    concurrency: int,
    code_version: str,
) -> dict[str, Any]:
    """跑矩阵里的一臂。

    输入：模型配置、种子、臂、目标词数、首个增量后的延迟、并发任务数、代码版本。
    输出：一行记录。并发大于 1 时，撤销目标是第一个产出增量的任务。
    副作用：可能把库存改为 0。B 臂关闭响应，A 臂不关。
    失败：臂名或并发数非法时抛出 ``ValueError``。运行错误写进行。
    """
    if arm not in {"A", "B"}:
        raise ValueError("arm must be A or B")
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    ids = IdFactory(seed)
    env = InventoryEnvironment(stock=3, schedule=[], seed=seed, pace="step", ids=ids)
    stamps = {"scheduled": None, "applied": None}
    fired = {"done": False}

    def drop_stock() -> None:
        if fired["done"]:
            return
        fired["done"] = True
        stamps["applied"] = time.monotonic()
        env.apply_exogenous(-env.stock)

    backend = HttpStreamingBackend(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system_prompt=STREAM_SYSTEM,
        tool_choice="auto",
        stop_on_cancel=arm == "B",
        on_first_delta=None if delay_s < 0 else drop_stock,
        first_delta_delay_s=0.0 if delay_s < 0 else delay_s,
        ids=ids,
        timeout_s=TIMEOUT_S,
    )
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=f"matrix-{seed}-{arm}-{words}-{concurrency}",
        max_steps=8000,
        seed=seed,
    )
    now = time.monotonic()
    goal = goal_for(words)
    tasks = [
        Task(
            task_id=f"t{index}",
            arrival_mono=now,
            deadline_mono=now + DEADLINE_S,
            goal=goal,
        )
        for index in range(concurrency)
    ]
    started = time.monotonic()
    error = ""
    try:
        await runtime.run(tasks)
    except RuntimeError as exc:
        error = str(exc)
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    row = _arm_row(
        events,
        seed=seed,
        arm=arm,
        words=words,
        delay_s=delay_s,
        concurrency=concurrency,
        stock_change_fired=fired["done"],
        scheduled_at=stamps["scheduled"],
        applied_at=stamps["applied"],
        error=error,
        final_stock=env.stock,
        wall_s=time.monotonic() - started,
        code_version=code_version,
        valid_commits=sum(1 for task in tasks if runtime.registry.get_task(task.task_id).status == TaskStatus.SUCCEEDED),
    )
    if delay_s >= 0 and row["first_delta_at"] is not None and stamps["applied"] is not None:
        row["stock_change_scheduled_at"] = row["first_delta_at"]
    return row


def _arm_row(
    events: list,
    *,
    seed: int,
    arm: str,
    words: int,
    delay_s: float,
    concurrency: int,
    stock_change_fired: bool,
    scheduled_at: float | None,
    applied_at: float | None,
    error: str,
    final_stock: int,
    wall_s: float,
    code_version: str,
    valid_commits: int,
) -> dict[str, Any]:
    target = _target_attempt(events)
    revoked = _first(events, "cancel_requested", target)
    first_delta = _first_delta(events, target)
    tool_at = _first(events, "tool_call_delta", target)
    content_before_tool = _text_before(events, target, tool_at.mono_time if tool_at else None, "token_delta")
    words_before_tool = len(content_before_tool.split())
    words_before_revoke = 0
    if revoked is not None:
        words_before_revoke = len(
            _text_before(events, target, revoked.mono_time, "token_delta").split()
        )
    pre = _count_before(events, target, None if revoked is None else revoked.mono_time)
    post = 0 if revoked is None else _count_from(events, target, revoked.mono_time)
    replan = _count_other(events, target)
    stopped = _first(events, "local_read_stopped", target)
    requested = _first(events, "cancel_requested", target)
    acknowledged = _first(events, "cancel_acknowledged", target)
    terminal = events[-1] if events else None
    enqueued = _first_any(events, "runtime_start")
    request_started = _first(events, "backend_request", target)
    status = _pair_status(
        events,
        error=error,
        fired=stock_change_fired,
        revoked=revoked,
        tool_at=tool_at,
    )
    position = None
    if tool_at is not None and revoked is not None and words_before_tool > 0:
        position = words_before_revoke / words_before_tool
    failures = [event.reason or "" for event in events if event.event_type == "backend_failed"]
    detection = None
    if revoked is not None and applied_at is not None:
        detection = float(revoked.mono_time) - applied_at
    stop_latency = None
    if stopped is not None and requested is not None:
        stop_latency = float(stopped.mono_time) - float(requested.mono_time)
    queue_waits = _queue_waits(events, target)
    return {
        "prompt_version": PROMPT_VERSION,
        "code_version": code_version,
        "cell_id": _cell_id(words, delay_s, concurrency),
        "seed": seed,
        "arm": arm,
        "target_words": words,
        "trigger_delay_s": delay_s,
        "concurrency": concurrency,
        "stock_change_fired": stock_change_fired,
        "pair_status": status,
        "actual_words_before_tool": words_before_tool,
        "pre_revoke_local_deltas": pre,
        "post_revoke_local_deltas": post,
        "revocation_position_ratio": position,
        "revoked_attempt_local_deltas": pre + post,
        "replan_local_deltas": replan,
        "task_total_local_deltas": pre + post + replan,
        "valid_commit": valid_commits > 0,
        "valid_commits": valid_commits,
        "end_to_end_latency_s": None
        if enqueued is None or terminal is None
        else float(terminal.mono_time) - float(enqueued.mono_time),
        "wall_clock_s": wall_s,
        "final_stock": final_stock,
        "detection_latency_s": detection,
        "local_stop_latency_s": stop_latency,
        "task_enqueued_at": None if enqueued is None else float(enqueued.mono_time),
        "request_started_at": None if request_started is None else float(request_started.mono_time),
        "first_delta_at": None if first_delta is None else float(first_delta.mono_time),
        "stock_change_scheduled_at": scheduled_at,
        "stock_change_applied_at": applied_at,
        "eligibility_revoked_at": None if revoked is None else float(revoked.mono_time),
        "cancel_requested_at": None if requested is None else float(requested.mono_time),
        "cancel_acknowledged_at": None if acknowledged is None else float(acknowledged.mono_time),
        "local_read_stopped_at": None if stopped is None else float(stopped.mono_time),
        "task_terminal_at": None if terminal is None else float(terminal.mono_time),
        "queue_wait_s": queue_waits,
        "target_attempt_id": target,
        "error": error,
        "tool_call_seen": tool_at is not None,
        "backend_failures": failures,
    }


def _cell_id(words: int, delay_s: float, concurrency: int) -> str:
    load = "" if concurrency == 1 else f"_x{concurrency}"
    return f"w{words}_d{delay_s}{load}"


def _target_attempt(events: list) -> str:
    for event in events:
        if event.event_type in {"token_delta", "tool_call_delta"}:
            return str(event.identity.get("attempt_id") or "")
    for event in events:
        if event.event_type == "backend_request":
            return str(event.identity.get("attempt_id") or "")
    return ""


def _first(events: list, event_type: str, attempt_id: str):
    for event in events:
        if event.event_type == event_type and (not attempt_id or event.identity.get("attempt_id") == attempt_id):
            return event
    return None


def _first_any(events: list, event_type: str):
    return next((event for event in events if event.event_type == event_type), None)


def _first_delta(events: list, attempt_id: str):
    for event in events:
        if event.event_type in {"token_delta", "tool_call_delta"} and event.identity.get("attempt_id") == attempt_id:
            return event
    return None


def _text_before(events: list, attempt_id: str, limit: float | None, event_type: str) -> str:
    parts = []
    for event in events:
        if event.event_type != event_type or event.identity.get("attempt_id") != attempt_id:
            continue
        if limit is not None and float(event.mono_time) >= limit:
            continue
        parts.append(event.reason or "")
    return "".join(parts)


def _count_before(events: list, attempt_id: str, limit: float | None) -> int:
    total = 0
    for event in events:
        if event.event_type not in {"token_delta", "tool_call_delta"}:
            continue
        if attempt_id and event.identity.get("attempt_id") != attempt_id:
            continue
        if limit is not None and float(event.mono_time) >= limit:
            continue
        total += 1
    return total


def _count_from(events: list, attempt_id: str, limit: float) -> int:
    return sum(
        1
        for event in events
        if event.event_type in {"token_delta", "tool_call_delta"}
        and event.identity.get("attempt_id") == attempt_id
        and float(event.mono_time) >= limit
    )


def _count_other(events: list, attempt_id: str) -> int:
    if not attempt_id:
        return 0
    return sum(
        1
        for event in events
        if event.event_type in {"token_delta", "tool_call_delta"}
        and event.identity.get("attempt_id") != attempt_id
    )


def _queue_waits(events: list, target: str) -> list[float]:
    enqueued = _first_any(events, "runtime_start")
    if enqueued is None:
        return []
    waits = []
    seen = set()
    for event in events:
        if event.event_type != "backend_request":
            continue
        attempt_id = event.identity.get("attempt_id")
        if not attempt_id or attempt_id in seen or attempt_id == target:
            continue
        seen.add(attempt_id)
        waits.append(float(event.mono_time) - float(enqueued.mono_time))
    return waits


def _pair_status(events: list, *, error: str, fired: bool, revoked, tool_at) -> str:
    blob = " ".join(event.event_type for event in events)
    if "cancel_failed" in blob:
        return "cancel_error"
    if error and "exceeded max_steps" in error:
        return "request_timeout"
    reasons = [event.reason or "" for event in events if event.event_type == "backend_failed"]
    if any(_transport_failure(reason) for reason in reasons):
        return "model_error"
    if not fired:
        return "trigger_not_fired"
    if tool_at is not None and revoked is not None and float(tool_at.mono_time) < float(revoked.mono_time):
        return "unexpected_tool_call"
    if revoked is None:
        return "trigger_not_fired"
    return "completed"


def _transport_failure(reason: str) -> bool:
    text = reason.lower()
    return text.startswith(("http_", "http ", "parse_error", "stream_closed")) or "http_error" in text
