"""4.0 之后的策略回放。

谓词真值来自合成工作负载的 harmful/harmless，不是语义检测器。
AlwaysContinue 占槽到自然结束（G0）。AlwaysAbort 在事件点放槽并重做（G1）。
defer 占满剩余服务，但不提交、也不把重做放进队列。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from epochgate.decision_policy import DecisionInput, RuntimeAction, decide
from epochgate.replay import replay
from epochgate.workload import build_workload

PROTOCOL = "followup-0-6"
POLICIES = ("AlwaysContinue", "AlwaysAbort", "PredicateOnly", "Oracle", "UncertaintyAwarePolicy")
SCHEDULERS = ("fcfs", "runtime-plas", "srpt")
TRIGGERS = ("harmful", "harmless")
TIMINGS = ("early", "mid", "late")
LOADS = (("low", 20.0), ("medium", 1.9573), ("high", 1.7824))
SLOTS = (2, 4, 8)
RHOS = (0.5, 0.75, 1.0, 1.25, 2.0)
ERRORS = (0.0, 0.10, 0.25)
SEEDS = tuple(range(5000, 5050))

# 不含策略。种子放在最后，同一个 cell 的 50 条在数组里连续。
AXES = (TRIGGERS, TIMINGS, LOADS, SLOTS, RHOS, ERRORS, SCHEDULERS, SEEDS)
SIZES = tuple(len(axis) for axis in AXES)
N_TRACES = 1
for _size in SIZES:
    N_TRACES *= _size


def unravel(index: int) -> tuple:
    """把平坦下标还原成各轴坐标。"""
    coords: list[int] = []
    for size in reversed(SIZES):
        coords.append(index % size)
        index //= size
    coords.reverse()
    return tuple(coords)


def policy_replay_arm(
    policy: str,
    trigger: str,
    rho: float,
    remaining: float,
    error: float,
) -> tuple[str, str]:
    """返回 (回放臂, 决策标签)。

    回放臂只决定槽占用。决策标签单独记录，带内的 defer 不记成错误动作。
    """
    valid = trigger == "harmless"
    if policy == "AlwaysContinue":
        return "G0", RuntimeAction.CONTINUE.value
    if policy == "AlwaysAbort":
        return "G1", RuntimeAction.ABORT_AND_REPLAN.value
    if policy == "PredicateOnly":
        if not valid:
            return "G1", RuntimeAction.ABORT_AND_REPLAN.value
        return "G0", RuntimeAction.CONTINUE.value
    if policy == "Oracle":
        if not valid or rho < 1.0:
            return "G1", RuntimeAction.ABORT_AND_REPLAN.value
        return "G0", RuntimeAction.CONTINUE.value
    if policy != "UncertaintyAwarePolicy":
        raise ValueError(f"unknown policy: {policy}")
    result = decide(_uap_input(valid, rho, remaining, error))
    if result.action is RuntimeAction.ABORT_AND_REPLAN:
        return "G1", result.action.value
    if result.action is RuntimeAction.DEFER:
        return "defer", result.action.value
    if result.action is RuntimeAction.CONTINUE:
        return "G0", result.action.value
    raise ValueError(f"matrix does not form a candidate, got {result.action}")


def _uap_input(valid: bool, rho: float, remaining: float, error: float) -> DecisionInput:
    return DecisionInput(
        predicate_valid=valid,
        candidate_formed=False,
        remaining_service_estimate=remaining * (1.0 + error),
        restart_service_estimate=(rho * remaining) * (1.0 - error),
        remaining_service_error=error * remaining,
        restart_service_error=error * rho * remaining,
        safety_buffer=0.05 * remaining,
    )


@dataclass(frozen=True)
class TraceMetrics:
    w95: float
    w99: float
    focal_release_at: float
    completion: float
    old_attempt_committed: int
    duplicate_side_effects: int
    restart_service_s: float
    reclaimed_slot_s: float


def replay_policy(
    *,
    seed: int,
    trigger: str,
    timing: str,
    load: str,
    interarrival_s: float,
    slots: int,
    rho: float,
    error: float,
    policy: str,
    scheduler: str,
) -> tuple[str, str, TraceMetrics]:
    workload = build_workload(
        seed=seed,
        trigger_class=trigger,
        timing=timing,
        rho=rho,
        load=load,
        interarrival_s=interarrival_s,
        slots=slots,
    )
    arm, action = policy_replay_arm(policy, trigger, rho, workload.remaining_at_event_s, error)
    result = replay(workload, policy=arm, scheduler=scheduler, phase="followup", protocol_hash=PROTOCOL)
    summary = result.summary
    release = summary["focal_release_at"]
    metrics = TraceMetrics(
        w95=float(summary["W95"]),
        w99=float(summary["W99"]),
        focal_release_at=-1.0 if release is None else float(release),
        completion=float(summary["bystander_completion_rate"]),
        old_attempt_committed=int(summary["old_attempt_committed"]),
        duplicate_side_effects=int(summary["duplicate_side_effects"]),
        restart_service_s=float(summary["restart_service_s"]),
        reclaimed_slot_s=float(summary["reclaimed_slot_s"]),
    )
    return arm, action, metrics


def run_index(index: int) -> tuple[int, list[tuple]]:
    """一条 trace 上跑完全部策略。工作负载只建一次。"""
    trigger_i, timing_i, load_i, slots_i, rho_i, error_i, scheduler_i, seed_i = unravel(index)
    trigger = TRIGGERS[trigger_i]
    timing = TIMINGS[timing_i]
    load, interarrival = LOADS[load_i]
    slots = SLOTS[slots_i]
    rho = RHOS[rho_i]
    error = ERRORS[error_i]
    scheduler = SCHEDULERS[scheduler_i]
    seed = SEEDS[seed_i]
    workload = build_workload(
        seed=seed,
        trigger_class=trigger,
        timing=timing,
        rho=rho,
        load=load,
        interarrival_s=interarrival,
        slots=slots,
    )
    rows: list[tuple] = []
    for policy in POLICIES:
        arm, action = policy_replay_arm(policy, trigger, rho, workload.remaining_at_event_s, error)
        result = replay(workload, policy=arm, scheduler=scheduler, phase="followup", protocol_hash=PROTOCOL)
        summary = result.summary
        release = summary["focal_release_at"]
        rows.append(
            (
                policy,
                arm,
                action,
                float(summary["W95"]),
                float(summary["W99"]),
                -1.0 if release is None else float(release),
                float(summary["bystander_completion_rate"]),
                int(summary["old_attempt_committed"]),
                int(summary["duplicate_side_effects"]),
                float(summary["restart_service_s"]),
                float(summary["reclaimed_slot_s"]),
            )
        )
    return index, rows


def bootstrap_mean(values: list[float], *, replicates: int = 10_000, seed: int = 0) -> dict[str, float]:
    """以 trace 为重采样单位，对配对差的均值做 10,000 次有放回抽样。"""
    if not values:
        raise ValueError("bootstrap needs at least one paired difference")
    rng = random.Random(seed)
    n = len(values)
    means = [sum(rng.choices(values, k=n)) / n for _ in range(replicates)]
    means.sort()

    def _at(fraction: float) -> float:
        return means[int(fraction * (len(means) - 1))]

    return {
        "n": n,
        "mean": sum(values) / n,
        "p2_5": _at(0.025),
        "p97_5": _at(0.975),
        "replicates": replicates,
        "seed": seed,
    }


def beats(interval: dict[str, float]) -> bool:
    """区间下界大于 0 才算这条负载上优于对照。"""
    return interval["p2_5"] > 0 and interval["mean"] > 0
