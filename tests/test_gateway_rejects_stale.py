"""Gateway must reject candidates whose plan_epoch lags the registry."""

from __future__ import annotations

import time

import pytest

from epochgate.backend import FakeBackend
from epochgate.environment import FakeEnvironment
from epochgate.models import AdmissionLease, CandidateAction, Task
from epochgate.runtime import Runtime
from epochgate.scheduler import HappyPathScheduler


@pytest.mark.asyncio
async def test_gateway_rejects_stale_plan_epoch() -> None:
    env = FakeEnvironment(stock=5)
    runtime = Runtime(
        environment=env,
        backend=FakeBackend(qty=1),
        scheduler=HappyPathScheduler(),
    )
    now = time.monotonic()
    task = runtime.registry.register_task(
        Task(
            task_id="t-stale",
            arrival_mono=now,
            deadline_mono=now + 30.0,
            goal="should reject stale epoch",
        )
    )
    attempt = runtime.registry.begin_attempt(task.task_id)

    # Advance plan epoch so a candidate minted at epoch 0 becomes stale.
    runtime.registry.supersede_attempt(
        attempt.attempt_id,
        reason="plan revised",
        bump_plan_epoch=True,
    )
    plan_epoch, obs_revision = runtime.registry.current_epoch(task.task_id)
    assert plan_epoch == 1

    # Re-open an eligible attempt at the new epoch for lease binding.
    attempt2 = runtime.registry.begin_attempt(task.task_id)
    lease = AdmissionLease(
        lease_id="lease-test",
        attempt_id=attempt2.attempt_id,
        earliest_start_mono=now,
        expires_at_mono=now + 60.0,
    )

    stale = CandidateAction(
        action_id="act-stale",
        task_id=task.task_id,
        attempt_id=attempt2.attempt_id,
        plan_epoch=0,  # stale
        obs_revision=obs_revision,
        action_type="reserve",
        parameters={"qty": 1},
    )
    result = await runtime.gateway.submit(stale, lease)
    assert result.accepted is False
    assert any("stale plan_epoch" in r for r in result.reasons)
    assert env.stock == 5
