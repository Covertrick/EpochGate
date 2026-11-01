"""只读 JSONL trace 的离线指标。不读取演示脚本里的数字。"""

from __future__ import annotations

import json
from pathlib import Path


def score_trace(path: str | Path) -> dict[str, float | int]:
    """从 JSONL 计算按时成功、拒绝分类、观测年龄、取消和可确认浪费 token。

    输入：事件文件路径。每行一个 JSON 对象。
    输出：指标字典。分母是 ``task_registered`` 的任务数。没有采样时刻或 token 时对应项为 0。
    副作用：只读文件。
    失败：文件无法读取或某行不是 JSON 时向上抛。
    """
    deadlines: dict[str, float] = {}
    on_time: set[str] = set()
    stale_rejects = 0
    stale_candidate = 0
    provider_rejected = 0
    gateway_rejected = 0
    waits = 0
    cancels = 0
    observation_age_max = 0.0
    confirmable_waste_tokens = 0
    arrivals: dict[str, float] = {}
    classes: dict[str, str] = {}
    first_service: dict[str, float] = {}
    faults = 0
    backend_requests = 0
    admission_rejected = 0
    ages: list[float] = []
    refresh_count = 0
    refresh_skipped = 0
    window_start: float | None = None
    window_end: float | None = None
    window_success: set[str] = set()
    candidate_stale = 0
    gateway_intercept = 0
    provider_reject = 0
    stale_commit = 0
    scheduler_cpu_s = 0.0
    metadata_bytes = 0
    drain_duration = 0.0
    lease_expired = 0
    unobservable_cancels = 0
    saw_backend_usage = False
    skip_marks = ("same version", "refresh interval", "refresh budget exhausted")
    for event in _load_events(path):
        event_type = str(event.get("event_type", ""))
        identity = event.get("identity") or {}
        task_id = str(identity.get("task_id", ""))
        reason = str(event.get("reason") or "")
        usage = event.get("usage") or {}
        if event_type == "task_registered" and task_id:
            deadlines[task_id] = float(usage.get("deadline_mono"))
            arrivals[task_id] = float(usage.get("arrival_mono") or 0)
            classes[task_id] = str(usage.get("task_class") or "default")
        elif event_type == "task_completed" and reason == "succeeded" and task_id:
            deadline = deadlines.get(task_id)
            mono = float(event.get("mono_time") or 0)
            arrival = arrivals.get(task_id, 0.0)
            if deadline is not None and mono <= deadline:
                on_time.add(task_id)
            if (
                deadline is not None
                and window_start is not None
                and window_end is not None
                and arrival < window_end
                and mono <= min(deadline, window_end)
            ):
                window_success.add(task_id)
        elif event_type == "action_rejected":
            kind = str(usage.get("reject_class") or _reject_class(reason))
            if kind == "candidate_stale":
                candidate_stale += 1
                stale_candidate += 1
            elif kind == "provider_reject":
                provider_reject += 1
                provider_rejected += 1
            elif kind == "stale_commit":
                stale_commit += 1
            else:
                gateway_intercept += 1
                gateway_rejected += 1
        elif event_type == "action_committed" and usage.get("effect_stale"):
            stale_commit += 1
        elif event_type == "decision_wait":
            waits += 1
            if any(mark in reason for mark in skip_marks):
                refresh_skipped += 1
        elif event_type == "observations_refreshed":
            refresh_count += 1
        elif event_type == "run_started":
            if "window_start_mono" in usage and "window_end_mono" in usage:
                window_start = float(usage["window_start_mono"])
                window_end = float(usage["window_end_mono"])
        elif event_type == "cancel_requested":
            cancels += 1
        elif event_type == "fault_injected":
            faults += 1
        elif event_type == "backend_request":
            backend_requests += 1
        elif event_type == "admission_rejected":
            admission_rejected += 1
        elif event_type == "run_finished":
            scheduler_cpu_s = float(usage.get("scheduler_cpu_s") or 0)
            metadata_bytes = int(usage.get("metadata_bytes") or 0)
            drain_duration = float(usage.get("drain_duration") or 0)
        elif event_type == "lease_expired":
            lease_expired += 1
        elif event_type == "cancel_acknowledged" and usage.get("reclaim_observable") is False:
            unobservable_cancels += 1
        if usage.get("usage_source") == "backend":
            saw_backend_usage = True
        if event_type in {"lease_granted", "decision_wait"} and task_id and task_id not in first_service:
            first_service[task_id] = float(event.get("mono_time") or 0)
        if "observation_ages" in usage:
            for item in usage["observation_ages"]:
                age = float(item.get("age_s") or 0)
                ages.append(age)
                observation_age_max = max(observation_age_max, age)
        elif event_type in {"action_committed", "action_rejected"} and "sample_time_mono" in usage:
            age = float(event.get("mono_time") or 0) - float(usage["sample_time_mono"])
            ages.append(age)
            observation_age_max = max(observation_age_max, age)
        if (
            event_type in {"action_rejected", "cancel_requested", "cancel_acknowledged"}
            and usage.get("usage_source") == "backend"
            and "tokens" in usage
        ):
            confirmable_waste_tokens += int(usage["tokens"])
    tasks = len(deadlines)
    rate = 0.0 if tasks == 0 else len(on_time) / tasks
    span = 0.0 if window_start is None or window_end is None else window_end - window_start
    effective_throughput = 0.0 if span <= 0 else len(window_success) / span
    stale_rejects = candidate_stale + gateway_intercept + provider_reject + stale_commit
    waits_by_task = [
        max(0.0, first_service[task_id] - arrivals.get(task_id, 0.0))
        for task_id in first_service
    ]
    by_class: dict[str, list[float]] = {}
    for task_id, waited in (
        (task_id, max(0.0, first_service[task_id] - arrivals.get(task_id, 0.0)))
        for task_id in first_service
    ):
        by_class.setdefault(classes.get(task_id, "default"), []).append(waited)
    class_wait: dict[str, float] = {}
    for name, values in sorted(by_class.items()):
        class_wait[f"wait_p50_{name}"] = _percentile(values, 0.50)
        class_wait[f"wait_p95_{name}"] = _percentile(values, 0.95)
        class_wait[f"wait_p99_{name}"] = _percentile(values, 0.99)
    return {
        "tasks": tasks,
        "on_time_success": len(on_time),
        "cohort_success_rate": rate,
        "stale_rejects": stale_rejects,
        "stale_candidate": stale_candidate,
        "provider_rejected": provider_rejected,
        "gateway_rejected": gateway_rejected,
        "waits": waits,
        "observation_age_max": observation_age_max,
        "observation_age_p50": _percentile(ages, 0.50),
        "observation_age_p95": _percentile(ages, 0.95),
        "observation_age_p99": _percentile(ages, 0.99),
        "window_success": len(window_success),
        "effective_throughput": effective_throughput,
        "drain_duration": drain_duration,
        "candidate_stale": candidate_stale,
        "gateway_intercept": gateway_intercept,
        "provider_reject": provider_reject,
        "stale_commit": stale_commit,
        "scheduler_cpu_s": scheduler_cpu_s,
        "metadata_bytes": metadata_bytes,
        "log_io_bytes": Path(path).stat().st_size,
        "lease_expired": lease_expired,
        "unobservable_cancels": unobservable_cancels,
        "performance_claim": 1 if saw_backend_usage else 0,
        "refresh_count": refresh_count,
        "refresh_skipped": refresh_skipped,
        "cancels": cancels,
        "confirmable_waste_tokens": confirmable_waste_tokens,
        "fault_count": faults,
        "fault_injection_rate": 0.0 if backend_requests == 0 else faults / backend_requests,
        "reject_rate": 0.0 if tasks == 0 else admission_rejected / tasks,
        "wait_max": max(waits_by_task) if waits_by_task else 0.0,
        "wait_p50": _percentile(waits_by_task, 0.50),
        "wait_p95": _percentile(waits_by_task, 0.95),
        "wait_p99": _percentile(waits_by_task, 0.99),
        "wait_gini": _gini(waits_by_task),
        **class_wait,
    }


