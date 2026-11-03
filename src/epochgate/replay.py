"""逻辑时钟上的 G0/G1/G2 槽位回放。不调用模型 API。"""

from __future__ import annotations

from dataclasses import dataclass, field

from epochgate.roles import BYSTANDER, FALSE_TARGET, RESTART, SUCCESSOR, TARGET, followup_role, role_for_focal
from epochgate.scheduler import fcfs_sort_key, plas_sort_key, srpt_sort_key
from epochgate.workload import Workload

POLICIES = ("G0", "G1", "G2", "G2-delayed-release")
SCHEDULERS = ("fcfs", "srpt", "runtime-plas")


@dataclass
class Attempt:
    """回放中的一次占槽尝试。"""

    workflow_id: str
    attempt_id: str
    role: str
    service_demand: float
    remaining: float
    arrival_time: float
    enqueue_time: float
    length_class: str
    action_id: str
    eligible: bool = True
    revoked: bool = False
    release_at_natural_end: bool = False
    started_at: float | None = None
    released_at: float | None = None
    completed_at: float | None = None
    committed: bool = False
    termination_reason: str = "queued"
    false_cancel: bool = False


@dataclass
class ReplayResult:
    """一条 trace 的汇总和逐工作流行。"""

    summary: dict[str, object]
    rows: list[dict[str, object]] = field(default_factory=list)


