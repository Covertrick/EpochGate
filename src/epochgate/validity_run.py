"""阶段 C：静态、失效、可恢复任务探针。使用阶段 B 口径。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from epochgate.http_backend import HttpToolBackend
from epochgate.http_streaming import HttpStreamingBackend
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.matrix_run import STREAM_SYSTEM, TIMEOUT_S, _arm_row, goal_for
from epochgate.models import Task
from epochgate.real_run import RESERVE_SYSTEM, RESERVE_TOOL_CHOICE
from epochgate.runtime import Runtime
from epochgate.scheduler import NearAdmissionScheduler
from epochgate.scripted_backend import ScriptShot, ScriptedValidityBackend

DEADLINE_S = 120.0
INITIAL_STOCK = 3


def acceptance_predicates(kind: str, row: dict[str, Any]) -> dict[str, bool]:
    """与 acceptance_report / 离线 pytest 共用的谓词，避免两套口径。"""
    commits = int(row.get("action_commits", row.get("valid_commits", 0)))
    stock = int(row.get("final_stock", -1))
    old = bool(row.get("old_attempt_committed", False))
    once = bool(row.get("side_effect_once", False))
    if kind == "static":
        return {
            "action_commits_eq_1": commits == 1,
            "stock_equals_2": stock == 2,
            "side_effect_once": once and commits == 1 and stock == 2,
            "no_duplicate_commit": commits == 1,
        }
    if kind == "invalidate":
        return {
            "old_attempt_not_committed": not old,
            "action_commits_eq_0": commits == 0,
            "stock_not_reduced_by_old_action": stock == 0,
            "stock_change_fired": bool(row.get("stock_change_fired")),
        }
    if kind == "recoverable":
        restore_delta = int(row.get("restore_delta", 1))
        # 清零后加回 restore_delta，再恰好预约 qty=1 → 期末 = restore_delta - 1
        return {
            "old_attempt_not_committed": not old,
            "action_commits_eq_1": commits == 1,
            "side_effect_once": once and commits == 1,
            "final_stock_one_deduction": stock == restore_delta - 1,
            "stock_change_fired": bool(row.get("stock_change_fired")),
        }
    raise ValueError(f"unknown kind: {kind}")


def predicates_pass(kind: str, row: dict[str, Any]) -> bool:
    return all(acceptance_predicates(kind, row).values())


async def run_static_probe(
    *,
    base_url: str,
    api_key: str,
    model: str,
    seed: int,
    code_version: str,
) -> dict[str, Any]:
    """无外生变化的预约。用非流式后端确认能提交一次。"""
    ids = IdFactory(seed)
    env = InventoryEnvironment(stock=3, schedule=[], seed=seed, pace="step", ids=ids)
    backend = HttpToolBackend(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system_prompt=RESERVE_SYSTEM,
        tool_choice=RESERVE_TOOL_CHOICE,
        parse_retries=1,
    )
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=f"phasec-static-{seed}",
        inline_dispatch=True,
        max_steps=64,
        seed=seed,
    )
    now = time.monotonic()
    started = time.monotonic()
    error = ""
    try:
        await runtime.run(
            [
                Task(
                    task_id="t0",
                    arrival_mono=now,
                    deadline_mono=now + 60.0,
                    goal="reserve one unit",
                )
            ]
        )
    except RuntimeError as exc:
        error = str(exc)
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    commits = sum(1 for event in events if event.event_type == "action_committed")
    rejects = sum(1 for event in events if event.event_type == "action_rejected")
    return {
        "task_kind": "static",
        "pair_id": f"static:{seed}",
        "request_id": f"static:{seed}:S",
        "seed": seed,
        "arm": "S",
        "code_version": code_version,
        "valid_commits": commits,
        "action_rejects": rejects,
        "final_stock": env.stock,
        "stock_delta": 3 - env.stock,
        "side_effect_once": commits == 1 and env.stock == 2,
        "success": commits == 1 and env.stock == 2,
        "end_to_end_latency_s": time.monotonic() - started,
        "error": error,
        "backend_failures": [event.reason or "" for event in events if event.event_type == "backend_failed"],
    }


async def run_stream_scenario(
    *,
    base_url: str,
    api_key: str,
    model: str,
    seed: int,
    arm: str,
    words: int,
    delay_s: float,
    code_version: str,
    task_kind: str,
    restore_after_s: float | None = None,
    restore_delta: int = 0,
) -> dict[str, Any]:
    """流式一臂。失效只降库存；可恢复在延迟后再加回。"""
    if arm not in {"A", "B"}:
        raise ValueError("arm must be A or B")
    ids = IdFactory(seed)
    env = InventoryEnvironment(stock=3, schedule=[], seed=seed, pace="step", ids=ids)
    stamps = {"scheduled": None, "applied": None, "restored": None}
    fired = {"done": False}

    def on_first_delta() -> None:
        if fired["done"]:
            return
        fired["done"] = True
        stamps["applied"] = time.monotonic()
        env.apply_exogenous(-env.stock)
        if restore_after_s is None or restore_delta == 0:
            return

        async def _restore() -> None:
            await asyncio.sleep(restore_after_s)
            stamps["restored"] = time.monotonic()
            env.apply_exogenous(restore_delta)

        asyncio.create_task(_restore())

    backend = HttpStreamingBackend(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system_prompt=STREAM_SYSTEM,
        tool_choice="auto",
        stop_on_cancel=arm == "B",
        on_first_delta=on_first_delta,
        first_delta_delay_s=delay_s,
        ids=ids,
        timeout_s=TIMEOUT_S,
    )
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=f"phasec-{task_kind}-{seed}-{arm}",
        max_steps=8000,
        seed=seed,
    )
    now = time.monotonic()
    started = time.monotonic()
    error = ""
    try:
        await runtime.run(
            [
                Task(
                    task_id="t0",
                    arrival_mono=now,
                    deadline_mono=now + DEADLINE_S,
                    goal=goal_for(words),
                )
            ]
        )
    except RuntimeError as exc:
        error = str(exc)
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    row = _arm_row(
        events,
        seed=seed,
        arm=arm,
        words=words,
        delay_s=delay_s,
        concurrency=1,
        stock_change_fired=fired["done"],
        scheduled_at=stamps["applied"],
        applied_at=stamps["applied"],
        error=error,
        final_stock=env.stock,
        wall_s=time.monotonic() - started,
        code_version=code_version,
        valid_commits=0,
    )
    commits = [event for event in events if event.event_type == "action_committed"]
    rejects = [event for event in events if event.event_type == "action_rejected"]
    target = row["target_attempt_id"]
    old_commit = any(event.identity.get("attempt_id") == target for event in commits)
    row["valid_commits"] = len(commits)
    row["valid_commit"] = len(commits) > 0
    row.update(
        {
            "task_kind": task_kind,
            "stock_restored_at": stamps["restored"],
            "action_commits": len(commits),
            "action_rejects": len(rejects),
            "old_attempt_committed": old_commit,
            "side_effect_once": len(commits) == 1,
            "success": _scenario_success(task_kind, row, commits, old_commit, env.stock),
        }
    )
    if delay_s >= 0 and row["first_delta_at"] is not None:
        row["stock_change_scheduled_at"] = row["first_delta_at"]
    return row


def _scenario_success(kind: str, row: dict[str, Any], commits: list, old_commit: bool, stock: int) -> bool:
    if kind == "invalidate":
        return (not old_commit) and len(commits) == 0 and stock == 0 and bool(row.get("stock_change_fired"))
    if kind == "recoverable":
        return (not old_commit) and len(commits) == 1 and stock == 0 and bool(row.get("stock_change_fired"))
    return False


async def run_deterministic_scenario(
    *,
    task_kind: str,
    seed: int,
    code_version: str,
    restore_after_s: float = 0.05,
    restore_delta: int = 1,
) -> dict[str, Any]:
    """确定性 ScriptedValidityBackend：正确性主证据，不访问 API。"""
    if task_kind not in {"static", "invalidate", "recoverable"}:
        raise ValueError("task_kind must be static|invalidate|recoverable")
    ids = IdFactory(seed)
    env = InventoryEnvironment(stock=INITIAL_STOCK, schedule=[], seed=seed, pace="step", ids=ids)
    stamps: dict[str, float | None] = {"applied": None, "restored": None}
    fired = {"done": False}
    arm = "B"

    def on_trigger() -> None:
        if fired["done"]:
            return
        fired["done"] = True
        stamps["applied"] = time.monotonic()
        env.apply_exogenous(-env.stock)
        if task_kind != "recoverable":
            return
        # 确定性路径：在同一触发里清零再加回，避免「重规划已观测到 0 库存 →
        # insufficient stock 结束任务」的竞态。真实探针用延迟恢复 + 慢流式覆盖该窗口。
        stamps["restored"] = time.monotonic()
        env.apply_exogenous(restore_delta)

    if task_kind == "static":
        scripts = [ScriptShot(token_count=3, stop_on_cancel=True, emit_tool=True)]
        on_cb = None
    elif task_kind == "invalidate":
        # 外生清零后仍允许迟到工具调用交出，由 Gateway 拒绝
        scripts = [
            ScriptShot(
                token_count=4,
                trigger_at=2,
                stop_on_cancel=False,
                emit_tool=True,
                wait_s=0.5,
            )
        ]
        on_cb = on_trigger
    else:
        scripts = [
            ScriptShot(token_count=4, trigger_at=2, stop_on_cancel=True, emit_tool=True, wait_s=0.5),
            ScriptShot(token_count=2, stop_on_cancel=True, emit_tool=True),
        ]
        on_cb = on_trigger
    _ = restore_after_s  # 真实探针用；确定性用同步恢复（见上）

    backend = ScriptedValidityBackend(scripts, on_trigger=on_cb, ids=ids)
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=f"phasec-det-{task_kind}-{seed}",
        max_steps=256,
        seed=seed,
        inline_dispatch=task_kind == "static",
    )
    now = time.monotonic()
    started = time.monotonic()
    error = ""
    try:
        await runtime.run(
            [
                Task(
                    task_id="t0",
                    arrival_mono=now,
                    deadline_mono=now + 30.0,
                    goal="reserve one unit",
                )
            ]
        )
    except RuntimeError as exc:
        error = str(exc)

    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    commits = [event for event in events if event.event_type == "action_committed"]
    rejects = [event for event in events if event.event_type == "action_rejected"]
    if task_kind == "static":
        row: dict[str, Any] = {
            "task_kind": "static",
            "pair_id": f"det-static:{seed}",
            "request_id": f"det-static:{seed}:S",
            "seed": seed,
            "arm": "S",
            "code_version": code_version,
            "evidence_class": "deterministic",
            "valid_commits": len(commits),
            "action_commits": len(commits),
            "action_rejects": len(rejects),
            "final_stock": env.stock,
            "stock_delta": INITIAL_STOCK - env.stock,
            "side_effect_once": len(commits) == 1 and env.stock == 2,
            "old_attempt_committed": False,
            "stock_change_fired": False,
            "end_to_end_latency_s": time.monotonic() - started,
            "error": error,
        }
        row["success"] = predicates_pass("static", row)
        row["predicates"] = acceptance_predicates("static", row)
        return row

    row = _arm_row(
        events,
        seed=seed,
        arm=arm,
        words=0,
        delay_s=0.0,
        concurrency=1,
        stock_change_fired=fired["done"],
        scheduled_at=stamps["applied"],
        applied_at=stamps["applied"],
        error=error,
        final_stock=env.stock,
        wall_s=time.monotonic() - started,
        code_version=code_version,
        valid_commits=0,
    )
    target = row["target_attempt_id"]
    old_commit = any(event.identity.get("attempt_id") == target for event in commits)
    row.update(
        {
            "task_kind": task_kind,
            "evidence_class": "deterministic",
            "pair_id": f"det-{task_kind}:{seed}",
            "request_id": f"det-{task_kind}:{seed}:{arm}",
            "stock_restored_at": stamps["restored"],
            "restore_delta": restore_delta if task_kind == "recoverable" else 0,
            "action_commits": len(commits),
            "action_rejects": len(rejects),
            "old_attempt_committed": old_commit,
            "side_effect_once": len(commits) == 1,
            "valid_commits": len(commits),
            "valid_commit": len(commits) > 0,
        }
    )
    row["predicates"] = acceptance_predicates(task_kind, row)
    row["success"] = predicates_pass(task_kind, row)
    return row


def summarize_acceptance(
    deterministic_rows: list[dict[str, Any]],
    real_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """分区报告：确定性主结论；真实探针只作集成抽检。"""
    by_kind: dict[str, list[dict[str, Any]]] = {"static": [], "invalidate": [], "recoverable": []}
    for row in deterministic_rows:
        by_kind.setdefault(row["task_kind"], []).append(row)
    det_section = {}
    det_all_ok = True
    for kind, rows in by_kind.items():
        if not rows:
            continue
        ok = all(predicates_pass(kind, r) for r in rows)
        det_all_ok = det_all_ok and ok
        det_section[kind] = {
            "n": len(rows),
            "all_predicates_pass": ok,
            "rows": [
                {
                    "seed": r.get("seed"),
                    "success": r.get("success"),
                    "predicates": r.get("predicates") or acceptance_predicates(kind, r),
                    "action_commits": r.get("action_commits", r.get("valid_commits")),
                    "final_stock": r.get("final_stock"),
                    "old_attempt_committed": r.get("old_attempt_committed"),
                    "side_effect_once": r.get("side_effect_once"),
                }
                for r in rows
            ],
        }
    real_section: dict[str, Any] = {"note": "integration sample only; failures must not rewrite deterministic verdict"}
    if real_rows:
        real_by: dict[str, list] = {}
        for row in real_rows:
            real_by.setdefault(row.get("task_kind", "unknown"), []).append(row)
        for kind, rows in real_by.items():
            real_section[kind] = {
                "n": len(rows),
                "success_count": sum(1 for r in rows if r.get("success")),
                "seeds": [r.get("seed") for r in rows],
            }
    return {
        "deterministic": det_section,
        "real_model": real_section,
        "deterministic_all_pass": det_all_ok,
        "verdict": "pass" if det_all_ok else "fail",
    }
