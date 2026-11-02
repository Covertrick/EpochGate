"""P1.2–P1.5：阶段 E 统一实验执行器、指标提取与 A/B 配对行构建。

严格遵守：
- A/B 唯一处理变量：资格撤销后是否关闭本地响应流（stop_on_cancel = (arm == 'B')）。
- 两臂均在线性化点先撤销旧 attempt 资格（eligibility_revoked）。
- 外生变化由独立 ScenarioDriver 按固定相对时间推进，不依赖 A/B 完成时刻。
- 每个事件与结果行均关联 pair_id、run_id、task_id、attempt_id、request_id。
- 多任务批次按 task_id 独立核算重规划输出量，不混算整个 batch。
"""

from __future__ import annotations

import time
from typing import Any

from epochgate.http_streaming import HttpStreamingBackend
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.matrix_run import PROMPT_VERSION, STREAM_SYSTEM, TIMEOUT_S, goal_for
from epochgate.models import Task, TaskStatus
from epochgate.runtime import Runtime
from epochgate.scenario_driver import ScenarioDriver, ScenarioSpec, make_scenario_spec
from epochgate.scheduler import NearAdmissionScheduler
from epochgate.scripted_backend import ScriptShot, ScriptedValidityBackend
from epochgate.text_tokens import TOKENIZER_ENCODING, TOKENIZER_NAME, count_observed_text_tokens

DEADLINE_S = 120.0

LENGTH_SPECS: dict[str, int] = {
    "short": 50,
    "mid": 200,
    "long": 450,
}

TIMING_SPECS: dict[str, float] = {
    "early": 0.25,
    "mid": 0.80,
    "late": 1.80,
}


def arm_order_for_seed(seed: int) -> tuple[str, str]:
    """按配对种子确定性伪随机决定 A->B 或 B->A 执行顺序。"""
    # 使用简单散列避免全先 A 后 B
    h = ((seed * 1103515245 + 12345) >> 16) & 1
    return ("A", "B") if h == 0 else ("B", "A")


