"""用随机交错检查登记表和网关的不变量。"""

from __future__ import annotations

import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from epochgate.events import EventLog
from epochgate.gateway import ActionGateway
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.models import AttemptState, CandidateAction, Task, TaskStatus
from epochgate.observation import ObservationStore
from epochgate.registry import Registry


@given(st.lists(st.sampled_from(["commit", "repeat", "epoch", "cancel"]), min_size=1, max_size=6))
@settings(max_examples=20, deadline=None)
def test_registry_and_gateway_hold_i1_i7(actions: list[str]) -> None:
    async def _run() -> None:
        ids = IdFactory(1)
        env = InventoryEnvironment(stock=5, schedule=[], seed=1, ids=ids)
        log = EventLog()
        registry = Registry(log, run_id="hyp", ids=ids)
        observations = ObservationStore(ids=ids)
        gateway = ActionGateway(env, registry, observations, log, run_id="hyp")
        now = time.monotonic()
        task = Task(task_id="t", arrival_mono=now, deadline_mono=now + 30, goal="reserve")
        registry.register_task(task)
        attempt = registry.begin_attempt(task.task_id)
        batch = await env.observe(env.default_refresh())
        obs = observations.record(batch.observations[0], task_id="t")
        from epochgate.models import AdmissionLease

        lease = AdmissionLease(
            lease_id="lease",
            attempt_id=attempt.attempt_id,
            earliest_start_mono=now,
            expires_at_mono=now + 30,
        )
        candidate = CandidateAction(
            action_id="act",
            task_id="t",
            attempt_id=attempt.attempt_id,
            plan_epoch=attempt.plan_epoch,
            obs_revision=attempt.obs_revision,
            action_type="reserve",
            parameters={"qty": 1},
            observation_ids=[obs.observation_id],
        )
        commits = []
        for action in actions:
            if action == "epoch":
                if registry.get_task("t").status in {
                    TaskStatus.SUCCEEDED,
                    TaskStatus.FAILED,
                    TaskStatus.TIMED_OUT,
                    TaskStatus.REJECTED,
                }:
                    continue
                registry.supersede_attempt(attempt.attempt_id, reason="revise", bump_plan_epoch=True)
                attempt = registry.begin_attempt("t")
                candidate = candidate.model_copy(
                    update={"attempt_id": attempt.attempt_id, "plan_epoch": attempt.plan_epoch}
                )
                lease = lease.model_copy(update={"attempt_id": attempt.attempt_id})
            elif action == "cancel":
                registry.supersede_attempt(attempt.attempt_id, reason="cancel")
            elif action in {"commit", "repeat"}:
                if registry.get_task("t").status in {
                    TaskStatus.SUCCEEDED,
                    TaskStatus.FAILED,
                    TaskStatus.TIMED_OUT,
                    TaskStatus.REJECTED,
                }:
                    continue
                current = registry.get_attempt(candidate.attempt_id)
                if current.state == AttemptState.REJECTED or not current.eligible:
                    continue
                result = await gateway.submit(candidate, lease)
                commits.append(result)
                if result.accepted:
                    registry.complete_task("t", TaskStatus.SUCCEEDED)
        accepted = [item for item in commits if item.accepted]
        if len(accepted) > 1:
            assert len({item.commit_id for item in accepted}) == 1
        completed = [event for event in log.iter_events(run_id="hyp") if event.event_type == "task_completed"]
        assert len(completed) <= 1

    pytest.importorskip("hypothesis")
    import asyncio

    asyncio.run(_run())
