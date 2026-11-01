"""工具名留在环境里；外生库存会在推理期间变化。"""

from __future__ import annotations

import time

import pytest

from epochgate.backend import FakeBackend
from epochgate.environment import FakeEnvironment
from epochgate.inventory import InventoryEnvironment, plan_exogenous
from epochgate.models import RefreshRecipe, Task, TaskStatus, ToolSpec
from epochgate.runtime import Runtime
from epochgate.scheduler import (
    FcfsEnqueueScheduler,
    HappyPathScheduler,
    NearAdmissionScheduler,
    RuleScheduler,
)


class HoldEnvironment(FakeEnvironment):
    """用另一套工具名证明运行时不认识具体名字。"""

    def tool_catalog(self) -> list[ToolSpec]:
        return [
            ToolSpec(name="peek", role="read", description="Look"),
            ToolSpec(name="hold", role="effect", description="Hold"),
        ]

    def default_refresh(self) -> RefreshRecipe:
        return RefreshRecipe(read_tool="peek", sources=[self.source_id], merge_key="box")


def test_exogenous_plan_is_stable_for_a_seed() -> None:
    assert plan_exogenous(3) == plan_exogenous(3)
    assert plan_exogenous(3) != plan_exogenous(4)


@pytest.mark.asyncio
async def test_due_schedule_changes_stock() -> None:
    env = InventoryEnvironment(stock=4, schedule=[(0.0, -1)], seed=1)
    batch = await env.changes("0")
    assert env.stock == 3
    assert len(batch.events) == 1
    assert env.planned_offsets() == [(0.0, -1)]
    again = await env.changes(batch.next_cursor)
    assert again.events == []


@pytest.mark.asyncio
async def test_alternate_tool_names_use_same_runtime_and_scheduler() -> None:
    env = HoldEnvironment(stock=3)
    runtime = Runtime(
        environment=env,
        backend=FakeBackend(action_type="hold"),
        scheduler=HappyPathScheduler(),
    )
    now = time.monotonic()
    result = await runtime.run_task(
        Task(
            task_id="t-hold-name",
            arrival_mono=now,
            deadline_mono=now + 30.0,
            goal="hold one",
        )
    )
    assert result.status == TaskStatus.SUCCEEDED
    assert env.stock == 2


def test_b0_ignores_dirty_but_rule_refreshes() -> None:
    now = time.monotonic()
    from epochgate.models import (
        AttemptState,
        AttemptView,
        BackendCapacityView,
        SchedulerSnapshot,
        TaskStatus,
        TaskView,
    )

    snapshot = SchedulerSnapshot(
        now_mono=now,
        run_id="r",
        tasks=[
            TaskView(
                task_id="t",
                status=TaskStatus.RUNNING,
                goal="g",
                plan_epoch=0,
                obs_revision=1,
                arrival_mono=now,
                deadline_mono=now + 10,
            )
        ],
        attempts=[
            AttemptView(
                attempt_id="a",
                task_id="t",
                plan_epoch=0,
                obs_revision=1,
                state=AttemptState.QUEUED,
                eligible=True,
                has_observations=True,
                sources_dirty=True,
            )
        ],
        observations=[],
        capacity=BackendCapacityView(free_slots=1, supports_cancel=True),
        default_refresh=RefreshRecipe(read_tool="peek", sources=["box"]),
        dirty_sources=["box"],
    )
    b0 = FcfsEnqueueScheduler().decide(snapshot)
    assert all(decision.kind != "refresh" for decision in b0)
    assert any(decision.kind == "admit" for decision in b0)
    joint = RuleScheduler().decide(snapshot)
    assert any(decision.kind == "refresh" for decision in joint)


@pytest.mark.asyncio
async def test_stale_commit_during_inference_can_redecide() -> None:
    env = InventoryEnvironment(stock=5, schedule=[], seed=2)
    fired = {"done": False}

    def change_once() -> None:
        if not fired["done"]:
            fired["done"] = True
            env.apply_exogenous(-1)

    backend = FakeBackend(
        delay_s=0.3,
        supports_cancel=True,
        max_inflight=1,
        on_start=change_once,
    )
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(),
        max_steps=200,
    )
    now = time.monotonic()
    result = await runtime.run_task(
        Task(
            task_id="t-dirty",
            arrival_mono=now,
            deadline_mono=now + 30.0,
            goal="reserve one unit",
        )
    )
    types = [event.event_type for event in runtime.events.iter_events(run_id=runtime.run_id)]
    assert "cancel_requested" not in types
    assert "action_rejected" in types
    assert types.count("observations_refreshed") >= 2
    assert result.status == TaskStatus.SUCCEEDED
    assert env.stock == 3


@pytest.mark.asyncio
async def test_premise_failure_cancels_and_late_result_is_rejected() -> None:
    env = InventoryEnvironment(stock=1, schedule=[], seed=4)
    fired = {"done": False}

    def change_once() -> None:
        if not fired["done"]:
            fired["done"] = True
            env.apply_exogenous(-1)

    backend = FakeBackend(
        delay_s=0.3,
        supports_cancel=True,
        max_inflight=1,
        on_start=change_once,
    )
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(),
        max_steps=200,
    )
    now = time.monotonic()
    result = await runtime.run_task(
        Task(
            task_id="t-cancel",
            arrival_mono=now,
            deadline_mono=now + 30.0,
            goal="reserve one unit",
        )
    )
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    types = [event.event_type for event in events]
    assert "cancel_requested" in types
    assert "cancel_acknowledged" in types
    assert types.index("cancel_requested") < types.index("cancel_acknowledged")
    assert "action_rejected" in types
    assert types.count("observations_refreshed") >= 2
    evidence = [event.reason or "" for event in events if event.event_type == "cancel_acknowledged"]
    assert evidence
    assert "gpu" not in evidence[0].lower()
    acknowledged = next(event for event in events if event.event_type == "cancel_acknowledged")
    assert acknowledged.usage["grade"] == "ACKNOWLEDGED"
    assert acknowledged.usage["reclaim_observable"] is False
    assert "resource_reclaimed" not in types
    assert backend.capabilities().resource_reclaim_observable is False
    assert result.status == TaskStatus.REJECTED
    assert env.stock == 0
