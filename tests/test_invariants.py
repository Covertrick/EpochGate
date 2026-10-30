"""I2、I3、I4、I7，以及失效观测后的提示词重建。"""

from __future__ import annotations

import time

import pytest

from epochgate.backend import FakeBackend
from epochgate.inventory import InventoryEnvironment
from epochgate.models import (
    AdmissionLease,
    CandidateAction,
    Decision,
    Observation,
    RefreshRecipe,
    Task,
    TaskStatus,
)
from epochgate.refresh_pool import RefreshNotAllowed, RefreshPool
from epochgate.runtime import Runtime
from epochgate.scheduler import EdfFixedScheduler, NearAdmissionScheduler


def _candidate(task_id: str, attempt_id: str, *, action_id: str, observation_ids: list[str]) -> CandidateAction:
    return CandidateAction(
        action_id=action_id,
        task_id=task_id,
        attempt_id=attempt_id,
        plan_epoch=0,
        obs_revision=0,
        action_type="reserve",
        parameters={"qty": 1},
        observation_ids=observation_ids,
    )


@pytest.mark.asyncio
async def test_i2_missing_observation_id_rejects_without_fallback() -> None:
    env = InventoryEnvironment(stock=4, schedule=[], seed=1)
    runtime = Runtime(environment=env, backend=FakeBackend(), scheduler=NearAdmissionScheduler())
    now = time.monotonic()
    task = runtime.registry.register_task(
        Task(task_id="t-i2", arrival_mono=now, deadline_mono=now + 30, goal="reserve")
    )
    attempt = runtime.registry.begin_attempt(task.task_id)
    stored = runtime.observations.record(
        Observation(
            observation_id="obs-real",
            source_id="inventory",
            source_version="v9",
            sample_time_mono=now,
            received_time_mono=now,
            payload={"stock": 4},
        ),
        task_id=task.task_id,
    )
    lease = AdmissionLease(
        lease_id="lease-i2",
        attempt_id=attempt.attempt_id,
        earliest_start_mono=now,
        expires_at_mono=now + 60,
    )
    result = await runtime.gateway.submit(
        _candidate(
            task.task_id,
            attempt.attempt_id,
            action_id="act-i2",
            observation_ids=[stored.observation_id, "obs-missing"],
        ),
        lease,
    )
    assert result.accepted is False
    assert any("obs-missing" in reason for reason in result.reasons)
    assert env.stock == 4
    assert env.version == "v1"


@pytest.mark.asyncio
async def test_i3_check_then_mutate_then_commit_conflicts() -> None:
    env = InventoryEnvironment(stock=4, schedule=[], seed=1)
    batch = await env.observe(env.default_refresh())
    obs = batch.observations[0]
    candidate = CandidateAction(
        action_id="act-i3",
        task_id="t",
        attempt_id="a",
        plan_epoch=0,
        obs_revision=0,
        action_type="reserve",
        parameters={"qty": 1},
        observation_ids=[obs.observation_id],
    )
    checked = await env.check(candidate)
    assert checked.ok is True
    env.apply_exogenous(-1)
    result = await env.commit(
        candidate,
        {"source_id": obs.source_id, "source_version": obs.source_version},
    )
    assert result.accepted is False
    assert "version conflict" in result.reasons
    assert env.stock == 3


