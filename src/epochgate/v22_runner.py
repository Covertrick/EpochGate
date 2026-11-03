"""v1.4–v2.0 对照运行器。A 继续读，B 见信号就断本地流，C 只在前提失败时撤销并断流。"""

from __future__ import annotations

import time
from typing import Any

from epochgate.http_streaming import HttpStreamingBackend
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.matrix_run import TIMEOUT_S, goal_for
from epochgate.models import Task
from epochgate.runtime import Runtime
from epochgate.scenario_driver import ScenarioDriver, make_scenario_spec
from epochgate.scheduler import FifoAdmissionScheduler
from epochgate.stage_e_runner import DEADLINE_S, STREAM_SYSTEM, extract_stage_e_row

ARM_POLICY = {
    "A": ("premise", False),
    "B": ("any_signal", True),
    "C": ("premise", True),
}


async def run_v22_case(
    *,
    api_key: str,
    base_url: str,
    model: str,
    seed: int,
    arm: str,
    task_kind: str,
    cell_id: str,
    pair_id: str,
    batch_id: str,
    length_tier: str,
    timing_tier: str,
    target_words: int,
    t_change_s: float,
    t_restore_s: float | None = None,
    initial_stock: int = 1,
    scheduler_order: str = "fifo",
    task_plan: list[dict[str, Any]] | None = None,
    code_version: str = "v22",
    experiment_block: str = "v22",
    goal_text: str | None = None,
    system_prompt: str | None = None,
    workflow_class: str = "",
) -> list[dict[str, Any]]:
    """跑一臂。同一种子的 A/B/C 使用各自的环境副本和同一套时间表。

    输入：臂、任务种类、长度、变化时刻。
    输出：每个任务一行。
    副作用：可能调用 DeepSeek。失败记在行的 error 字段，不把传输失败写成取消成功。
    """
    if arm not in ARM_POLICY:
        raise ValueError("arm must be A, B, or C")
    signal_policy, stop_on_cancel = ARM_POLICY[arm]
    ids = IdFactory(seed)
    spec = make_scenario_spec(
        scenario_id=cell_id,
        task_kind=task_kind,
        t_change_s=t_change_s,
        t_restore_s=t_restore_s,
        initial_stock=initial_stock,
    )
    env = InventoryEnvironment(stock=spec.initial_stock, schedule=[], seed=seed, pace="step", ids=ids)
    driver = ScenarioDriver(spec=spec, env=env)
    backend = HttpStreamingBackend(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system_prompt=system_prompt or STREAM_SYSTEM,
        tool_choice="auto",
        stop_on_cancel=stop_on_cancel,
        on_first_delta=driver.start_anchor if spec.cues else None,
        first_delta_delay_s=0.0,
        ids=ids,
        timeout_s=TIMEOUT_S,
    )
    scheduler = FifoAdmissionScheduler(replan_to_tail=True, ids=ids, order=scheduler_order)
    run_id = f"{experiment_block}-{cell_id}-s{seed}-{arm}-{scheduler_order}"
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=scheduler,
        ids=ids,
        run_id=run_id,
        max_steps=12000,
        seed=seed,
        signal_policy=signal_policy,
        await_recovery_on_depletion=(task_kind == "recoverable"),
    )
    now = time.monotonic()
    if task_plan is None:
        tasks = [
            Task(
                task_id="t0",
                arrival_mono=now,
                deadline_mono=now + DEADLINE_S,
                goal=goal_text or goal_for(target_words),
                task_class=length_tier,
            )
        ]
        plan_meta = [{"task_id": "t0", "words": target_words, "length_tier": length_tier, "arrival_offset_s": 0.0}]
    else:
        tasks = []
        plan_meta = []
        for idx, item in enumerate(task_plan):
            tid = str(item.get("task_id", f"t{idx}"))
            words = int(item["words"])
            tier = str(item.get("length_tier", length_tier))
            offset = float(item.get("arrival_offset_s", 0.0))
            tasks.append(
                Task(
                    task_id=tid,
                    arrival_mono=now + offset,
                    deadline_mono=now + DEADLINE_S,
                    goal=goal_text or goal_for(words),
                    task_class=tier,
                )
            )
            plan_meta.append(
                {"task_id": tid, "words": words, "length_tier": tier, "arrival_offset_s": offset}
            )
    started = time.monotonic()
    error = ""
    try:
        await runtime.run(tasks)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        driver.cancel_pending()
    wall_s = time.monotonic() - started
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    rows: list[dict[str, Any]] = []
    for pos, (task, meta) in enumerate(zip(tasks, plan_meta)):
        status = runtime.registry.get_task(task.task_id).status.value
        row = extract_stage_e_row(
            events,
            pair_id=pair_id,
            run_id=run_id,
            seed=seed,
            arm=arm,
            arm_order="ABC",
            experiment_block=experiment_block,
            cell_id=cell_id,
            task_kind=task_kind,
            length_tier=meta["length_tier"],
            timing_tier=timing_tier,
            target_words=meta["words"],
            t_change_plan_s=t_change_s,
            t_restore_plan_s=t_restore_s,
            concurrency=len(tasks),
            task_id=task.task_id,
            task_status=status,
            driver=driver,
            final_stock=env.stock,
            initial_stock=spec.initial_stock,
            wall_s=wall_s,
            code_version=code_version,
            error=error,
        )
        types = [ev.event_type for ev in events if ev.identity.get("task_id") in {task.task_id, None, ""}]
        row.update(
            {
                "signal_policy": signal_policy,
                "scheduler_order": scheduler_order,
                "batch_id": batch_id,
                "queue_position": pos,
                "arrival_offset_s": meta["arrival_offset_s"],
                "provider_abort_not_issued": "provider_abort_not_issued" in types,
                "local_read_stop_requested": "local_read_stop_requested" in types,
                "slot_acquired": "slot_acquired" in types,
                "false_cancel": task_kind == "harmless" and "eligibility_revoked" in types,
                "transport_error": error,
                "workflow_class": workflow_class,
                "read_tool_seen": "observations_refreshed" in types,
                "duplicate_effect": int(row.get("valid_commits") or 0) > 1,
            }
        )
        rows.append(row)
    return rows
