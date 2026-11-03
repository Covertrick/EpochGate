"""v1.5 测量：时间线可重建，无害变化不误撤销，普通断流会停，供应商取消 API 未调用。"""

from __future__ import annotations

import time

import pytest

from epochgate.backend import FakeBackend
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.models import AttemptState, AttemptView, BackendCapacityView, SchedulerSnapshot, Task, TaskStatus, TaskView
from epochgate.runtime import Runtime
from epochgate.scenario_driver import ScenarioDriver, make_scenario_spec
from epochgate.scheduler import FifoAdmissionScheduler
from epochgate.scripted_backend import ScriptShot, ScriptedValidityBackend


def _snapshot(now: float, tasks: list[TaskView], attempts: list[AttemptView]) -> SchedulerSnapshot:
    return SchedulerSnapshot(
        now_mono=now,
        run_id="sjf",
        tasks=tasks,
        attempts=attempts,
        observations=[],
        capacity=BackendCapacityView(free_slots=1, supports_cancel=True),
    )


def test_sjf_prefers_declared_short_class_and_ages() -> None:
    now = 100.0
    tasks = [
        TaskView(
            task_id="long",
            status=TaskStatus.PENDING,
            goal="g",
            plan_epoch=0,
            obs_revision=1,
            arrival_mono=now - 1,
            deadline_mono=now + 30,
            task_class="long",
        ),
        TaskView(
            task_id="short",
            status=TaskStatus.PENDING,
            goal="g",
            plan_epoch=0,
            obs_revision=1,
            arrival_mono=now - 0.5,
            deadline_mono=now + 30,
            task_class="short",
        ),
    ]
    attempts = [
        AttemptView(
            attempt_id="a-long",
            task_id="long",
            plan_epoch=0,
            obs_revision=1,
            state=AttemptState.READY,
            eligible=True,
            has_observations=True,
            lease_id="l1",
            lease_expires_at_mono=now + 10,
            created_mono=now - 1,
        ),
        AttemptView(
            attempt_id="a-short",
            task_id="short",
            plan_epoch=0,
            obs_revision=1,
            state=AttemptState.READY,
            eligible=True,
            has_observations=True,
            lease_id="l2",
            lease_expires_at_mono=now + 10,
            created_mono=now - 0.5,
        ),
    ]
    sjf = FifoAdmissionScheduler(order="sjf")
    kinds = [(item.kind, item.task_id) for item in sjf.decide(_snapshot(now, tasks, attempts))]
    assert ("dispatch", "short") in kinds

    aged = FifoAdmissionScheduler(order="sjf", aging_s=25.0)
    tasks[0].arrival_mono = now - 30
    attempts[0].created_mono = now - 30
    aged_kinds = [(item.kind, item.task_id) for item in aged.decide(_snapshot(now, tasks, attempts))]
    assert aged_kinds[0] == ("dispatch", "long")


async def _run_scripted(arm: str, task_kind: str) -> list[str]:
    ids = IdFactory(1)
    spec = make_scenario_spec(scenario_id="m", task_kind=task_kind, t_change_s=0.05, initial_stock=1)
    env = InventoryEnvironment(stock=1, schedule=[], seed=1, pace="step", ids=ids)
    driver = ScenarioDriver(spec=spec, env=env)
    stop = arm != "A"
    backend = ScriptedValidityBackend(
        [ScriptShot(token_count=6, stop_on_cancel=stop, emit_tool=True, token_interval_s=0.05)],
        on_first_delta=driver.start_anchor,
        ids=ids,
    )
    policy = "any_signal" if arm == "B" else "premise"
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=FifoAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=f"measure-{arm}-{task_kind}",
        max_steps=4000,
        signal_policy=policy,
    )
    now = time.monotonic()
    await runtime.run([Task(task_id="t0", arrival_mono=now, deadline_mono=now + 20, goal="reserve")])
    driver.cancel_pending()
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    return [ev.event_type for ev in events]


@pytest.mark.asyncio
async def test_timeline_separates_local_stop_from_provider_abort() -> None:
    types = await _run_scripted("C", "invalidate")
    assert types.index("slot_acquired") < types.index("slot_released")
    assert "provider_abort_not_issued" in types
    assert "eligibility_revoked" in types


@pytest.mark.asyncio
async def test_harmless_change_cancels_plain_close_only() -> None:
    plain = await _run_scripted("B", "harmless")
    gated = await _run_scripted("C", "harmless")
    assert "eligibility_revoked" in plain
    assert "eligibility_revoked" not in gated