def percentile(values: list[float], pct: float) -> float:
    """线性插值分位数。

    输入：数值列表和 0 到 100 的百分位。
    输出：分位数。空列表返回 0。
    副作用：无。
    失败：无。
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _sort_queue(queue: list[Attempt], scheduler: str, now: float, attained: dict[str, float]) -> None:
    def key(item: Attempt) -> tuple:
        waited = now - item.enqueue_time
        if scheduler == "fcfs":
            return fcfs_sort_key(item.enqueue_time, item.attempt_id)
        if scheduler == "srpt":
            return srpt_sort_key(item.remaining, waited, item.enqueue_time, item.attempt_id)
        if scheduler == "runtime-plas":
            return plas_sort_key(attained.get(item.workflow_id, 0.0), waited, item.enqueue_time, item.attempt_id)
        raise ValueError(f"unknown scheduler: {scheduler}")

    queue.sort(key=key)


def replay(
    workload: Workload,
    *,
    policy: str,
    scheduler: str,
    code_version: str = "3.0.0",
    protocol_hash: str = "",
    phase: str = "pilot",
    audit_order: bool = False,
) -> ReplayResult:
    """回放一条 trace。

    输入：与政策无关的工作负载、撤销政策和调度器名。
    输出：旁观者分位数、槽接收者和逐行记录。
    副作用：无。
    失败：未知政策或调度器抛出 ``ValueError``。
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown policy: {policy}")
    if scheduler not in SCHEDULERS:
        raise ValueError(f"unknown scheduler: {scheduler}")

    focal_role = role_for_focal(workload.trigger_class)
    attempts: dict[str, Attempt] = {}
    queue: list[Attempt] = []
    running: list[Attempt] = []
    attained: dict[str, float] = {}
    accepted: set[str] = set()
    arrival_index = 0
    event_done = False
    now = 0.0
    slot_reacquired_by = "idle"
    focal_release_at: float | None = None
    successor_service = 0.0
    restart_service = 0.0
    false_restart_count = 0
    old_attempt_committed = 0
    duplicate_side_effects = 0
    false_cancel = 0
    next_serial = 0
    followup_spawned = False
    dispatch_decisions = 0
    order_differs_from_fcfs = 0

    def make_attempt(
        workflow_id: str,
        role: str,
        service: float,
        arrival: float,
        length_class: str,
        *,
        action_suffix: str,
    ) -> Attempt:
        nonlocal next_serial
        next_serial += 1
        attempt = Attempt(
            workflow_id=workflow_id,
            attempt_id=f"att-{next_serial:04d}",
            role=role,
            service_demand=service,
            remaining=service,
            arrival_time=arrival,
            enqueue_time=now,
            length_class=length_class,
            action_id=f"{workflow_id}:{action_suffix}",
        )
        attempts[attempt.attempt_id] = attempt
        return attempt

    def enqueue(attempt: Attempt) -> None:
        attempt.enqueue_time = now
        queue.append(attempt)

    def commit(attempt: Attempt) -> None:
        nonlocal old_attempt_committed, duplicate_side_effects
        if attempt.revoked or not attempt.eligible:
            if attempt.role in {TARGET, FALSE_TARGET}:
                old_attempt_committed += 1
            return
        if attempt.action_id in accepted:
            duplicate_side_effects += 1
            return
        accepted.add(attempt.action_id)
        attempt.committed = True
        attempt.termination_reason = "completed"

    def release(attempt: Attempt, *, natural: bool) -> None:
        nonlocal focal_release_at, slot_reacquired_by
        if attempt.released_at is not None:
            return
        attempt.released_at = now
        if attempt in running:
            running.remove(attempt)
        served = attempt.service_demand - attempt.remaining
        attained[attempt.workflow_id] = attained.get(attempt.workflow_id, 0.0) + max(0.0, served)
        if attempt.role == focal_role and focal_release_at is None:
            focal_release_at = now
            slot_reacquired_by = "pending"
        if natural and attempt.eligible and not attempt.revoked:
            commit(attempt)
        elif natural:
            attempt.termination_reason = "revoked" if attempt.revoked else "premise_rejected"
        if (
            natural
            and attempt.role == focal_role
            and workload.trigger_class == "harmful"
            and not followup_spawned
        ):
            _spawn_followup()

    def _spawn_followup() -> None:
        nonlocal successor_service, restart_service, false_restart_count, followup_spawned
        if followup_spawned:
            return
        followup_spawned = True
        service = workload.rho * workload.remaining_at_event_s
        role = followup_role(workload.trigger_class)
        if role == SUCCESSOR:
            successor_service = service
        else:
            restart_service = service
            false_restart_count += 1
        follow = make_attempt(
            "wf-focal",
            role,
            service,
            workload.event_time,
            "long",
            action_suffix=role,
        )
        enqueue(follow)

    def dispatch() -> None:
        nonlocal slot_reacquired_by, dispatch_decisions, order_differs_from_fcfs
        while len(running) < workload.slots and queue:
            if audit_order and len(queue) >= 2:
                dispatch_decisions += 1
                fcfs_ids = [item.attempt_id for item in sorted(queue, key=lambda item: fcfs_sort_key(item.enqueue_time, item.attempt_id))]
                plas_ids = [
                    item.attempt_id
                    for item in sorted(
                        queue,
                        key=lambda item: plas_sort_key(
                            attained.get(item.workflow_id, 0.0),
                            now - item.enqueue_time,
                            item.enqueue_time,
                            item.attempt_id,
                        ),
                    )
                ]
                if fcfs_ids != plas_ids:
                    order_differs_from_fcfs += 1
            _sort_queue(queue, scheduler, now, attained)
            nxt = queue.pop(0)
            nxt.started_at = now
            nxt.termination_reason = "running"
            running.append(nxt)
            if slot_reacquired_by == "pending":
                slot_reacquired_by = nxt.role

    arrivals = list(workload.arrivals)
    horizon = workload.horizon_s

    while now < horizon + 1e-9:
        next_times: list[float] = []
        if arrival_index < len(arrivals):
            next_times.append(arrivals[arrival_index].arrival_time)
        if not event_done:
            next_times.append(workload.event_time)
        if running:
            next_times.append(now + min(item.remaining for item in running))
        if not next_times:
            break
        nxt_time = min(next_times)
        if nxt_time > horizon + 1e-12:
            break
        delta = nxt_time - now
        now = nxt_time
        finished: list[Attempt] = []
        for item in running:
            item.remaining -= delta
            if item.remaining <= 1e-9:
                item.remaining = 0.0
                finished.append(item)
        for item in finished:
            item.completed_at = now
            release(item, natural=True)
        if not event_done and abs(now - workload.event_time) <= 1e-9:
            event_done = True
            _apply_event(
                policy=policy,
                workload=workload,
                attempts=attempts,
                running=running,
                release=release,
                spawn=_spawn_followup,
            )
            false_cancel = sum(1 for item in attempts.values() if item.false_cancel)
        while arrival_index < len(arrivals) and arrivals[arrival_index].arrival_time <= now + 1e-12:
            spec = arrivals[arrival_index]
            arrival_index += 1
            role = focal_role if spec.workflow_id == "wf-focal" else BYSTANDER
            created = make_attempt(
                spec.workflow_id,
                role,
                spec.service_s,
                spec.arrival_time,
                spec.length_class,
                action_suffix="primary",
            )
            created.enqueue_time = spec.arrival_time
            queue.append(created)
        dispatch()
        if not running and not queue and arrival_index >= len(arrivals) and event_done:
            break

    if slot_reacquired_by == "pending":
        slot_reacquired_by = "idle"

    for item in list(running) + list(queue):
        if item.completed_at is None:
            item.termination_reason = "censored"

    reclaimed = 0.0
    if focal_release_at is not None and policy in {"G1", "G2"} and workload.trigger_class == "harmful":
        reclaimed = max(0.0, workload.remaining_at_event_s)
    if policy == "G1" and workload.trigger_class == "harmless" and focal_release_at is not None:
        reclaimed = max(0.0, workload.remaining_at_event_s)

    return _summarize(
        workload=workload,
        policy=policy,
        scheduler=scheduler,
        attempts=attempts,
        horizon=horizon,
        code_version=code_version,
        protocol_hash=protocol_hash,
        phase=phase,
        slot_reacquired_by=slot_reacquired_by,
        reclaimed=reclaimed,
        successor_service=successor_service,
        restart_service=restart_service,
        false_restart_count=false_restart_count,
        old_attempt_committed=old_attempt_committed,
        duplicate_side_effects=duplicate_side_effects,
        false_cancel=false_cancel,
        focal_release_at=focal_release_at,
        audit_order=audit_order,
        dispatch_decisions=dispatch_decisions,
        order_differs_from_fcfs=order_differs_from_fcfs,
    )