def extract_stage_e_row(
    events: list,
    *,
    pair_id: str,
    run_id: str,
    seed: int,
    arm: str,
    arm_order: str,
    experiment_block: str,
    cell_id: str,
    task_kind: str,
    length_tier: str,
    timing_tier: str,
    target_words: int,
    t_change_plan_s: float,
    t_restore_plan_s: float | None,
    concurrency: int,
    task_id: str,
    task_status: str,
    driver: ScenarioDriver,
    final_stock: int,
    initial_stock: int,
    wall_s: float,
    code_version: str,
    error: str,
    evidence_class: str = "real_model",
) -> dict[str, Any]:
    """针对指定 task_id 提取统一结构的阶段 E 行（A/B 字段完全一致）。"""
    task_events = [
        ev for ev in events if ev.identity.get("task_id") == task_id or not ev.identity.get("task_id")
    ]
    attempt_ids = _attempts_for_task(events, task_id)
    first_attempt_id = attempt_ids[0] if attempt_ids else ""
    request_id = f"{pair_id}:{arm}:{task_id}"

    eligibility_ev = _first_for_attempt(events, "eligibility_revoked", first_attempt_id)
    cancel_req_ev = _first_for_attempt(events, "cancel_requested", first_attempt_id)
    cancel_ack_ev = _first_for_attempt(events, "cancel_acknowledged", first_attempt_id)
    conn_closed_ev = _first_for_attempt(events, "connection_closed", first_attempt_id)
    local_stop_ev = _first_for_attempt(events, "local_read_stopped", first_attempt_id)
    slot_rel_ev = _first_for_attempt(events, "slot_released", first_attempt_id)
    req_start_ev = _first_for_attempt(events, "backend_request", first_attempt_id)
    first_delta_ev = _first_delta_for_attempt(events, first_attempt_id)
    tool_delta_ev = _first_for_attempt(events, "tool_call_delta", first_attempt_id)
    att_term_ev = _attempt_terminal_for_attempt(events, first_attempt_id)

    enqueued_ev = _first_for_task(events, "task_registered", task_id) or _first_for_task(
        events, "runtime_start", task_id
    )
    task_term_ev = _first_for_task(events, "task_completed", task_id)
    batch_done_ev = next((ev for ev in reversed(events) if ev.event_type == "run_finished"), None)

    revoked_ev = eligibility_ev or cancel_req_ev
    revoke_mono = None if revoked_ev is None else float(revoked_ev.mono_time)

    # First attempt text & events before/after revocation
    pre_text = _text_for_attempt(events, first_attempt_id, before_mono=revoke_mono, kind="token_delta")
    post_text = (
        ""
        if revoke_mono is None
        else _text_for_attempt(events, first_attempt_id, from_mono=revoke_mono, kind="token_delta")
    )
    full_first_text = _text_for_attempt(events, first_attempt_id, before_mono=None, kind="token_delta")
    text_before_tool = _text_for_attempt(
        events,
        first_attempt_id,
        before_mono=(float(tool_delta_ev.mono_time) if tool_delta_ev is not None else None),
        kind="token_delta",
    )

    pre_text_events = _count_for_attempt(events, first_attempt_id, before_mono=revoke_mono, kind="token_delta")
    pre_tool_events = _count_for_attempt(events, first_attempt_id, before_mono=revoke_mono, kind="tool_call_delta")
    post_text_events = (
        0
        if revoke_mono is None
        else _count_for_attempt(events, first_attempt_id, from_mono=revoke_mono, kind="token_delta")
    )
    post_tool_events = (
        0
        if revoke_mono is None
        else _count_for_attempt(events, first_attempt_id, from_mono=revoke_mono, kind="tool_call_delta")
    )

    pre_obs_tokens = count_observed_text_tokens(pre_text)
    post_obs_tokens = count_observed_text_tokens(post_text)
    first_attempt_total_obs_tokens = count_observed_text_tokens(full_first_text)

    # Replan metrics strictly scoped to THIS task_id (excluding first_attempt_id)
    subsequent_attempts = attempt_ids[1:]
    replan_delta_events = sum(
        _count_for_attempt(events, att_id, before_mono=None, kind="token_delta")
        + _count_for_attempt(events, att_id, before_mono=None, kind="tool_call_delta")
        for att_id in subsequent_attempts
    )
    replan_text_body = "".join(
        _text_for_attempt(events, att_id, before_mono=None, kind="token_delta")
        for att_id in subsequent_attempts
    )
    replan_obs_tokens = count_observed_text_tokens(replan_text_body)

    replan_started_at: float | None = None
    for att_id in subsequent_attempts:
        ev = _first_for_attempt(events, "backend_request", att_id) or _first_for_attempt(
            events, "attempt_begun", att_id
        )
        if ev is not None:
            replan_started_at = float(ev.mono_time)
            break

    commits_for_task = [
        ev for ev in events if ev.event_type == "action_committed" and ev.identity.get("task_id") == task_id
    ]
    rejects_for_task = [
        ev for ev in events if ev.event_type == "action_rejected" and ev.identity.get("task_id") == task_id
    ]
    # 只有当首 attempt 的资格真正被撤销（revoke_mono is not None）时，首 attempt 的提交才构成「被撤销旧 attempt 越权提交」；
    # 若请求在外生变化触发前已正常完成并提交（trigger_not_fired），则属于未触发，而非旧 attempt 违规提交。
    old_attempt_committed = (
        any(ev.identity.get("attempt_id") == first_attempt_id for ev in commits_for_task)
        and revoke_mono is not None
    )

    replan_committed_at: float | None = None
    for ev in commits_for_task:
        if ev.identity.get("attempt_id") in subsequent_attempts:
            replan_committed_at = float(ev.mono_time)
            break

    stock_applied_at = driver.first_change_applied_at()
    stock_restored_at = driver.restore_applied_at()
    stock_scheduled_at = driver.anchor_mono if driver.spec.cues else None

    elig_at = None if eligibility_ev is None else float(eligibility_ev.mono_time)
    creq_at = None if cancel_req_ev is None else float(cancel_req_ev.mono_time)
    cack_at = None if cancel_ack_ev is None else float(cancel_ack_ev.mono_time)
    cclose_at = None if conn_closed_ev is None else float(conn_closed_ev.mono_time)
    lstop_at = None if local_stop_ev is None else float(local_stop_ev.mono_time)
    slot_rel_at = None if slot_rel_ev is None else float(slot_rel_ev.mono_time)
    req_start_at = None if req_start_ev is None else float(req_start_ev.mono_time)
    fdelta_at = None if first_delta_ev is None else float(first_delta_ev.mono_time)
    enq_at = None if enqueued_ev is None else float(enqueued_ev.mono_time)
    tterm_at = None if task_term_ev is None else float(task_term_ev.mono_time)
    bcomp_at = None if batch_done_ev is None else float(batch_done_ev.mono_time)

    # Derived latencies per section 5 formulas
    t_detect = (elig_at - stock_applied_at) if (elig_at is not None and stock_applied_at is not None) else None
    t_cancel_local = (lstop_at - creq_at) if (lstop_at is not None and creq_at is not None) else None
    t_slot_release = (slot_rel_at - elig_at) if (slot_rel_at is not None and elig_at is not None) else None
    t_recovery = (replan_committed_at - elig_at) if (replan_committed_at is not None and elig_at is not None) else None
    slot_hold_s = (slot_rel_at - req_start_at) if (slot_rel_at is not None and req_start_at is not None) else None
    first_delta_to_revoke_s = (elig_at - fdelta_at) if (elig_at is not None and fdelta_at is not None) else None

    revocation_progress_ratio: float | None = None
    if elig_at is not None and first_attempt_total_obs_tokens > 0:
        revocation_progress_ratio = pre_obs_tokens / first_attempt_total_obs_tokens

    provider_tokens = _provider_completion_for_attempt(events, first_attempt_id)
    failures = [
        ev.reason or ""
        for ev in events
        if ev.event_type == "backend_failed"
        and (ev.identity.get("task_id") == task_id or not ev.identity.get("task_id"))
    ]

    trigger_outcome = _classify_trigger_outcome(
        task_kind=task_kind,
        stock_fired=stock_applied_at is not None,
        eligibility_at=elig_at,
        tool_delta_at=(None if tool_delta_ev is None else float(tool_delta_ev.mono_time)),
        failures=failures,
        error=error,
        events=events,
        first_attempt_id=first_attempt_id,
    )

    queue_waits = _task_queue_waits(events, task_id)
    first_queue_wait_s = queue_waits[0] if queue_waits else 0.0
    task_e2e_s = (tterm_at - enq_at) if (tterm_at is not None and enq_at is not None) else wall_s

    valid_commits = len(commits_for_task)
    side_effect_once = valid_commits == 1 and (initial_stock - final_stock) <= 1 if task_kind != "recoverable" else (
        valid_commits == 1 and final_stock == 0
    )
    correctness_ok = _check_task_correctness(
        task_kind=task_kind,
        valid_commits=valid_commits,
        old_attempt_committed=old_attempt_committed,
        final_stock=final_stock,
        initial_stock=initial_stock,
        stock_fired=(stock_applied_at is not None),
    )

    return {
        "prompt_version": PROMPT_VERSION,
        "code_version": code_version,
        "evidence_class": evidence_class,
        "experiment_block": experiment_block,
        "cell_id": cell_id,
        "pair_id": pair_id,
        "run_id": run_id,
        "task_id": task_id,
        "attempt_id": first_attempt_id,
        "request_id": request_id,
        "attempt_ids": attempt_ids,
        "seed": seed,
        "arm": arm,
        "arm_order": arm_order,
        "task_kind": task_kind,
        "length_tier": length_tier,
        "timing_tier": timing_tier,
        "target_words": target_words,
        "trigger_delay_s": t_change_plan_s,
        "restore_delay_s": t_restore_plan_s,
        "concurrency": concurrency,
        "queue_pressure_label": "low_single" if concurrency == 1 else f"serial_queue_x{concurrency}",
        "max_inflight": 1,
        "trigger_outcome": trigger_outcome,
        "stock_change_fired": stock_applied_at is not None,
        "stock_restored_fired": stock_restored_at is not None,
        "actual_words_before_tool": len(text_before_tool.split()),
        "pre_revocation_delta_events": pre_text_events,
        "pre_revocation_tool_call_delta_events": pre_tool_events,
        "pre_revocation_observed_text_tokens": pre_obs_tokens,
        "post_revocation_delta_events": post_text_events,
        "post_revocation_tool_call_delta_events": post_tool_events,
        "post_revocation_observed_text_tokens": post_obs_tokens,
        "first_attempt_total_observed_text_tokens": first_attempt_total_obs_tokens,
        "revocation_progress_ratio": revocation_progress_ratio,
        "tool_call_seen_before_revoke": (
            tool_delta_ev is not None and elig_at is not None and float(tool_delta_ev.mono_time) < elig_at
        ),
        "tokenizer_name": TOKENIZER_NAME,
        "tokenizer_encoding": TOKENIZER_ENCODING,
        "provider_completion_tokens": provider_tokens,
        "replan_local_deltas": replan_delta_events,
        "replan_observed_text_tokens": replan_obs_tokens,
        "attempt_count": len(attempt_ids),
        "action_commits": valid_commits,
        "valid_commits": valid_commits,
        "valid_commit": valid_commits > 0,
        "action_rejects": len(rejects_for_task),
        "old_attempt_committed": old_attempt_committed,
        "side_effect_once": valid_commits == 1,
        "correctness_ok": correctness_ok,
        "task_status": task_status,
        "initial_stock": initial_stock,
        "final_stock": final_stock,
        "task_enqueued_at": enq_at,
        "request_started_at": req_start_at,
        "first_delta_at": fdelta_at,
        "stock_change_scheduled_at": stock_scheduled_at,
        "stock_change_applied_at": stock_applied_at,
        "stock_restored_at": stock_restored_at,
        "eligibility_revoked_at": elig_at,
        "cancel_requested_at": creq_at,
        "cancel_acknowledged_at": cack_at,
        "connection_closed_at": cclose_at,
        "local_read_stopped_at": lstop_at,
        "old_attempt_slot_release_at": slot_rel_at,
        "replan_started_at": replan_started_at,
        "replan_committed_at": replan_committed_at,
        "attempt_terminal_at": None if att_term_ev is None else float(att_term_ev.mono_time),
        "task_terminal_at": tterm_at,
        "batch_completed_at": bcomp_at,
        "first_delta_to_revoke_s": first_delta_to_revoke_s,
        "detection_latency_s": t_detect,
        "cancel_local_latency_s": t_cancel_local,
        "slot_release_latency_s": t_slot_release,
        "recovery_latency_s": t_recovery,
        "old_attempt_slot_hold_s": slot_hold_s,
        "first_queue_wait_s": first_queue_wait_s,
        "queue_wait_s": queue_waits,
        "end_to_end_latency_s": task_e2e_s,
        "batch_wall_clock_s": wall_s,
        "exogenous_history": driver.summary(),
        "backend_failures": failures,
        "error": error,
    }


