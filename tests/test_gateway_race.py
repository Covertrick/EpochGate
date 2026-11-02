"""Gateway：check 通过后、commit 前外生改版本，旧动作不得产生副作用。"""

from __future__ import annotations

import time

import pytest

from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.models import (
    AdmissionLease,
    AttemptState,
    CandidateAction,
    Task,
)
from epochgate.runtime import Runtime
from epochgate.scheduler import NearAdmissionScheduler
from mocks.streaming import MockStreamingBackend


@pytest.mark.asyncio
async def test_exogenous_version_change_after_check_blocks_gateway_commit() -> None:
    env = InventoryEnvironment(stock=3, schedule=[], seed=1, pace="step")
    ids = IdFactory(1)
    runtime = Runtime(
        environment=env,
        backend=MockStreamingBackend(token_count=1, ids=ids),
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id="gateway-race",
        seed=1,
    )
    now = time.monotonic()
    task = runtime.registry.register_task(
        Task(task_id="t-race", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")
    )
    batch = await env.observe(env.default_refresh())
    obs = batch.observations[0]
    runtime.observations.record(obs, task_id=task.task_id)
    attempt = runtime.registry.begin_attempt(task.task_id)
    runtime.registry.update_attempt_state(attempt.attempt_id, AttemptState.VALIDATING)
    lease = AdmissionLease(
        lease_id="lease-race",
        attempt_id=attempt.attempt_id,
        earliest_start_mono=now,
        expires_at_mono=now + 60,
    )
    stock_before = env.stock
    version_before = env.version
    original_check = env.check

    async def check_then_bump(candidate: CandidateAction):
        result = await original_check(candidate)
        if result.ok:
            env.apply_exogenous(0)
        return result

    env.check = check_then_bump  # type: ignore[method-assign]
    candidate = CandidateAction(
        action_id="act-race",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        plan_epoch=attempt.plan_epoch,
        obs_revision=attempt.obs_revision,
        action_type="reserve",
        parameters={"qty": 1},
        observation_ids=[obs.observation_id],
    )
    result = await runtime.gateway.submit(candidate, lease)
    assert result.accepted is False
    assert any("version conflict" in reason for reason in result.reasons)
    assert env.stock == stock_before
    assert env.version != version_before
    types = [event.event_type for event in runtime.events.iter_events(run_id=runtime.run_id)]
    assert "action_committed" not in types
    assert "action_rejected" in types
