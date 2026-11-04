"""v5.0 方向 A 编排：target / successor / bystander 角色与 G0、G2-local 合同。

DeepSeek 路径强制 backend_stop_status=unknown，禁止残余填 0。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Literal

from epochgate.http_streaming import HttpStreamingBackend
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.matrix_run import TIMEOUT_S, goal_for
from epochgate.models import Task
from epochgate.residual_backend import ResidualTokenBackend
from epochgate.runtime import Runtime
from epochgate.scenario_driver import ScenarioDriver, make_scenario_spec
from epochgate.scheduler import FifoAdmissionScheduler
from epochgate.stage_e_runner import DEADLINE_S, STREAM_SYSTEM
from epochgate.v50_metrics import extract_role_row, summarize_bystanders
from epochgate.vllm_backend import VllmObservingBackend

ArmName = Literal["G0", "G2-local", "G2-residual"]

ARM_CONTRACT: dict[ArmName, tuple[str, bool]] = {
    # signal_policy, stop_on_cancel
    "G0": ("premise", False),
    "G2-local": ("premise", True),
    "G2-residual": ("premise", True),
}


def arrival_offset(seed: int, role: str, index: int, *, span_s: float = 12.0) -> float:
    """政策无关的到达偏移；同 seed 跨臂共享。"""
    digest = hashlib.sha256(f"{seed}:{role}:{index}".encode()).hexdigest()
    u = int(digest[:8], 16) / 0xFFFFFFFF
    if role == "target":
        return 0.0
    return u * span_s


def build_task_plan(
    seed: int,
    *,
    bystanders_b: int = 8,
    target_words: int = 400,
    bystander_words: int = 200,
    successor_words: int = 200,
    span_s: float = 12.0,
) -> list[dict[str, Any]]:
    """1 target + B bystanders。successor 由 Runtime 重规划同一 task 产生，角色在行上标注。"""
    plan: list[dict[str, Any]] = [
        {
            "task_id": "target",
            "words": target_words,
            "length_tier": "long",
            "arrival_offset_s": arrival_offset(seed, "target", 0),
            "workflow_role": "target",
        }
    ]
    for i in range(bystanders_b):
        plan.append(
            {
                "task_id": f"bystander-{i}",
                "words": bystander_words,
                "length_tier": "mid",
                "arrival_offset_s": arrival_offset(seed, "bystander", i, span_s=span_s),
                "workflow_role": "bystander",
            }
        )
    plan.append(
        {
            "task_id": "successor-marker",
            "words": successor_words,
            "length_tier": "mid",
            "arrival_offset_s": arrival_offset(seed, "successor", 0),
            "workflow_role": "successor",
            "note": "optional co-scheduled successor workflow; not the same as target replan",
        }
    )
    return plan


async def run_v50_trace(
    *,
    seed: int,
    arm: ArmName,
    backend_kind: Literal["ds", "scripted", "vllm"],
    cell_id: str,
    pair_id: str,
    batch_id: str,
    load: str = "medium",
    t_change_s: float = 2.0,
    slots_s: int = 2,
    bystanders_b: int = 8,
    tau_s: float = 0.05,
    api_key: str = "",
    base_url: str = "https://api.deepseek.com/v1",
    model: str = "deepseek-chat",
    residual_hold_s: float = 0.0,
    scripted_token_interval_s: float = 0.01,
    scripted_max_tokens: int = 12,
    admin_base_url: str | None = None,
    metrics_url: str | None = None,
    code_version: str = "5.0.0",
    arm_order: str = "",
) -> dict[str, Any]:
    """跑一条 trace。DS 上 arm 不得为 G2-residual。"""
    if backend_kind == "ds" and arm == "G2-residual":
        raise ValueError("G2-residual is forbidden on DeepSeek black-box API")
    if arm not in ARM_CONTRACT:
        raise ValueError(f"unknown arm: {arm}")
    signal_policy, stop_on_cancel = ARM_CONTRACT[arm]
    if arm == "G0":
        stop_on_cancel = False
    elif arm == "G2-local":
        stop_on_cancel = True
    else:
        stop_on_cancel = True

    ids = IdFactory(seed)
    event_id = f"{pair_id}-{arm}"
    spec = make_scenario_spec(
        scenario_id=cell_id,
        task_kind="invalidate",
        t_change_s=t_change_s,
        t_restore_s=None,
        initial_stock=1,
    )
    env = InventoryEnvironment(stock=spec.initial_stock, schedule=[], seed=seed, pace="step", ids=ids)
    driver = ScenarioDriver(spec=spec, env=env)

    if backend_kind == "ds":
        backend: Any = HttpStreamingBackend(
            base_url=base_url,
            api_key=api_key,
            model=model,
            system_prompt=STREAM_SYSTEM,
            tool_choice="auto",
            stop_on_cancel=stop_on_cancel,
            on_first_delta=driver.start_anchor if spec.cues else None,
            first_delta_delay_s=0.0,
            ids=ids,
            timeout_s=TIMEOUT_S,
            max_inflight=slots_s,
            extra_payload={
                "thinking": {"type": "disabled"},
                "max_tokens": 256,
                "temperature": 0,
            },
        )
        backend_stop_status = "unknown"
    elif backend_kind == "scripted":
        backend = ResidualTokenBackend(
            residual_hold_s=residual_hold_s if arm == "G2-residual" else 0.0,
            token_interval_s=scripted_token_interval_s,
            max_tokens=scripted_max_tokens,
            backend_slots=1,
            max_inflight=slots_s,
            stop_on_cancel=stop_on_cancel,
            ids=ids,
        )
        backend_stop_status = "delayed" if arm == "G2-residual" and residual_hold_s > 0 else "stopped"
    else:
        backend = VllmObservingBackend(
            base_url=base_url,
            api_key=api_key or "EMPTY",
            model=model,
            system_prompt=STREAM_SYSTEM,
            stop_on_cancel=stop_on_cancel,
            admin_base_url=admin_base_url,
            metrics_url=metrics_url,
            max_inflight=slots_s,
            timeout_s=TIMEOUT_S,
            ids=ids,
        )
        backend_stop_status = "unknown"  # filled from poll when available

    scheduler = FifoAdmissionScheduler(replan_to_tail=True, ids=ids, order="fifo")
    run_id = f"v50-{cell_id}-s{seed}-{arm}-{backend_kind}"
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=scheduler,
        ids=ids,
        run_id=run_id,
        max_steps=20000,
        seed=seed,
        signal_policy=signal_policy,
        await_recovery_on_depletion=False,
    )

    plan = build_task_plan(seed, bystanders_b=bystanders_b)
    # Drop successor-marker as separate API task for DS main D1 (optional); keep bystanders+target
    plan = [p for p in plan if p["workflow_role"] != "successor"]
    now = time.monotonic()
    tasks = [
        Task(
            task_id=str(item["task_id"]),
            arrival_mono=now + float(item["arrival_offset_s"]),
            deadline_mono=now + DEADLINE_S,
            goal=goal_for(int(item["words"])),
            task_class=str(item["length_tier"]),
        )
        for item in plan
    ]
    role_by_task = {str(item["task_id"]): str(item["workflow_role"]) for item in plan}

    started = time.monotonic()
    error = ""
    try:
        await runtime.run(tasks)
    except Exception as exc:  # noqa: BLE001 — transport recorded in row
        error = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        driver.cancel_pending()
    wall_s = time.monotonic() - started
    events = list(runtime.events.iter_events(run_id=runtime.run_id))

    # Optional vLLM observation upgrade (never invent residual=0 on DS)
    if backend_kind == "vllm" and isinstance(backend, VllmObservingBackend):
        for hid in list(getattr(backend, "_request_ids", {})):
            obs = backend.last_observation(hid) or await backend.poll_observation(hid)
            if obs.get("backend_stop_status") in {"stopped", "delayed"}:
                backend_stop_status = str(obs["backend_stop_status"])

    rows: list[dict[str, Any]] = []
    for item, task in zip(plan, tasks):
        role = role_by_task[task.task_id]
        stop_status = "unknown" if backend_kind == "ds" else backend_stop_status
        row = extract_role_row(
            events=events,
            task_id=task.task_id,
            workflow_role=role,
            event_id=event_id,
            arrival_mono=float(task.arrival_mono),
            backend_stop_status=stop_status,
        )
        if backend_kind == "ds":
            row["backend_stop_status"] = "unknown"
            row["residual_lower_s"] = None
            row["residual_upper_s"] = None
        row.update(
            {
                "seed": seed,
                "arm": arm,
                "load": load,
                "cell_id": cell_id,
                "pair_id": pair_id,
                "batch_id": batch_id,
                "backend_kind": backend_kind,
                "slots_S": slots_s,
                "bystanders_B": bystanders_b,
                "t_change_s": t_change_s,
                "arm_order": arm_order,
                "code_version": code_version,
                "wall_s": wall_s,
                "error": error,
                "failed": bool(error),
                "task_status": runtime.registry.get_task(task.task_id).status.value,
            }
        )
        rows.append(row)

    interesting = {
        "backend_failed",
        "cancel_failed",
        "action_rejected",
        "late_output_rejected",
        "eligibility_revoked",
        "token_delta",
        "tool_call_delta",
    }
    diagnostics = []
    for ev in events:
        if ev.event_type not in interesting:
            continue
        diagnostics.append(
            {
                "type": ev.event_type,
                "task_id": ev.identity.get("task_id"),
                "reason": str(getattr(ev, "reason", "") or "")[:220],
                "detail": str((getattr(ev, "usage", None) or {}).get("detail", ""))[:120],
            }
        )
        if len(diagnostics) >= 24:
            break
    summary = summarize_bystanders(rows, tau=tau_s)
    focal = next((r for r in rows if r["workflow_role"] == "target"), None)
    return {
        "seed": seed,
        "arm": arm,
        "load": load,
        "cell_id": cell_id,
        "pair_id": pair_id,
        "backend_kind": backend_kind,
        "arm_order": arm_order,
        "failed": bool(error) or any(r.get("failed") for r in rows),
        "error": error,
        "wall_s": wall_s,
        "rows": rows,
        "summary": summary,
        "diagnostics": diagnostics,
        "focal": focal,
        "claim_ceiling": (
            "client-observable only"
            if backend_kind == "ds"
            else "mechanism-observability depends on L2-a/b/c"
        ),
    }
