"""Happy-path integration: observe → admit → fake backend → gateway commit."""

from __future__ import annotations

import time

import pytest

from epochgate.backend import FakeBackend
from epochgate.environment import FakeEnvironment
from epochgate.models import Task, TaskStatus
from epochgate.runtime import Runtime


@pytest.mark.asyncio
async def test_happy_path_succeeds() -> None:
    env = FakeEnvironment(stock=3)
    runtime = Runtime(environment=env, backend=FakeBackend(qty=1))
    now = time.monotonic()
    result = await runtime.run_task(
        Task(
            task_id="t-happy",
            arrival_mono=now,
            deadline_mono=now + 30.0,
            goal="reserve one unit",
        )
    )
    assert result.status == TaskStatus.SUCCEEDED
    assert env.stock == 2
    assert result.obs_revision >= 1

    types = [e.event_type for e in runtime.events.iter_events(run_id=runtime.run_id)]
    assert "observations_refreshed" in types
    assert "lease_granted" in types
    assert "action_committed" in types
    assert "task_completed" in types


@pytest.mark.asyncio
async def test_backend_and_environment_are_swappable() -> None:
    """Replacing Fake* types does not require changing scheduler.py."""

    class AltEnv(FakeEnvironment):
        pass

    class AltBackend(FakeBackend):
        pass

    runtime = Runtime(environment=AltEnv(stock=1), backend=AltBackend(qty=1))
    now = time.monotonic()
    result = await runtime.run_task(
        Task(
            task_id="t-swap",
            arrival_mono=now,
            deadline_mono=now + 30.0,
            goal="swap test",
        )
    )
    assert result.status == TaskStatus.SUCCEEDED