def _classify_trigger_outcome(
    *,
    task_kind: str,
    stock_fired: bool,
    eligibility_at: float | None,
    tool_delta_at: float | None,
    failures: list[str],
    error: str,
    events: list,
    first_attempt_id: str,
) -> str:
    if error:
        return "runtime_failed"
    # Check if the FIRST attempt itself failed with transport error before revocation
    first_att_failures: list[str] = []
    for ev in events:
        if ev.event_type == "backend_failed":
            if eligibility_at is None or float(ev.mono_time) <= eligibility_at:
                first_att_failures.append(ev.reason or "")
    for reason in first_att_failures:
        if any(tag in reason for tag in ("http_", "stream_error", "timeout", "ConnectError", "ReadError")):
            return "transport_failed"
        if "no action" in reason:
            return "model_no_action"
    if task_kind == "static":
        for reason in failures:
            if any(tag in reason for tag in ("http_", "stream_error", "timeout", "ConnectError", "ReadError")):
                return "transport_failed"
            if "no action" in reason:
                return "model_no_action"
        return "static_control"
    if not stock_fired or eligibility_at is None:
        return "trigger_not_fired"
    # Check if old action already entered gateway before eligibility_revoked
    for ev in events:
        if ev.identity.get("attempt_id") == first_attempt_id and ev.event_type in {
            "action_committed",
            "action_rejected",
        }:
            if float(ev.mono_time) <= eligibility_at:
                return "trigger_late"
    if tool_delta_at is not None and tool_delta_at < eligibility_at:
        return "trigger_late"
    return "triggered"


