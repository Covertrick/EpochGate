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
            if deadline is not None and mono <= deadline:
                on_time.add(task_id)
        elif event_type == "action_rejected":
            kind = _reject_kind(reason)
            if kind == "stale":
                stale_candidate += 1
                stale_rejects += 1
            elif kind == "provider":
                provider_rejected += 1
                stale_rejects += 1
            else:
                gateway_rejected += 1
        elif event_type == "decision_wait":
            waits += 1
        elif event_type == "cancel_requested":
            cancels += 1
        elif event_type == "fault_injected":
            faults += 1
        elif event_type == "backend_request":
            backend_requests += 1
        elif event_type == "admission_rejected":
            admission_rejected += 1
        if event_type in {"lease_granted", "decision_wait"} and task_id and task_id not in first_service:
            first_service[task_id] = float(event.get("mono_time") or 0)
        if event_type in {"action_committed", "action_rejected"} and "sample_time_mono" in usage:
            age = float(event.get("mono_time") or 0) - float(usage["sample_time_mono"])
            observation_age_max = max(observation_age_max, age)
        if event_type in {"action_rejected", "cancel_requested", "cancel_acknowledged"} and "tokens" in usage:
            confirmable_waste_tokens += int(usage["tokens"])
    tasks = len(deadlines)
    rate = 0.0 if tasks == 0 else len(on_time) / tasks
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
        "cancels": cancels,
        "confirmable_waste_tokens": confirmable_waste_tokens,
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


def _reject_kind(reason: str) -> str:
    lowered = reason.lower()
    if "stale plan_epoch" in lowered or "stale obs_revision" in lowered or lowered.startswith("stale"):
        return "stale"
    if "version conflict" in lowered:
        return "provider"
    return "gateway"
