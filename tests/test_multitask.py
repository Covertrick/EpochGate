"""共享槽位、截止时间，以及过期租约重新准入。"""

from __future__ import annotations

import time

import pytest

from epochgate.backend import FakeBackend
from epochgate.environment import FakeEnvironment
from epochgate.models import AdmissionLease, AttemptState, Task, TaskStatus
from epochgate.runtime import Runtime


@pytest.mark.asyncio
async def test_two_tasks_share_one_slot_and_log_the_waiter() -> None:
    env = FakeEnvironment(stock=5)
    runtime = Runtime(
        environment=env,
        backend=FakeBackend(delay_s=0.3, max_inflight=1),
        max_steps=200,
    )
    now = time.monotonic()
    results = await runtime.run(
        [
            Task(
                task_id="t-hold",
                arrival_mono=now,
                deadline_mono=now + 30.0,
                goal="reserve one unit",
            ),
            Task(
                task_id="t-wait",
                arrival_mono=now + 0.001,
                deadline_mono=now + 0.1,
                goal="reserve one unit",
            ),
        ]
    )
    by_id = {task.task_id: task.status for task in results}
    assert by_id["t-hold"] == TaskStatus.SUCCEEDED
    assert by_id["t-wait"] == TaskStatus.TIMED_OUT
    waits = [
        event
        for event in runtime.events.iter_events(run_id=runtime.run_id)
        if event.event_type == "decision_wait" and event.identity.get("task_id") == "t-wait"
    ]
    assert waits
    assert any("t-wait" in (event.reason or "") for event in waits)
    assert any("t-hold" in (event.reason or "") for event in waits)


@pytest.mark.asyncio
async def test_expired_lease_is_cleared_and_readmitted() -> None:
    runtime = Runtime(environment=FakeEnvironment(stock=3), backend=FakeBackend())
    now = time.monotonic()
    task = runtime.registry.register_task(
        Task(
            task_id="t-lease",
            arrival_mono=now,
            deadline_mono=now + 30.0,
            goal="reserve",
        )
    )
    attempt = runtime.registry.begin_attempt(task.task_id)
    batch = await runtime.environment.observe(runtime.environment.default_refresh())
    for obs in batch.observations:
        runtime.observations.record(obs, task_id=task.task_id)
    runtime.registry.update_attempt_state(attempt.attempt_id, AttemptState.READY)
    runtime._leases[attempt.attempt_id] = AdmissionLease(
        lease_id="old-lease",
        attempt_id=attempt.attempt_id,
        earliest_start_mono=now - 10.0,
        expires_at_mono=now - 1.0,
    )
    runtime._drop_expired_leases()
    assert attempt.attempt_id not in runtime._leases
    assert any(event.event_type == "lease_expired" for event in runtime.events.iter_events())
    decisions = runtime.scheduler.decide(runtime.build_snapshot())
    assert any(decision.kind == "admit" for decision in decisions)
