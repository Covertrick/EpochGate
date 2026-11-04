"""v5.0 统计与行提取：W_slot / T_e2e、trace 内 P95、符号带。"""

from __future__ import annotations

import math
from typing import Any, Sequence


def linear_percentile(values: Sequence[float], p: float) -> float | None:
    """线性插值分位。空序列返回 None。"""
    if not values:
        return None
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    rank = (len(xs) - 1) * p
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return xs[lo]
    w = rank - lo
    return xs[lo] * (1.0 - w) + xs[hi] * w


def sign_tau(delta: float, tau: float) -> int:
    """带零效应带的符号：|d|<tau → 0。"""
    if abs(delta) < tau:
        return 0
    return 1 if delta > 0 else -1


def local_e2e_separated(d_w_slot: float, d_t_e2e: float, tau: float) -> bool:
    """slot 与 e2e 符号分离（至少一侧越出零带）。"""
    s_w = sign_tau(d_w_slot, tau)
    s_t = sign_tau(d_t_e2e, tau)
    return s_w != s_t and (s_w != 0 or s_t != 0)


def extract_role_row(
    *,
    events: list[Any],
    task_id: str,
    workflow_role: str,
    event_id: str,
    arrival_mono: float,
    backend_stop_status: str = "unknown",
) -> dict[str, Any]:
    """从 Runtime 事件提取单工作流行。黑盒路径 backend_stop_status 必须为 unknown。"""
    if backend_stop_status != "unknown" and backend_stop_status not in {
        "stopped",
        "delayed",
        "timeout",
        "observed_aborted",
    }:
        raise ValueError(f"invalid backend_stop_status: {backend_stop_status}")

    def _first(etype: str, *, attempt: str | None = None) -> Any | None:
        for ev in events:
            if ev.event_type != etype:
                continue
            if ev.identity.get("task_id") != task_id:
                continue
            if attempt is not None and ev.identity.get("attempt_id") != attempt:
                continue
            return ev
        return None

    def _last_token_mono() -> float | None:
        last = None
        for ev in events:
            if ev.identity.get("task_id") != task_id:
                continue
            if ev.event_type in {"token_delta", "tool_call_delta", "backend_delta"}:
                last = float(ev.mono_time)
        return last

    slot_ev = _first("slot_acquired")
    # first token: token_delta or tool_call_delta
    ttft_ev = None
    for ev in events:
        if ev.identity.get("task_id") != task_id:
            continue
        if ev.event_type in {"token_delta", "tool_call_delta", "backend_delta"}:
            ttft_ev = ev
            break
    done_ev = _first("task_completed")
    slot_rel = _first("slot_released")
    elig = _first("eligibility_revoked")
    cancel_req = _first("cancel_requested")
    conn = _first("connection_closed")
    local_stop = _first("local_read_stopped")
    provider_abort = _first("provider_abort_not_issued")

    slot_acquired = None if slot_ev is None else float(slot_ev.mono_time)
    ttft = None if ttft_ev is None else float(ttft_ev.mono_time)
    completed = None if done_ev is None else float(done_ev.mono_time)
    last_token = _last_token_mono()

    w_slot = None if slot_acquired is None else max(0.0, slot_acquired - arrival_mono)
    w_ttft = None if ttft is None else max(0.0, ttft - arrival_mono)
    t_e2e = None if completed is None else max(0.0, completed - arrival_mono)

    return {
        "task_id": task_id,
        "workflow_role": workflow_role,
        "event_id": event_id,
        "arrival_mono": arrival_mono,
        "slot_acquired_mono": slot_acquired,
        "ttft_mono": ttft,
        "last_token_mono": last_token,
        "completed_mono": completed,
        "slot_released_mono": None if slot_rel is None else float(slot_rel.mono_time),
        "eligibility_revoked_mono": None if elig is None else float(elig.mono_time),
        "cancel_requested_mono": None if cancel_req is None else float(cancel_req.mono_time),
        "connection_closed_mono": None if conn is None else float(conn.mono_time),
        "local_read_stopped_mono": None if local_stop is None else float(local_stop.mono_time),
        "provider_cancel_ack": False,
        "provider_abort_not_issued": provider_abort is not None,
        "backend_stop_status": backend_stop_status,
        "residual_lower_s": None,
        "residual_upper_s": None,
        "W_slot_s": w_slot,
        "W_ttft_s": w_ttft,
        "T_e2e_s": t_e2e,
        "pre_revoke_completed": bool(
            completed is not None
            and elig is not None
            and completed < float(elig.mono_time)
        ),
    }


def summarize_bystanders(rows: Sequence[dict[str, Any]], *, tau: float) -> dict[str, Any]:
    """一条 trace 内 bystander 的 P95 与完成计数。B<2 时不报 P95。"""
    bys = [r for r in rows if r.get("workflow_role") == "bystander"]
    w_vals = [float(r["W_slot_s"]) for r in bys if r.get("W_slot_s") is not None]
    e_vals = [float(r["T_e2e_s"]) for r in bys if r.get("T_e2e_s") is not None]
    incomplete = sum(1 for r in bys if r.get("T_e2e_s") is None)
    n = len(bys)
    p95_w = linear_percentile(w_vals, 0.95) if n >= 2 and len(w_vals) >= 2 else None
    p95_e = linear_percentile(e_vals, 0.95) if n >= 2 and len(e_vals) >= 2 else None
    return {
        "bystander_count": n,
        "incomplete_count": incomplete,
        "W_slot_p95_s": p95_w,
        "T_e2e_p95_s": p95_e,
        "tau_s": tau,
    }