@pytest.mark.asyncio
async def test_revoked_attempt_rejected_across_fifty_orders() -> None:
    from epochgate.environment import FakeEnvironment
    from epochgate.models import AdmissionLease, CandidateAction

    for seed in range(50):
        env = FakeEnvironment(stock=3)
        runtime = Runtime(environment=env, backend=FakeBackend(), scheduler=FifoAdmissionScheduler())
        now = time.monotonic()
        task = runtime.registry.register_task(
            Task(task_id=f"t{seed}", arrival_mono=now, deadline_mono=now + 30, goal="reserve")
        )
        attempt = runtime.registry.begin_attempt(task.task_id)
        await runtime.gateway.linearize_revoke(attempt.attempt_id, reason="revoked")
        fresh = runtime.registry.begin_attempt(task.task_id)
        lease = AdmissionLease(
            lease_id=f"lease-{seed}",
            attempt_id=fresh.attempt_id,
            earliest_start_mono=now,
            expires_at_mono=now + 30,
        )
        stale = CandidateAction(
            action_id=f"old-{seed}",
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            plan_epoch=attempt.plan_epoch,
            obs_revision=attempt.obs_revision,
            action_type="reserve",
            parameters={"qty": 1},
        )
        current = CandidateAction(
            action_id=f"new-{seed}",
            task_id=task.task_id,
            attempt_id=fresh.attempt_id,
            plan_epoch=fresh.plan_epoch,
            obs_revision=fresh.obs_revision,
            action_type="reserve",
            parameters={"qty": 1},
        )
        order = [stale, current] if seed % 2 == 0 else [current, stale]
        results = [await runtime.gateway.submit(item, lease) for item in order]
        accepted = [item.action_id for item, result in zip(order, results) if result.accepted]
        assert f"old-{seed}" not in accepted
        assert env.stock == 2


def _reserve(action_id: str, attempt_id: str) -> CandidateAction:
    from epochgate.models import CandidateAction

    return CandidateAction(
        action_id=action_id,
        task_id="t",
        attempt_id=attempt_id,
        plan_epoch=0,
        obs_revision=0,
        action_type="reserve",
        parameters={"qty": 1},
    )


@pytest.mark.asyncio
async def test_two_attempts_racing_one_effect_commit_once() -> None:
    import random

    from epochgate.inventory import InventoryEnvironment

    for seed in range(50):
        env = InventoryEnvironment(stock=1, schedule=[], seed=seed, pace="step")
        version = env.version
        actions = [_reserve(f"old-{seed}", "old"), _reserve(f"new-{seed}", "new")]
        if seed % 2:
            random.Random(seed).shuffle(actions)
        results = [await env.commit(item, {"source_version": version}) for item in actions]
        accepted = [item.action_id for item, result in zip(actions, results) if result.accepted]
        assert len(accepted) == 1
        assert env.stock == 0


@pytest.mark.asyncio
async def test_lost_receipt_retry_does_not_repeat_effect() -> None:
    from epochgate.inventory import InventoryEnvironment

    for seed in range(50):
        env = InventoryEnvironment(stock=1, schedule=[], seed=seed, pace="step")
        action = _reserve(f"act-{seed}", "att")
        first = await env.commit(action, {"source_version": env.version})
        assert first.accepted
        lost = first.commit_id
        retried = await env.commit(action, {"source_version": "stale-after-loss"})
        assert retried.commit_id == lost
        assert env.stock == 0
        other = await env.commit(_reserve(f"other-{seed}", "att2"), {"source_version": env.version})
        assert not other.accepted
        assert env.stock == 0


@pytest.mark.asyncio
async def test_second_invalidation_during_recovery_rejects_stale_commit() -> None:
    import random

    from epochgate.inventory import InventoryEnvironment

    for seed in range(50):
        env = InventoryEnvironment(stock=1, schedule=[], seed=seed, pace="step")
        env.apply_exogenous(-1)
        env.apply_exogenous(1)
        captured = env.version
        action = _reserve(f"recover-{seed}", "fresh")
        order = ["drop", "commit"]
        random.Random(seed).shuffle(order)
        accepted = False
        dropped = False
        for step in order:
            if step == "drop":
                env.apply_exogenous(-1)
                dropped = True
            else:
                result = await env.commit(action, {"source_version": captured})
                accepted = result.accepted
                if dropped:
                    assert not result.accepted
        assert env.stock >= 0
        if accepted:
            assert not dropped or order[0] == "commit"
            assert env.stock == 0
        else:
            assert dropped


def test_restart_replay_does_not_restore_revoked_eligibility() -> None:
    import random

    from epochgate.registry import replay_eligibility

    for seed in range(50):
        rng = random.Random(seed)
        seq = [("attempt_begun", "old")]
        tail = [
            ("eligibility_revoked", "old"),
            ("process_restart", "old"),
            ("attempt_begun", "old"),
            ("noise", "old"),
            ("attempt_begun", "fresh"),
        ]
        rng.shuffle(tail)
        seq.extend(tail)
        state = replay_eligibility(seq)
        assert state["old"] is False
        assert state["fresh"] is True
