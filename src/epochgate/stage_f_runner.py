"""阶段 F（v1.2）执行与指标提取模块。

严格满足 F 阶段执行前十项修订要求：
1. F1 拆分为 F1-M（7 格失效机制实验，不恢复）与 F1-R（3 格可恢复集成实验，独立定时恢复）；
   未在外生变化前触发的请求绝不标为成功取消或成功恢复；
   同时记录 first_delta 锚点与 request_started_to_stock_change_s。
2. 显式记录 seed_split ("dev" | "confirmatory")、package_version、git_commit、git_worktree_clean、
   uv_lock_hash、config_hash、prompt_hash、tool_schema_hash、model、provider、pair_id / batch_id、arm_order。
3. 配对触发状态四分类：both_triggered / A_only_triggered / B_only_triggered / neither_triggered；
   单臂触发与失败不合并。
4. F2 观察成本作用域隔离（observe_delay_scope: initial_only / refresh_only / all_observations），
   记录初始观察与重观察实际耗时、开始/结束时刻。
5. F4 严格 FIFO 队列公平性合同（FifoAdmissionScheduler, replan_to_tail=True），
   记录队列位置、被跨越次数与预先冻结的客观饥饿指标（starvation_count）。
6. F3 六维后端取消能力合同与非流式边界对照。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import epochgate
from epochgate.http_backend import HttpToolBackend
from epochgate.http_streaming import HttpStreamingBackend
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.matrix_run import PROMPT_VERSION, STREAM_SYSTEM, TIMEOUT_S, goal_for
from epochgate.models import Task
from epochgate.real_run import RESERVE_SYSTEM, RESERVE_TOOL_CHOICE
from epochgate.runtime import Runtime
from epochgate.scenario_driver import ScenarioDriver, make_scenario_spec
from epochgate.scheduler import FifoAdmissionScheduler, NearAdmissionScheduler
from epochgate. stage_e_runner import (
    DEADLINE_S,
    LENGTH_SPECS,
    TIMING_SPECS,
    arm_order_for_seed,
    extract_stage_e_row,
)

ROOT = Path(__file__).resolve().parents[2]

# 预设冻结的 F4 客观饥饿阈值（不得事后主观修改）
STARVATION_WAIT_THRESHOLD_S = 25.0
STARVATION_OVERTAKEN_LIMIT = 2


@dataclass(frozen=True)
class ProvenanceMeta:
    """正式确认性实验每行必带的溯源元数据（修订要求 8）。"""

    package_version: str
    git_commit: str
    git_worktree_clean: bool
    uv_lock_hash: str
    config_hash: str
    prompt_hash: str
    tool_schema_hash: str
    model: str
    provider: str


def build_provenance_meta(*, model: str, base_url: str, config_tag: str = "stage_f_v1.2") -> ProvenanceMeta:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
        status_out = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        # Ignore untracked results/real/phase_f/ files generated during current run when checking code cleanliness
        dirty_code_lines = [
            line
            for line in status_out.splitlines()
            if not line.strip().endswith(("phase_f",)) and "results/real/phase_f" not in line
        ]
        clean = len(dirty_code_lines) == 0
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = "unknown"
        clean = False

    lock_path = ROOT / "uv.lock"
    lock_hash = hashlib.sha256(lock_path.read_bytes()).hexdigest()[:16] if lock_path.is_file() else "unknown"
    prompt_hash = hashlib.sha256(STREAM_SYSTEM.encode("utf-8")).hexdigest()[:16]
    env_tmp = InventoryEnvironment(stock=1, schedule=[], seed=1)
    schema_blob = json.dumps([t.model_dump() for t in env_tmp.tool_catalog()], sort_keys=True)
    tool_hash = hashlib.sha256(schema_blob.encode("utf-8")).hexdigest()[:16]
    cfg_blob = json.dumps(
        {
            "config_tag": config_tag,
            "length_specs": LENGTH_SPECS,
            "timing_specs": TIMING_SPECS,
            "starvation_wait_s": STARVATION_WAIT_THRESHOLD_S,
            "starvation_overtaken_limit": STARVATION_OVERTAKEN_LIMIT,
        },
        sort_keys=True,
    )
    cfg_hash = hashlib.sha256(cfg_blob.encode("utf-8")).hexdigest()[:16]
    return ProvenanceMeta(
        package_version=epochgate.__version__,
        git_commit=commit,
        git_worktree_clean=clean,
        uv_lock_hash=lock_hash,
        config_hash=cfg_hash,
        prompt_hash=prompt_hash,
        tool_schema_hash=tool_hash,
        model=model,
        provider=base_url,
    )


def classify_pair_trigger_status(a_outcome: str, b_outcome: str) -> str:
    """配对级四状态分类（修订要求 3）。"""
    a_ok = a_outcome == "triggered"
    b_ok = b_outcome == "triggered"
    if a_ok and b_ok:
        return "both_triggered"
    if a_ok and not b_ok:
        return "A_only_triggered"
    if not a_ok and b_ok:
        return "B_only_triggered"
    return "neither_triggered"


def enrich_row_for_stage_f(
    row: dict[str, Any],
    *,
    events: list,
    env: InventoryEnvironment,
    meta: ProvenanceMeta,
    seed_split: str,
    batch_id: str,
    observe_delay_scope: str,
    configured_observe_delay_s: float,
    queue_position: int = 0,
    overtaken_count: int = 0,
    scheduler_policy: str = "NearAdmissionScheduler",
) -> dict[str, Any]:
    """补充阶段 F 要求的全部溯源、时间锚点、观察隔离与饥饿判定字段。"""
    req_start = row.get("request_started_at")
    stock_app = row.get("stock_change_applied_at")
    req_to_change = (
        (float(stock_app) - float(req_start))
        if (req_start is not None and stock_app is not None)
        else None
    )

    # Extract initial vs refresh observation metrics from env.observe_log and runtime events
    task_id = row["task_id"]
    obs_events = [
        ev
        for ev in events
        if ev.event_type == "observations_refreshed" and ev.identity.get("task_id") == task_id
    ]
    init_obs_ev = next((ev for ev in obs_events if (ev.usage or {}).get("observation_phase") == "initial"), None)
    ref_obs_evs = [ev for ev in obs_events if (ev.usage or {}).get("observation_phase") == "refresh"]

    init_lat = (init_obs_ev.usage or {}).get("actual_observe_latency_s") if init_obs_ev is not None else None
    ref_lats = [
        float((ev.usage or {})["actual_observe_latency_s"])
        for ev in ref_obs_evs
        if (ev.usage or {}).get("actual_observe_latency_s") is not None
    ]
    ref_started = (ref_obs_evs[0].usage or {}).get("refresh_started_at") if ref_obs_evs else None
    ref_completed = (ref_obs_evs[-1].usage or {}).get("refresh_completed_at") if ref_obs_evs else None

    # Strict requirement 1: never mark a request that finished/committed BEFORE exogenous change as successful cancel or recovery
    triggered = row["trigger_outcome"] == "triggered"
    successful_cancellation = triggered and (not row["old_attempt_committed"])
    successful_recovery = (
        triggered
        and row["task_kind"] == "recoverable"
        and (not row["old_attempt_committed"])
        and row["action_commits"] == 1
        and row["final_stock"] < row["initial_stock"]
    )

    # Objective starvation predicate (Requirement 5)
    first_wait = float(row.get("first_queue_wait_s") or 0.0)
    timed_out = row.get("task_status") == "timed_out"
    starved = (
        first_wait > STARVATION_WAIT_THRESHOLD_S
        or overtaken_count > STARVATION_OVERTAKEN_LIMIT
        or timed_out
    )

    row.update(
        {
            "package_version": meta.package_version,
            "git_commit": meta.git_commit,
            "git_worktree_clean": meta.git_worktree_clean,
            "uv_lock_hash": meta.uv_lock_hash,
            "config_hash": meta.config_hash,
            "prompt_hash": meta.prompt_hash,
            "tool_schema_hash": meta.tool_schema_hash,
            "model": meta.model,
            "provider": meta.provider,
            "seed_split": seed_split,
            "batch_id": batch_id,
            "scheduler_policy": scheduler_policy,
            "t_change_anchor": "first_delta",
            "request_started_to_stock_change_s": req_to_change,
            "successful_cancellation": successful_cancellation,
            "successful_recovery": successful_recovery,
            "observe_delay_scope": observe_delay_scope,
            "configured_observe_delay_s": configured_observe_delay_s,
            "actual_initial_observe_latency_s": init_lat,
            "actual_refresh_observe_latency_s": (sum(ref_lats) / len(ref_lats)) if ref_lats else None,
            "refresh_observe_count": len(ref_obs_evs),
            "refresh_started_at": ref_started,
            "refresh_completed_at": ref_completed,
            "initial_queue_position": queue_position,
            "overtaken_by_later_tasks_count": overtaken_count,
            "starvation_detected": starved,
        }
    )
    return row


async def run_stage_f_arm(
    *,
    meta: ProvenanceMeta,
    api_key: str,
    seed_split: str,
    experiment_block: str,
    cell_id: str,
    pair_id: str,
    batch_id: str,
    seed: int,
    arm: str,
    arm_order: str,
    task_kind: str,
    length_tier: str,
    timing_tier: str,
    target_words: int,
    t_change_s: float,
    t_restore_s: float | None,
    initial_stock: int,
    restore_delta: int = 1,
    observe_delay_s: float = 0.0,
    observe_delay_scope: str = "refresh_only",
    task_plan: list[dict[str, Any]] | None = None,
    use_fifo_scheduler: bool = False,
) -> list[dict[str, Any]]:
    """运行阶段 F 的单臂（支持 F1-M、F1-R、F2 observe_delay_scope、F4 多任务 FIFO 队列）。"""
    if arm not in {"A", "B"}:
        raise ValueError("arm must be A or B")
    ids = IdFactory(seed)
    spec = make_scenario_spec(
        scenario_id=cell_id,
        task_kind=task_kind,
        t_change_s=t_change_s,
        t_restore_s=t_restore_s,
        initial_stock=initial_stock,
        restore_delta=restore_delta,
    )
    env = InventoryEnvironment(
        stock=spec.initial_stock,
        schedule=[],
        seed=seed,
        pace="step",
        ids=ids,
        observe_delay_s=observe_delay_s,
        observe_delay_scope=observe_delay_scope,
    )
    driver = ScenarioDriver(spec=spec, env=env)

    backend = HttpStreamingBackend(
        base_url=meta.provider,
        api_key=api_key,
        model=meta.model,
        system_prompt=STREAM_SYSTEM,
        tool_choice="auto",
        stop_on_cancel=(arm == "B"),
        on_first_delta=driver.start_anchor if spec.cues else None,
        first_delta_delay_s=0.0,
        ids=ids,
        timeout_s=TIMEOUT_S,
    )
    scheduler = FifoAdmissionScheduler(replan_to_tail=True, ids=ids) if use_fifo_scheduler else NearAdmissionScheduler(ids=ids)
    sched_name = "FifoAdmissionScheduler(replan_to_tail=True)" if use_fifo_scheduler else "NearAdmissionScheduler"
    run_id = f"{experiment_block}-{cell_id}-s{seed}-{arm}"
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=scheduler,
        ids=ids,
        run_id=run_id,
        max_steps=12000,
        seed=seed,
        await_recovery_on_depletion=(task_kind == "recoverable"),
    )

    now = time.monotonic()
    if task_plan is None:
        tasks = [
            Task(
                task_id="t0",
                arrival_mono=now,
                deadline_mono=now + DEADLINE_S,
                goal=goal_for(target_words),
            )
        ]
        plan_meta = [{"task_id": "t0", "words": target_words, "length_tier": length_tier, "arrival_offset_s": 0.0}]
    else:
        tasks = []
        plan_meta = []
        for idx, item in enumerate(task_plan):
            tid = item.get("task_id", f"t{idx}")
            w = int(item["words"])
            lt = str(item.get("length_tier", length_tier))
            arr_off = float(item.get("arrival_offset_s", idx * 0.001))
            tasks.append(
                Task(
                    task_id=tid,
                    arrival_mono=now + arr_off,
                    deadline_mono=now + DEADLINE_S,
                    goal=goal_for(w),
                )
            )
            plan_meta.append({"task_id": tid, "words": w, "length_tier": lt, "arrival_offset_s": arr_off})

    started = time.monotonic()
    error = ""
    try:
        await runtime.run(tasks)
    except RuntimeError as exc:
        error = str(exc)
    finally:
        driver.cancel_pending()
    wall_s = time.monotonic() - started
    events = list(runtime.events.iter_events(run_id=runtime.run_id))

    # Compute overtaken counts per task (how many tasks that arrived strictly after task_i committed before task_i)
    commit_time_by_task: dict[str, float] = {}
    for ev in events:
        if ev.event_type == "action_committed":
            tid = str(ev.identity.get("task_id") or "")
            if tid and tid not in commit_time_by_task:
                commit_time_by_task[tid] = float(ev.mono_time)

    rows: list[dict[str, Any]] = []
    for pos, (task, pmeta) in enumerate(zip(tasks, plan_meta)):
        t_obj = runtime.registry.get_task(task.task_id)
        my_commit = commit_time_by_task.get(task.task_id, float("inf"))
        overtaken = 0
        for later_pos in range(pos + 1, len(tasks)):
            later_tid = tasks[later_pos].task_id
            if commit_time_by_task.get(later_tid, float("inf")) < my_commit:
                overtaken += 1

        base_row = extract_stage_e_row(
            events,
            pair_id=pair_id,
            run_id=run_id,
            seed=seed,
            arm=arm,
            arm_order=arm_order,
            experiment_block=experiment_block,
            cell_id=cell_id,
            task_kind=task_kind,
            length_tier=pmeta["length_tier"],
            timing_tier=timing_tier,
            target_words=pmeta["words"],
            t_change_plan_s=t_change_s,
            t_restore_plan_s=t_restore_s,
            concurrency=len(tasks),
            task_id=task.task_id,
            task_status=t_obj.status.value,
            driver=driver,
            final_stock=env.stock,
            initial_stock=spec.initial_stock,
            wall_s=wall_s,
            code_version=meta.git_commit,
            error=error,
            evidence_class="real_model",
        )
        enriched = enrich_row_for_stage_f(
            base_row,
            events=events,
            env=env,
            meta=meta,
            seed_split=seed_split,
            batch_id=batch_id,
            observe_delay_scope=observe_delay_scope,
            configured_observe_delay_s=observe_delay_s,
            queue_position=pos,
            overtaken_count=overtaken,
            scheduler_policy=sched_name,
        )
        rows.append(enriched)
    return rows


async def run_f3_nonstreaming_boundary_probe(
    *,
    meta: ProvenanceMeta,
    api_key: str,
    seed: int,
    seed_split: str = "confirmatory",
) -> dict[str, Any]:
    """F3 非流式真实 API 能力边界对照（不与流式 A/B 词元表合并）。"""
    ids = IdFactory(seed)
    env = InventoryEnvironment(stock=1, schedule=[], seed=seed, pace="step", ids=ids)
    # Trigger exogenous wipe right after initial observation is recorded
    orig_observe = env.observe

    async def observe_then_schedule_wipe(recipe):
        batch = await orig_observe(recipe)
        if env.observe_calls == 1:
            env.apply_exogenous(-1)
        return batch

    env.observe = observe_then_schedule_wipe  # type: ignore[method-assign]
    backend = HttpToolBackend(
        base_url=meta.provider,
        api_key=api_key,
        model=meta.model,
        system_prompt=RESERVE_SYSTEM,
        tool_choice=RESERVE_TOOL_CHOICE,
        parse_retries=1,
        ids=ids,
    )
    caps = backend.capabilities()
    run_id = f"F3-nonstream-s{seed}"
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=run_id,
        max_steps=500,
        seed=seed,
    )
    now = time.monotonic()
    started = time.monotonic()
    error = ""
    try:
        await runtime.run([Task(task_id="t0", arrival_mono=now, deadline_mono=now + 60.0, goal="reserve one unit")])
    except RuntimeError as exc:
        error = str(exc)
    wall_s = time.monotonic() - started
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    commits = sum(1 for ev in events if ev.event_type == "action_committed")
    rejects = sum(1 for ev in events if ev.event_type == "action_rejected")
    return {
        "experiment_block": "F3_nonstreaming_capability_boundary",
        "evidence_class": "nonstreaming_boundary_control",
        "package_version": meta.package_version,
        "git_commit": meta.git_commit,
        "git_worktree_clean": meta.git_worktree_clean,
        "uv_lock_hash": meta.uv_lock_hash,
        "config_hash": meta.config_hash,
        "prompt_hash": meta.prompt_hash,
        "tool_schema_hash": meta.tool_schema_hash,
        "model": meta.model,
        "provider": meta.provider,
        "seed": seed,
        "seed_split": seed_split,
        "pair_id": f"F3-nonstream:seed{seed}",
        "batch_id": f"F3-nonstream:seed{seed}",
        "arm": "NON_STREAM",
        "arm_order": "SINGLE",
        "capabilities_6d": {
            "streaming_capability": caps.streaming_capability,
            "local_read_abort": caps.local_read_abort,
            "client_stream_close_issued": caps.client_stream_close_issued,
            "transport_disconnect_observed": caps.transport_disconnect_observed,
            "provider_cancel_api_invoked": caps.provider_cancel_api_invoked,
            "provider_cancel_ack_received": caps.provider_cancel_ack_received,
            "provider_cancel_request": caps.provider_cancel_request,
            "provider_cancel_ack_semantics": caps.provider_cancel_ack_semantics,
            "server_generation_observability": caps.server_generation_observability,
            "server_generation_stopped_observed": caps.server_generation_stopped_observed,
            "server_resource_reclaim_observability": caps.server_resource_reclaim_observability,
            "server_resource_reclaim_observed": caps.server_resource_reclaim_observed,
        },
        "action_commits": commits,
        "action_rejects": rejects,
        "old_attempt_committed": commits > 0,
        "gateway_eligibility_gate_held": commits == 0 and env.stock == 0,
        "final_stock": env.stock,
        "wall_clock_s": wall_s,
        "error": error,
    }