def _check_task_correctness(
    *,
    task_kind: str,
    valid_commits: int,
    old_attempt_committed: bool,
    final_stock: int,
    initial_stock: int,
    stock_fired: bool,
) -> bool:
    if task_kind == "static":
        return valid_commits == 1 and final_stock == initial_stock - 1
    if task_kind == "invalidate":
        return (not old_attempt_committed) and valid_commits == 0 and final_stock == 0 and stock_fired
    if task_kind == "recoverable":
        return (not old_attempt_committed) and valid_commits == 1 and final_stock == 0 and stock_fired
    return False


def _attempts_for_task(events: list, task_id: str) -> list[str]:
    seen: list[str] = []
    for ev in events:
        if ev.event_type == "attempt_begun" and ev.identity.get("task_id") == task_id:
            att_id = str(ev.identity.get("attempt_id") or "")
            if att_id and att_id not in seen:
                seen.append(att_id)
    return seen


def _first_for_attempt(events: list, event_type: str, attempt_id: str):
    if not attempt_id:
        return None
    for ev in events:
        if ev.event_type == event_type and ev.identity.get("attempt_id") == attempt_id:
            return ev
    return None


def _first_for_task(events: list, event_type: str, task_id: str):
    for ev in events:
        if ev.event_type == event_type and ev.identity.get("task_id") == task_id:
            return ev
    return None


