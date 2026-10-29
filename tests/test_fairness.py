"""虚拟截止时间会照顾久等的任务；赶不上截止的任务在准入时被拒绝。"""

from __future__ import annotations

import time

import pytest

from epochgate.backend import FakeBackend
from epochgate.environment import FakeEnvironment
from epochgate.metrics import score_trace
from epochgate.models import (
    AttemptState,
    AttemptView,
    BackendCapacityView,
    SchedulerSnapshot,
    Task,
    TaskStatus,
    TaskView,
)
from epochgate.runtime import Runtime
from epochgate.scheduler import EdfFixedScheduler, EvdfScheduler, NearAdmissionScheduler


def _snapshot(now: float) -> SchedulerSnapshot:
    return SchedulerSnapshot(
        now_mono=now,
        run_id="fair",
        tasks=[
            TaskView(
                task_id="fresh",
                status=TaskStatus.RUNNING,
                goal="g",
                plan_epoch=0,
                obs_revision=1,
                arrival_mono=now,
                deadline_mono=now + 10,
                task_class="strict",
            ),
            TaskView(
                task_id="starved",
                status=TaskStatus.RUNNING,
                goal="g",
                plan_epoch=0,
                obs_revision=1,
                arrival_mono=now - 100,
                deadline_mono=now + 40,
                task_class="loose",
            ),
        ],
        attempts=[
            AttemptView(
                attempt_id="a-fresh",
                task_id="fresh",
                plan_epoch=0,
                obs_revision=1,
                state=AttemptState.READY,
                eligible=True,
                has_observations=True,
                lease_id="lease-fresh",
                lease_expires_at_mono=now + 10,
            ),
            AttemptView(
                attempt_id="a-starved",
                task_id="starved",
                plan_epoch=0,
                obs_revision=1,
                state=AttemptState.READY,
                eligible=True,
                has_observations=True,
                lease_id="lease-starved",
                lease_expires_at_mono=now + 10,
            ),
        ],
        observations=[],
        capacity=BackendCapacityView(free_slots=1),
        estimated_service_s=0,
    )


def test_evdf_serves_the_long_waiter_before_physical_edf() -> None:
    now = time.monotonic()
    snapshot = _snapshot(now)
    edf = EdfFixedScheduler().decide(snapshot)
    evdf = EvdfScheduler().decide(snapshot)
    assert edf[0].task_id == "fresh"
    assert evdf[0].task_id == "starved"
    assert evdf[0].kind == "dispatch"


def test_slo_lease_ttl_follows_task_class() -> None:
    now = time.monotonic()
    snapshot = SchedulerSnapshot(
        now_mono=now,
        run_id="slo",
        tasks=[
            TaskView(
                task_id="tight",
                status=TaskStatus.RUNNING,
                goal="g",
                plan_epoch=0,
                obs_revision=1,
                arrival_mono=now,
                deadline_mono=now + 100,
                task_class="strict",
            )
        ],
        attempts=[
            AttemptView(
                attempt_id="a",
                task_id="tight",
                plan_epoch=0,
                obs_revision=1,
                state=AttemptState.QUEUED,
                eligible=True,
                has_observations=True,
            )
        ],
        observations=[],
        capacity=BackendCapacityView(free_slots=1),
        default_refresh=None,
        estimated_service_s=0,
    )
    decisions = NearAdmissionScheduler().decide(snapshot)
    admit = next(decision for decision in decisions if decision.kind == "admit")
    assert admit.lease is not None
    assert admit.lease.quota_class == "strict"
    assert admit.lease.expires_at_mono - now == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_unreachable_task_is_rejected_before_it_takes_a_slot(tmp_path) -> None:
    now = time.monotonic()
    runtime = Runtime(
        environment=FakeEnvironment(stock=2),
        backend=FakeBackend(qty=1, max_inflight=1),
        scheduler=NearAdmissionScheduler(),
        service_prior_s=5.0,
    )
    results = await runtime.run(
        [
            Task(
                task_id="late",
                arrival_mono=now,
                deadline_mono=now + 1,
                goal="reserve one unit",
                task_class="strict",
            ),
            Task(
                task_id="ok",
                arrival_mono=now,
                deadline_mono=now + 40,
                goal="reserve one unit",
                task_class="loose",
            ),
        ]
    )
    by_id = {task.task_id: task.status for task in results}
    assert by_id["late"] == TaskStatus.REJECTED
    assert by_id["ok"] == TaskStatus.SUCCEEDED
    path = tmp_path / "slo.jsonl"
    runtime.events.export_jsonl(path, run_id=runtime.run_id)
    scored = score_trace(path)
    assert scored["reject_rate"] == 0.5
    assert scored["wait_p99"] >= 0
    assert "wait_p50_loose" in scored or "wait_p50_strict" in scored or scored["wait_max"] >= 0