def _load_events(path: str | Path) -> list[dict]:
    events: list[dict] = []
    text = Path(path).read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        events.append(json.loads(stripped))
    return events


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * fraction
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _gini(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    total = sum(ordered)
    if total == 0:
        return 0.0
    count = len(ordered)
    weighted = sum((index + 1) * value for index, value in enumerate(ordered))
    return (2 * weighted) / (count * total) - (count + 1) / count


def _reject_class(reason: str) -> str:
    lowered = reason.lower()
    if "stale plan_epoch" in lowered or "stale obs_revision" in lowered or lowered.startswith("stale"):
        return "candidate_stale"
    if "version conflict" in lowered or "insufficient" in lowered:
        return "provider_reject"
    if "lease" in lowered or "not eligible" in lowered:
        return "gateway_intercept"
    return "gateway_intercept"


def bootstrap_ci(
    rows: list[dict[str, float | int]],
    keys: list[str],
    *,
    seed: int = 0,
    samples: int = 200,
) -> dict[str, dict[str, float]]:
    """对多种子指标做 bootstrap 95% 区间。

    输入：每个种子一行指标，以及要汇总的键。
    输出：每个键的 low、high、mean。
    副作用：无。
    失败：行数小于 1 时抛出 ``ValueError``。
    """
    import random

    if not rows:
        raise ValueError("bootstrap needs at least one seed")
    rng = random.Random(seed)
    summary: dict[str, dict[str, float]] = {}
    count = len(rows)
    for key in keys:
        values = [float(row.get(key) or 0) for row in rows]
        means: list[float] = []
        for _ in range(samples):
            draw = [values[rng.randrange(count)] for _ in range(count)]
            means.append(sum(draw) / count)
        ordered = sorted(means)
        low = ordered[max(0, int(0.025 * (samples - 1)))]
        high = ordered[min(samples - 1, int(0.975 * (samples - 1)))]
        summary[key] = {"low": low, "high": high, "mean": sum(values) / count}
    return summary