def _first_delta_for_attempt(events: list, attempt_id: str):
    for ev in events:
        if ev.event_type in {"token_delta", "tool_call_delta"} and ev.identity.get("attempt_id") == attempt_id:
            return ev
    return None


def _text_for_attempt(
    events: list,
    attempt_id: str,
    *,
    before_mono: float | None = None,
    from_mono: float | None = None,
    kind: str = "token_delta",
) -> str:
    parts: list[str] = []
    for ev in events:
        if ev.event_type != kind or ev.identity.get("attempt_id") != attempt_id:
            continue
        t = float(ev.mono_time)
        if before_mono is not None and t >= before_mono:
            continue
        if from_mono is not None and t < from_mono:
            continue
        parts.append(ev.reason or "")
    return "".join(parts)


def _count_for_attempt(
    events: list,
    attempt_id: str,
    *,
    before_mono: float | None = None,
    from_mono: float | None = None,
    kind: str = "token_delta",
) -> int:
    total = 0
    for ev in events:
        if ev.event_type != kind or ev.identity.get("attempt_id") != attempt_id:
            continue
        t = float(ev.mono_time)
        if before_mono is not None and t >= before_mono:
            continue
        if from_mono is not None and t < from_mono:
            continue
        total += 1
    return total


def _attempt_terminal_for_attempt(events: list, attempt_id: str):
    last = None
    for ev in events:
        if ev.identity.get("attempt_id") != attempt_id:
            continue
        if ev.event_type in {
            "action_committed",
            "action_rejected",
            "backend_failed",
            "local_read_stopped",
            "slot_released",
            "cancel_failed",
            "commit_unknown",
        }:
            last = ev
    return last