def _apply_event(
    *,
    policy: str,
    workload: Workload,
    attempts: dict[str, Attempt],
    running: list[Attempt],
    release,
    spawn,
) -> None:
    focal = next((item for item in attempts.values() if item.workflow_id == "wf-focal" and item.role in {TARGET, FALSE_TARGET} and item.released_at is None), None)
    if focal is None or focal.started_at is None:
        return
    harmful = workload.trigger_class == "harmful"
    if policy == "G0":
        if harmful:
            focal.eligible = False
            focal.termination_reason = "running-ineligible"
        return
    if policy == "G2" and not harmful:
        return
    if policy == "G2-delayed-release":
        if not harmful:
            return
        focal.revoked = True
        focal.eligible = False
        focal.release_at_natural_end = True
        focal.termination_reason = "revoked-holding-slot"
        return
    if policy == "G1" and not harmful:
        focal.false_cancel = True
    focal.revoked = True
    focal.eligible = False
    focal.remaining = 0.0
    focal.termination_reason = "revoked"
    release(focal, natural=False)
    if harmful or policy == "G1":
        spawn()


def _summarize(
    *,
    workload: Workload,
    policy: str,
    scheduler: str,
    attempts: dict[str, Attempt],
    horizon: float,
    code_version: str,
    protocol_hash: str,
    phase: str,
    slot_reacquired_by: str,
    reclaimed: float,
    successor_service: float,
    restart_service: float,
    false_restart_count: int,
    old_attempt_committed: int,
    duplicate_side_effects: int,
    false_cancel: int,
    focal_release_at: float | None,
    audit_order: bool = False,
    dispatch_decisions: int = 0,
    order_differs_from_fcfs: int = 0,
) -> ReplayResult:
    bystanders = [item for item in attempts.values() if item.role == BYSTANDER]
    waits: list[float] = []
    flows: list[float] = []
    censored = 0
    completed = 0
    starvation = 0
    rows: list[dict[str, object]] = []
    for item in bystanders:
        if item.started_at is None:
            waits.append(horizon)
            flows.append(horizon - item.arrival_time)
            censored += 1
        else:
            waits.append(item.started_at - item.arrival_time)
            if item.committed and item.completed_at is not None:
                flows.append(item.completed_at - item.arrival_time)
                completed += 1
            else:
                flows.append(horizon - item.arrival_time)
                censored += 1
        if (item.started_at or horizon) - item.arrival_time > 25.0:
            starvation += 1
    for item in attempts.values():
        rows.append(
            {
                "seed": workload.seed,
                "workload_id": f"{workload.load}-{workload.trigger_class}-{workload.timing}-rho{workload.rho}",
                "policy": policy,
                "scheduler": scheduler,
                "workflow_id": item.workflow_id,
                "attempt_id": item.attempt_id,
                "workflow_role": item.role,
                "parent_workflow_id": "wf-focal" if item.role in {SUCCESSOR, RESTART} else "",
                "trigger_class": workload.trigger_class,
                "arrival_time": item.arrival_time,
                "service_demand": item.service_demand,
                "service_started_at": item.started_at,
                "service_completed_at": item.completed_at,
                "revocation_time": workload.event_time if item.revoked else None,
                "slot_released_at": item.released_at,
                "workflow_completed": item.committed,
                "termination_reason": item.termination_reason,
                "false_cancel": item.false_cancel,
                "phase": phase,
            }
        )
    n = len(bystanders) or 1
    focal_committed = any(item.workflow_id == "wf-focal" and item.committed and item.role in {SUCCESSOR, RESTART, FALSE_TARGET, TARGET} and not item.revoked for item in attempts.values())
    summary = {
        "seed": workload.seed,
        "policy": policy,
        "scheduler": scheduler,
        "trigger_class": workload.trigger_class,
        "timing": timing_name(workload),
        "load": workload.load,
        "rho": workload.rho,
        "p_b": workload.p_b,
        "slots": workload.slots,
        "interarrival_s": workload.interarrival_s,
        "arrival_hash": workload.arrival_hash,
        "n_bystanders": len(bystanders),
        "W50": percentile(waits, 50),
        "W90": percentile(waits, 90),
        "W95": percentile(waits, 95),
        "W99": percentile(waits, 99),
        "F95": percentile(flows, 95),
        "bystander_completion_rate": completed / n,
        "censored_wait_count": censored,
        "false_restart_count": false_restart_count,
        "false_cancel": false_cancel,
        "old_attempt_committed": old_attempt_committed,
        "duplicate_side_effects": duplicate_side_effects,
        "slot_reacquired_by": slot_reacquired_by,
        "reclaimed_slot_s": reclaimed,
        "successor_service_s": successor_service,
        "restart_service_s": restart_service,
        "target_recovery_success": focal_committed and old_attempt_committed == 0,
        "starvation_count": starvation,
        "focal_release_at": focal_release_at,
        "horizon_s": horizon,
        "effective_workflow_throughput": completed / horizon if horizon else 0.0,
        "code_version": code_version,
        "protocol_hash": protocol_hash,
        "phase": phase,
    }
    if audit_order:
        summary["dispatch_decisions"] = dispatch_decisions
        summary["order_differs_from_fcfs"] = order_differs_from_fcfs
    summary["timing"] = workload.timing
    return ReplayResult(summary=summary, rows=rows)


def timing_name(workload: Workload) -> str:
    """保留时机字段。输入是工作负载，输出时机名。"""
    return workload.timing