@pytest.mark.asyncio
async def test_i4_cancel_events_stay_separate_and_late_completion_is_rejected() -> None:
    env = InventoryEnvironment(stock=1, schedule=[], seed=5)
    fired = {"done": False}

    def change_once() -> None:
        if not fired["done"]:
            fired["done"] = True
            env.apply_exogenous(-1)

    runtime = Runtime(
        environment=env,
        backend=FakeBackend(delay_s=0.3, supports_cancel=True, max_inflight=1, on_start=change_once),
        scheduler=NearAdmissionScheduler(),
        max_steps=200,
    )
    now = time.monotonic()
    result = await runtime.run_task(
        Task(task_id="t-i4", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")
    )
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    types = [event.event_type for event in events]
    assert types.index("cancel_requested") < types.index("cancel_acknowledged")
    ack = next(event for event in events if event.event_type == "cancel_acknowledged")
    assert "gpu" not in (ack.reason or "").lower()
    assert any(event.event_type == "action_rejected" for event in events)
    assert result.status == TaskStatus.REJECTED


@pytest.mark.asyncio
async def test_i7_repeated_commit_is_idempotent_and_terminal_task_emits_once() -> None:
    env = InventoryEnvironment(stock=4, schedule=[], seed=1)
    batch = await env.observe(env.default_refresh())
    obs = batch.observations[0]
    candidate = CandidateAction(
        action_id="act-i7",
        task_id="t-i7",
        attempt_id="a",
        plan_epoch=0,
        obs_revision=0,
        action_type="reserve",
        parameters={"qty": 1},
        observation_ids=[obs.observation_id],
    )
    conditions = {"source_id": obs.source_id, "source_version": obs.source_version}
    first = await env.commit(candidate, conditions)
    second = await env.commit(candidate, conditions)
    assert first.accepted is True
    assert second.commit_id == first.commit_id
    assert env.stock == 3

    runtime = Runtime(environment=env, backend=FakeBackend(), scheduler=NearAdmissionScheduler())
    now = time.monotonic()
    runtime.registry.register_task(
        Task(task_id="t-i7", arrival_mono=now, deadline_mono=now + 10, goal="done")
    )
    runtime.registry.complete_task("t-i7", TaskStatus.SUCCEEDED)
    runtime.registry.complete_task("t-i7", TaskStatus.FAILED)
    completed = [
        event
        for event in runtime.events.iter_events(run_id=runtime.run_id)
        if event.event_type == "task_completed"
    ]
    assert len(completed) == 1
    assert completed[0].reason == "succeeded"


@pytest.mark.asyncio
async def test_invalidated_refresh_rebuilds_the_session() -> None:
    env = InventoryEnvironment(stock=4, schedule=[], seed=3)
    runtime = Runtime(environment=env, backend=FakeBackend(), scheduler=NearAdmissionScheduler())
    now = time.monotonic()
    task = runtime.registry.register_task(
        Task(task_id="t-rebuild", arrival_mono=now, deadline_mono=now + 30, goal="reserve")
    )
    attempt = runtime.registry.begin_attempt(task.task_id)
    decision = Decision(
        kind="refresh",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        reason="rebuild",
        refresh_recipe=env.default_refresh(),
    )
    await runtime._finish_refresh(decision, was_dirty=False)
    runtime._sessions[attempt.attempt_id] = [{"role": "tool", "content": "old tool result"}]
    env.apply_exogenous(0)
    await runtime._finish_refresh(decision, was_dirty=True)
    session = runtime._sessions[attempt.attempt_id]
    assert len(session) == 1
    assert session[0]["role"] == "user"
    assert env.version in session[0]["content"]
    assert "inventory" in session[0]["content"]
    assert "old tool result" not in session[0]["content"]


@pytest.mark.asyncio
async def test_near_admits_before_refresh_finishes_and_b1_does_not() -> None:
    now = time.monotonic()
    task = Task(task_id="t-order", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")

    async def order_for(scheduler) -> list[str]:
        runtime = Runtime(
            environment=InventoryEnvironment(stock=2, schedule=[], seed=1),
            backend=FakeBackend(),
            scheduler=scheduler,
        )
        await runtime.run([task])
        return [event.event_type for event in runtime.events.iter_events(run_id=runtime.run_id)]

    near = await order_for(NearAdmissionScheduler())
    b1 = await order_for(EdfFixedScheduler())
    assert near.index("lease_granted") < near.index("observations_refreshed")
    assert b1.index("observations_refreshed") < b1.index("lease_granted")


@pytest.mark.asyncio
async def test_read_tool_outside_catalog_is_rejected() -> None:
    env = InventoryEnvironment(stock=3, schedule=[], seed=1)
    pool = RefreshPool()
    recipe = RefreshRecipe(read_tool="drop_table", sources=["inventory"], merge_key="inventory")
    with pytest.raises(RefreshNotAllowed):
        await pool.observe("t-i5", recipe, env)
    assert pool.used("t-i5") == 0
    assert env.observe_calls == 0

    runtime = Runtime(environment=env, backend=FakeBackend(), scheduler=NearAdmissionScheduler())
    now = time.monotonic()
    task = runtime.registry.register_task(
        Task(task_id="t-i5", arrival_mono=now, deadline_mono=now + 10, goal="reserve")
    )
    attempt = runtime.registry.begin_attempt(task.task_id)
    await runtime._finish_refresh(
        Decision(
            kind="refresh",
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            reason="i5",
            refresh_recipe=recipe,
        ),
        was_dirty=False,
    )
    rejected = [
        event
        for event in runtime.events.iter_events(run_id=runtime.run_id)
        if event.event_type == "refresh_rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0].reason == "read tool not allowed"
