"""负例只检查机制会不会退回，不把成功数写成性能。"""

from __future__ import annotations

import json
import time

import pytest

from epochgate.backend import FakeBackend
from epochgate.change_stream import ChangeStream
from epochgate.experiment import run_cell, shared_tasks
from epochgate.inventory import InventoryEnvironment, plan_exogenous
from epochgate.models import ChangeBatch, ChangeEvent
from epochgate.runtime import Runtime
from epochgate.scheduler import JointScheduler, NearComboScheduler


@pytest.mark.asyncio
async def test_drop_reorder_and_disconnect(tmp_path) -> None:
    env = InventoryEnvironment(stock=3, schedule=[(0.1, -1), (0.2, 1)], seed=1, pace="step")
    await env.changes("0")
    page = await env.changes("1")
    assert page.events
    dropped = await ChangeStream(env, mode="drop", seed=1).changes("0")
    assert dropped.stream_open is False or dropped.events == []
    shuffled = ChangeStream(InventoryEnvironment(stock=1, schedule=[], seed=1), mode="reorder", seed=1)
    same = await shuffled.changes("0")
    assert same.stream_open is True
    closed = await ChangeStream(InventoryEnvironment(stock=1, schedule=[], seed=1), mode="disconnect", seed=1).changes("0")
    assert closed.stream_open is False
    assert closed.events == []
    assert isinstance(page, ChangeBatch)


@pytest.mark.asyncio
async def test_static_joint_observes_once_per_task(tmp_path) -> None:
    path = tmp_path / "static.jsonl"
    scored = await run_cell(
        seed=3,
        policy="j1",
        jsonl_path=path,
        schedule=[],
        change_mode=None,
    )
    assert scored["cancels"] == 0
    text = path.read_text(encoding="utf-8")
    assert text.count("observations_refreshed") == 3


@pytest.mark.asyncio
async def test_missing_notifications_mark_stream_closed(tmp_path) -> None:
    path = tmp_path / "dark.jsonl"
    try:
        await run_cell(seed=3, policy="j0", jsonl_path=path, schedule=[], change_mode="disconnect")
    except RuntimeError:
        pass
    assert "stream_closed" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_low_load_joint_does_not_lose_to_b0(tmp_path) -> None:
    b0 = tmp_path / "b0.jsonl"
    j1 = tmp_path / "j1.jsonl"
    await run_cell(seed=5, policy="b0", jsonl_path=b0, max_inflight=8, schedule=[])
    await run_cell(seed=5, policy="j1", jsonl_path=j1, max_inflight=8, schedule=[])
    assert _succeeded(j1) >= _succeeded(b0)


@pytest.mark.asyncio
async def test_uncancellable_backend_does_not_spam_cancel(tmp_path) -> None:
    path = tmp_path / "nocancel.jsonl"
    await run_cell(
        seed=4,
        policy="j1",
        jsonl_path=path,
        supports_cancel=False,
        schedule=plan_exogenous(4, 3),
    )
    assert path.read_text(encoding="utf-8").count("cancel_requested") == 0


@pytest.mark.asyncio
async def test_burst_stays_inside_budget_and_interval(tmp_path) -> None:
    path = tmp_path / "burst.jsonl"
    schedule = [(0.01, -1) for _ in range(6)]
    try:
        await run_cell(
            seed=8,
            policy="b3",
            jsonl_path=path,
            schedule=schedule,
            refresh_budget=2,
            min_refresh_interval_s=0.05,
        )
    except RuntimeError:
        pass
    text = path.read_text(encoding="utf-8")
    assert "refresh interval" in text or "refresh budget exhausted" in text
    per: dict[str, int] = {}
    for line in text.splitlines():
        event = json.loads(line)
        if event["event_type"] != "observations_refreshed":
            continue
        task_id = (event.get("identity") or {}).get("task_id") or ""
        per[task_id] = per.get(task_id, 0) + 1
    assert per
    assert max(per.values()) <= 2


@pytest.mark.asyncio
async def test_fair_share_serves_the_long_task_first(tmp_path) -> None:
    now = time.monotonic()
    tasks = shared_tasks(now, loose_long=True)

    async def first(policy: str, fair_every_n: int) -> str:
        path = tmp_path / f"{policy}-{fair_every_n}.jsonl"
        await run_cell(
            seed=2,
            policy=policy,
            jsonl_path=path,
            tasks=tasks,
            schedule=[],
            fair_every_n=fair_every_n,
            max_inflight=1,
        )
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event["event_type"] == "backend_request":
                return event["identity"]["task_id"]
        raise AssertionError("no dispatch")

    assert await first("j0", 1) == "long"
    assert await first("j1-fair-off", 0) == "short"


def test_scheduler_symbols_exist() -> None:
    assert JointScheduler is not None
    assert NearComboScheduler is not None


def _succeeded(path) -> int:
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["event_type"] == "task_completed" and event.get("reason") == "succeeded":
            total += 1
    return total
