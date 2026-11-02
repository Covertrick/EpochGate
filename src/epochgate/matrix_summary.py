"""把矩阵原始行收成每格汇总。不访问网络。"""

from __future__ import annotations

from statistics import median
from typing import Any


def pair_metrics(arm_a: dict[str, Any], arm_b: dict[str, Any]) -> dict[str, Any]:
    """由同一对的两臂算出节省率和绝对差。

    输入：A 臂行和 B 臂行。
    输出：这一对的差值和两个比率。分母为 0 或未触发时比率为 None。
    副作用：无。
    失败：无。差值为负时原样保留。
    """
    stock_fired = bool(arm_a.get("stock_change_fired")) and bool(arm_b.get("stock_change_fired"))
    missed = arm_a.get("pair_status") == "trigger_not_fired" or arm_b.get("pair_status") == "trigger_not_fired"
    triggered = stock_fired and not missed
    unexpected = arm_a.get("pair_status") == "unexpected_tool_call" or arm_b.get("pair_status") == "unexpected_tool_call"
    failed = _failed(arm_a) or _failed(arm_b)
    usable = triggered and not unexpected and not failed
    post_a = int(arm_a.get("post_revoke_local_deltas") or 0)
    post_b = int(arm_b.get("post_revoke_local_deltas") or 0)
    delta = post_a - post_b
    system = _ratio(
        delta,
        int(arm_a.get("task_total_local_deltas") or 0) + int(arm_b.get("task_total_local_deltas") or 0),
    )
    attempt = _ratio(
        delta,
        int(arm_a.get("revoked_attempt_local_deltas") or 0) + int(arm_b.get("revoked_attempt_local_deltas") or 0),
    )
    if not usable:
        system = None
        attempt = None
    return {
        "seed": arm_a.get("seed"),
        "triggered": triggered,
        "usable": usable,
        "failed": failed,
        "post_revoke_delta": delta if usable else None,
        "system_level_local_delta_saving_ratio": system,
        "revoked_attempt_local_delta_saving_ratio": attempt,
    }


