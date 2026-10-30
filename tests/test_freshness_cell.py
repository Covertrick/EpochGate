"""B3 和 J0 在有外生变化的库存上可重放，并且刷新次数分得开。"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from epochgate.backend import FakeBackend
from epochgate.experiment import run_freshness_cell
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.models import Decision, Task
from epochgate.refresh_pool import RefreshPool
from epochgate.runtime import Runtime
from epochgate.scheduler import StrongComboScheduler


def _load(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _signature(path) -> list[tuple[str, str | None, str | None]]:
    rows = []
    for event in _load(path):
        identity = event.get("identity") or {}
        rows.append((event["event_type"], event.get("reason"), identity.get("task_id")))
    return rows


def _lease_before_later_refresh(path) -> bool:
    per: dict[str, list[str]] = {}
    for event in _load(path):
        attempt_id = (event.get("identity") or {}).get("attempt_id")
        if not attempt_id:
            continue
        per.setdefault(attempt_id, []).append(event["event_type"])
    for kinds in per.values():
        if "lease_granted" not in kinds:
            continue
        lease_at = kinds.index("lease_granted")
        if "observations_refreshed" in kinds[lease_at + 1 :]:
            return True
    return False


@pytest.mark.asyncio
async def test_freshness_cell_replays_and_separates_policies(tmp_path) -> None:
    async def run(policy: str, name: str):
        path = tmp_path / name
        await run_freshness_cell(seed=7, policy=policy, jsonl_path=path)
        return path

    b3_first = await run("b3", "b3-a.jsonl")
    b3_second = await run("b3", "b3-b.jsonl")
    assert _signature(b3_first) == _signature(b3_second)
    j0_first = await run("j0", "j0-a.jsonl")
    j0_second = await run("j0", "j0-b.jsonl")
    assert _signature(j0_first) == _signature(j0_second)

    b0 = await run("b0", "b0.jsonl")
    b0_events = _load(b0)
    dispatched_tasks = {
        (event.get("identity") or {}).get("task_id")
        for event in b0_events
        if event["event_type"] == "backend_request"
    }
    refreshed = sum(1 for event in b0_events if event["event_type"] == "observations_refreshed")
    assert refreshed == len(dispatched_tasks)
    b0_for_task = sum(
        1
        for event in b0_events
        if event["event_type"] == "observations_refreshed" and (event.get("identity") or {}).get("task_id") == "t0"
    )
    b3_extra = sum(
        1
        for event in _load(b3_first)
        if event["event_type"] == "observations_refreshed"
        and "b3 event refresh" in (event.get("reason") or "")
        and (event.get("identity") or {}).get("task_id") == "t0"
    )
    assert b3_extra >= b0_for_task + 1
    assert _lease_before_later_refresh(j0_first)
    assert not _lease_before_later_refresh(b3_first)


@pytest.mark.asyncio
async def test_calendar_notification_does_not_refresh(tmp_path) -> None:
    async def count(notify: bool) -> int:
        ids = IdFactory(7)
        env = InventoryEnvironment(stock=3, seed=7, schedule=[], ids=ids)
        if notify:
            env.notify("calendar", "v9")
        runtime = Runtime(
            environment=env,
            backend=FakeBackend(qty=1, max_inflight=1, ids=ids),
            scheduler=StrongComboScheduler(ids=ids),
            ids=ids,
            inline_dispatch=True,
            min_refresh_interval_s=0.05,
        )
        now = time.monotonic()
        await runtime.run(
            [Task(task_id="t0", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")]
        )
        return sum(
            1
            for event in runtime.events.iter_events(run_id=runtime.run_id)
            if event.event_type == "observations_refreshed"
        )

    assert await count(False) == await count(True)


@pytest.mark.asyncio
async def test_budget_one_stops_the_second_version(tmp_path) -> None:
    path = tmp_path / "budget.jsonl"
    with pytest.raises(RuntimeError):
        await run_freshness_cell(seed=7, policy="b3", jsonl_path=path, refresh_budget=1)
    events = _load(path)
    assert any(
        event["event_type"] == "decision_wait" and "refresh budget exhausted" in (event.get("reason") or "")
        for event in events
    )
    refreshed = sum(1 for event in events if event["event_type"] == "observations_refreshed")
    assert refreshed <= 1 + 1
    assert refreshed == 1


@pytest.mark.asyncio
async def test_merged_refresh_counts_once() -> None:
    env = InventoryEnvironment(stock=3, schedule=[], seed=1)
    pool = RefreshPool()
    recipe = env.default_refresh()
    await asyncio.gather(
        pool.observe("left", recipe, env),
        pool.observe("right", recipe, env),
    )
    assert pool.used("left") + pool.used("right") == 1
    assert env.observe_calls == 1


@pytest.mark.asyncio
async def test_admission_reject_records_estimate_source() -> None:
    now = time.monotonic()
    runtime = Runtime(
        environment=InventoryEnvironment(stock=3, schedule=[], seed=1),
        backend=FakeBackend(qty=1),
        scheduler=StrongComboScheduler(),
        service_prior_s=5.0,
    )
    await runtime.run(
        [Task(task_id="late", arrival_mono=now, deadline_mono=now + 0.2, goal="reserve one unit")]
    )
    rejected = [
        event
        for event in runtime.events.iter_events(run_id=runtime.run_id)
        if event.event_type == "admission_rejected"
    ]
    assert rejected
    assert rejected[0].usage["service_source"] == "prior"
    assert rejected[0].usage["estimated_service_s"] == 5.0

    runtime._last_service_s = 1.25
    runtime.registry.register_task(
        Task(task_id="sampled", arrival_mono=now, deadline_mono=now + 30, goal="reserve")
    )
    runtime._do_reject(
        Decision(kind="reject", task_id="sampled", attempt_id="a", reason="infeasible before deadline")
    )
    sampled = [
        event
        for event in runtime.events.iter_events(run_id=runtime.run_id)
        if event.event_type == "admission_rejected" and event.identity.get("task_id") == "sampled"
    ]
    assert sampled[0].usage["service_source"] == "last_sample"
    assert sampled[0].usage["estimated_service_s"] == 1.25
