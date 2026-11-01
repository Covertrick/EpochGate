"""假流式后端上的配对：同一触发点，只拒绝或立刻停止。"""

from __future__ import annotations

import time
from typing import Any

from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.models import Task, TaskStatus
from epochgate.runtime import Runtime
from epochgate.scheduler import NearAdmissionScheduler
from epochgate.streaming_backend import StreamingFakeBackend


async def run_paired_case(*, seed: int, arm: str) -> dict[str, Any]:
    """跑一颗开发种子的一臂。

    输入：种子 1、2 或 3，以及 ``A``（取消后继续生成）或 ``B``（取消后停止循环）。
    输出：一行配对记录。供应商用量为空，词元都是本地计数。
    副作用：只改这一次运行自己的库存。
    失败：未知臂抛出 ``ValueError``。两臂脚本不同则不是这一轮要的配对。
    """
    if arm not in {"A", "B"}:
        raise ValueError("arm must be A or B")
    if seed not in {1, 2, 3}:
        raise ValueError("paired probe only uses development seeds 1, 2, 3")
    ids = IdFactory(seed)
    env = InventoryEnvironment(stock=3, schedule=[], seed=seed, pace="step", ids=ids)
    fired = {"done": False}

    def drop_stock() -> None:
        if not fired["done"]:
            fired["done"] = True
            env.apply_exogenous(-3)

    backend = StreamingFakeBackend(
        token_count=8,
        trigger_at=5,
        on_trigger=drop_stock,
        stop_on_cancel=arm == "B",
        ids=ids,
    )
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=f"paired-seed-{seed}-{arm}",
        max_steps=400,
        seed=seed,
    )
    now = time.monotonic()
    task = Task(
        task_id="reserve-one",
        arrival_mono=now,
        deadline_mono=now + 30.0,
        goal="reserve one unit",
    )
    result = await runtime.run_task(task)
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    row = _summarize(events, seed=seed, arm=arm, status=result.status)
    row["final_stock"] = env.stock
    return row


def _summarize(events: list, *, seed: int, arm: str, status: TaskStatus) -> dict[str, Any]:
    revoked = next(event for event in events if event.event_type == "cancel_requested")
    attempt_id = revoked.identity["attempt_id"]
    revoke_mono = float(revoked.mono_time)
    own = [
        event
        for event in events
        if event.event_type == "token_delta" and event.identity.get("attempt_id") == attempt_id
    ]
    before = sum(1 for event in own if float(event.mono_time) < revoke_mono)
    extra = sum(1 for event in own if float(event.mono_time) >= revoke_mono)
    replan = sum(
        1
        for event in events
        if event.event_type == "token_delta" and event.identity.get("attempt_id") != attempt_id
    )
    reclaimed = next((event for event in events if event.event_type == "resource_reclaimed"), None)
    latency = None if reclaimed is None else float(reclaimed.mono_time) - revoke_mono
    committed = any(event.event_type == "action_committed" for event in events)
    old_submitted = any(
        event.event_type in {"action_committed", "action_rejected"}
        and event.identity.get("attempt_id") == attempt_id
        for event in events
    )
    local_sources = {
        str((event.usage or {}).get("usage_source"))
        for event in events
        if event.event_type in {"token_delta", "tool_call_delta"}
    }
    return {
        "seed": seed,
        "arm": arm,
        "backend": "streaming_fake",
        "exploratory": True,
        "length_aligned": before == 5 and (before + extra) == (5 if arm == "B" else 8),
        "task_status": status.value,
        "success": status == TaskStatus.SUCCEEDED and committed,
        "revoke_mono": revoke_mono,
        "local_tokens_before_revoke": before,
        "extra_tokens_until_local_stop": extra,
        "full_generated_tokens": before + extra,
        "replan_tokens": replan,
        "provider_usage": None,
        "local_stop_latency_s": latency,
        "local_loop_stopped": reclaimed is not None,
        "committed": committed,
        "old_result_submitted": old_submitted,
        "unknown_compute": False,
        "usage_sources_on_deltas": sorted(local_sources),
    }