def _provider_completion_for_attempt(events: list, attempt_id: str) -> int | str:
    for ev in events:
        if ev.identity.get("attempt_id") != attempt_id:
            continue
        usage = ev.usage or {}
        if usage.get("usage_source") == "provider" and "completion_tokens" in usage:
            try:
                return int(usage["completion_tokens"])
            except (TypeError, ValueError):
                return "UNKNOWN"
    return "UNKNOWN"


def _task_queue_waits(events: list, task_id: str) -> list[float]:
    enqueued_by_attempt: dict[str, float] = {}
    waits: list[float] = []
    for ev in events:
        if ev.identity.get("task_id") != task_id:
            continue
        att_id = str(ev.identity.get("attempt_id") or "")
        if ev.event_type == "attempt_begun" and att_id:
            enqueued_by_attempt[att_id] = float(ev.mono_time)
        elif ev.event_type == "backend_request" and att_id in enqueued_by_attempt:
            waits.append(max(0.0, float(ev.mono_time) - enqueued_by_attempt[att_id]))
    return waits


async def run_timed_deterministic_arm(
    *,
    spec: ScenarioSpec,
    arm: str,
    seed: int,
    code_version: str = "det",
    first_shot_tokens: int = 8,
    second_shot_tokens: int = 3,
    token_interval_s: float = 0.04,
    concurrency: int = 1,
) -> list[dict[str, Any]]:
    """使用独立 ScenarioDriver + ScriptedValidityBackend 运行确定性时序一臂。"""
    if arm not in {"A", "B"}:
        raise ValueError("arm must be A or B")
    ids = IdFactory(seed)
    env = InventoryEnvironment(stock=spec.initial_stock, schedule=[], seed=seed, pace="step", ids=ids)
    driver = ScenarioDriver(spec=spec, env=env)

    shots = [
        ScriptShot(
            token_count=first_shot_tokens,
            stop_on_cancel=(arm == "B"),
            emit_tool=True,
            token_interval_s=token_interval_s,
            tool_fragments=('{"qty":', " 1}"),
        ),
        ScriptShot(
            token_count=second_shot_tokens,
            stop_on_cancel=(arm == "B"),
            emit_tool=True,
            token_interval_s=token_interval_s,
        ),
    ]
    backend = ScriptedValidityBackend(
        shots,
        on_first_delta=driver.start_anchor if spec.anchor == "first_delta" else None,
        ids=ids,
    )
    run_id = f"det-{spec.scenario_id}-{seed}-{arm}"
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=run_id,
        max_steps=4000,
        seed=seed,
        await_recovery_on_depletion=(spec.task_kind == "recoverable"),
    )
    now = time.monotonic()
    tasks = [
        Task(
            task_id=f"t{idx}",
            arrival_mono=now + idx * 0.001,
            deadline_mono=now + 30.0,
            goal="reserve one unit",
        )
        for idx in range(concurrency)
    ]
    started = time.monotonic()
    if spec.anchor == "run_start":
        driver.start_anchor()
    error = ""
    try:
        await runtime.run(tasks)
    except RuntimeError as exc:
        error = str(exc)
    finally:
        driver.cancel_pending()
    wall_s = time.monotonic() - started
    events = list(runtime.events.iter_events(run_id=runtime.run_id))

    t_change = spec.cues[0].offset_s if spec.cues else 0.0
    t_restore = spec.cues[1].offset_s if len(spec.cues) > 1 else None
    order = "".join(arm_order_for_seed(seed))
    rows: list[dict[str, Any]] = []
    for task in tasks:
        t_obj = runtime.registry.get_task(task.task_id)
        rows.append(
            extract_stage_e_row(
                events,
                pair_id=f"{spec.scenario_id}:seed{seed}",
                run_id=run_id,
                seed=seed,
                arm=arm,
                arm_order=order,
                experiment_block="P1.1_deterministic",
                cell_id=spec.scenario_id,
                task_kind=spec.task_kind,
                length_tier="det",
                timing_tier="det",
                target_words=first_shot_tokens,
                t_change_plan_s=t_change,
                t_restore_plan_s=t_restore,
                concurrency=concurrency,
                task_id=task.task_id,
                task_status=t_obj.status.value,
                driver=driver,
                final_stock=env.stock,
                initial_stock=spec.initial_stock,
                wall_s=wall_s,
                code_version=code_version,
                error=error,
                evidence_class="deterministic_timed",
            )
        )
    return rows