def summarize_cell(rows: list[dict[str, Any]], *, planned_pairs: int) -> dict[str, Any]:
    """按对计算后再取中位数。

    输入：同一格的臂行，以及计划配对数。
    输出：该格汇总。未触发不把节省率写成 0。
    副作用：无。
    失败：缺少成对的臂时，该种子不算有效对。
    """
    by_seed: dict[Any, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_seed.setdefault(row.get("seed"), {})[row.get("arm")] = row
    pairs = []
    for seed, arms in sorted(by_seed.items(), key=lambda item: str(item[0])):
        if "A" in arms and "B" in arms:
            pairs.append(pair_metrics(arms["A"], arms["B"]))
    triggered = [item for item in pairs if item["triggered"]]
    usable = [item for item in pairs if item["usable"]]
    failed = [item for item in pairs if item["failed"]]
    deltas = [item["post_revoke_delta"] for item in usable if item["post_revoke_delta"] is not None]
    return {
        "planned_pairs": planned_pairs,
        "recorded_pairs": len(pairs),
        "triggered_pairs": len(triggered),
        "untriggered_pairs": len(pairs) - len(triggered),
        "trigger_rate": None if not pairs else len(triggered) / len(pairs),
        "valid_pairs": len(usable),
        "failed_pairs": len(failed),
        "positive_saving_pairs": sum(1 for value in deltas if value > 0),
        "negative_saving_pairs": sum(1 for value in deltas if value < 0),
        "A_post_revoke_median": _arm_median(by_seed, "A", "post_revoke_local_deltas", usable),
        "B_post_revoke_median": _arm_median(by_seed, "B", "post_revoke_local_deltas", usable),
        "post_revoke_delta_median": _med(deltas),
        "post_revoke_delta_min": min(deltas) if deltas else None,
        "post_revoke_delta_max": max(deltas) if deltas else None,
        "attempt_saving_ratio_median": _med(
            [item["revoked_attempt_local_delta_saving_ratio"] for item in usable]
        ),
        "attempt_saving_ratio_min": _ends(
            [item["revoked_attempt_local_delta_saving_ratio"] for item in usable], min
        ),
        "attempt_saving_ratio_max": _ends(
            [item["revoked_attempt_local_delta_saving_ratio"] for item in usable], max
        ),
        "system_saving_ratio_median": _med(
            [item["system_level_local_delta_saving_ratio"] for item in usable]
        ),
        "system_saving_ratio_min": _ends(
            [item["system_level_local_delta_saving_ratio"] for item in usable], min
        ),
        "system_saving_ratio_max": _ends(
            [item["system_level_local_delta_saving_ratio"] for item in usable], max
        ),
        "A_task_total_median": _arm_median(by_seed, "A", "task_total_local_deltas", usable),
        "B_task_total_median": _arm_median(by_seed, "B", "task_total_local_deltas", usable),
        "A_replan_median": _arm_median(by_seed, "A", "replan_local_deltas", usable),
        "B_replan_median": _arm_median(by_seed, "B", "replan_local_deltas", usable),
        "A_e2e_median": _arm_median(by_seed, "A", "end_to_end_latency_s", usable),
        "B_e2e_median": _arm_median(by_seed, "B", "end_to_end_latency_s", usable),
        "A_valid_commits": _commits(by_seed, "A"),
        "B_valid_commits": _commits(by_seed, "B"),
    }


def choose_branch(cells: list[dict[str, Any]]) -> str:
    """按预先写好的门槛选项目方向。5% 到 20% 算不确定。

    输入：各格汇总。
    输出：``diagnose``、``joint_admission_next``、``measurement``、``negative`` 或 ``uncertain``。
    副作用：无。
    失败：无。门槛不是显著性阈值。
    """
    usable = [cell for cell in cells if cell.get("valid_pairs")]
    if any((cell.get("negative_saving_pairs") or 0) > (cell.get("positive_saving_pairs") or 0) for cell in usable):
        return "diagnose"
    attempt_values = [cell.get("attempt_saving_ratio_median") for cell in usable if cell.get("attempt_saving_ratio_median") is not None]
    system_values = [cell.get("system_saving_ratio_median") for cell in usable if cell.get("system_saving_ratio_median") is not None]
    if not attempt_values or not system_values:
        return "uncertain"
    if max(attempt_values) < 0.05 and max(system_values) < 0.05:
        return "negative"
    costly = []
    for cell in usable:
        attempt = cell.get("attempt_saving_ratio_median")
        if attempt is None or attempt <= 0.20:
            continue
        a_replan = cell.get("A_replan_median") or 0
        b_replan = cell.get("B_replan_median") or 0
        a_e2e = cell.get("A_e2e_median") or 0
        b_e2e = cell.get("B_e2e_median") or 0
        costly.append(b_replan > a_replan or b_e2e > a_e2e * 1.1)
    if costly and all(costly):
        return "joint_admission_next"
    if any(value > 0.20 for value in attempt_values) and any(value < 0.05 for value in system_values):
        return "uncertain"
    if any(0.05 <= value <= 0.20 for value in attempt_values):
        return "uncertain"
    return "measurement"


def _failed(row: dict[str, Any]) -> bool:
    return row.get("pair_status") in {
        "model_error",
        "request_timeout",
        "cancel_error",
        "protocol_error",
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _med(values: list[float | None]) -> float | None:
    chosen = [value for value in values if value is not None]
    if not chosen:
        return None
    return float(median(chosen))


def _ends(values: list[float | None], pick) -> float | None:
    chosen = [value for value in values if value is not None]
    if not chosen:
        return None
    return float(pick(chosen))


def _arm_median(by_seed: dict, arm: str, field: str, usable: list[dict[str, Any]]) -> float | None:
    seeds = {item["seed"] for item in usable}
    values = []
    for seed, arms in by_seed.items():
        if seed not in seeds or arm not in arms:
            continue
        raw = arms[arm].get(field)
        if isinstance(raw, (int, float)):
            values.append(float(raw))
    return _med(values)


def _commits(by_seed: dict, arm: str) -> int:
    total = 0
    for arms in by_seed.values():
        row = arms.get(arm)
        if row and row.get("valid_commit"):
            total += 1
    return total