async def run_real_stage_e_arm(
    *,
    base_url: str,
    api_key: str,
    model: str,
    experiment_block: str,
    cell_id: str,
    pair_id: str,
    seed: int,
    arm: str,
    arm_order: str,
    task_kind: str,
    length_tier: str,
    timing_tier: str,
    target_words: int,
    t_change_s: float,
    t_restore_s: float | None,
    concurrency: int,
    initial_stock: int,
    code_version: str,
    restore_delta: int | None = None,
) -> list[dict[str, Any]]:
    """使用独立 ScenarioDriver + HttpStreamingBackend 运行阶段 E 的一臂（单任务或三任务队列）。"""
    if arm not in {"A", "B"}:
        raise ValueError("arm must be A or B")
    ids = IdFactory(seed)
    spec = make_scenario_spec(
        scenario_id=cell_id,
        task_kind=task_kind,
        t_change_s=t_change_s,
        t_restore_s=t_restore_s,
        initial_stock=initial_stock,
        restore_delta=restore_delta if restore_delta is not None else initial_stock,
    )
    env = InventoryEnvironment(stock=spec.initial_stock, schedule=[], seed=seed, pace="step", ids=ids)
    driver = ScenarioDriver(spec=spec, env=env)

    backend = HttpStreamingBackend(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system_prompt=STREAM_SYSTEM,
        tool_choice="auto",
        stop_on_cancel=(arm == "B"),
        on_first_delta=driver.start_anchor if spec.cues else None,
        first_delta_delay_s=0.0,
        ids=ids,
        timeout_s=TIMEOUT_S,
    )
    run_id = f"{experiment_block}-{cell_id}-s{seed}-{arm}"
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=run_id,
        max_steps=10000,
        seed=seed,
        await_recovery_on_depletion=(task_kind == "recoverable"),
    )
    now = time.monotonic()
    goal = goal_for(target_words)
    tasks = [
        Task(
            task_id=f"t{idx}",
            arrival_mono=now + idx * 0.001,
            deadline_mono=now + DEADLINE_S,
            goal=goal,
        )
        for idx in range(concurrency)
    ]
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

    rows: list[dict[str, Any]] = []
    for task in tasks:
        t_obj = runtime.registry.get_task(task.task_id)
        rows.append(
            extract_stage_e_row(
                events,
                pair_id=pair_id,
                run_id=run_id,
                seed=seed,
                arm=arm,
                arm_order=arm_order,
                experiment_block=experiment_block,
                cell_id=cell_id,
                task_kind=task_kind,
                length_tier=length_tier,
                timing_tier=timing_tier,
                target_words=target_words,
                t_change_plan_s=t_change_s,
                t_restore_plan_s=t_restore_s,
                concurrency=concurrency,
                task_id=task.task_id,
                task_status=t_obj.status.value,
                driver=driver,
                final_stock=env.stock,
                initial_stock=spec.initial_stock,
                wall_s=wall_s,
                code_version=code_version,
                error=error,
                evidence_class="real_model",
            )
        )
    return rows
