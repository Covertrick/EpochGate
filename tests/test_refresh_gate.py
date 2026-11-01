"""B3 和 J0 的刷新门闩。只看快照，不跑环境。"""

from __future__ import annotations

from epochgate.models import (
    AttemptState,
    AttemptView,
    BackendCapacityView,
    RefreshRecipe,
    SchedulerSnapshot,
    TaskStatus,
    TaskView,
)
from epochgate.scheduler import NearComboScheduler, StrongComboScheduler


def _snapshot(attempt: AttemptView, **overrides) -> SchedulerSnapshot:
    now = 100.0
    fields = {
        "now_mono": now,
        "run_id": "gate",
        "tasks": [
            TaskView(
                task_id="t",
                status=TaskStatus.RUNNING,
                goal="g",
                plan_epoch=0,
                obs_revision=1,
                arrival_mono=now,
                deadline_mono=now + 100,
            )
        ],
        "attempts": [attempt],
        "observations": [],
        "capacity": BackendCapacityView(free_slots=1, supports_cancel=True),
        "default_refresh": RefreshRecipe(
            read_tool="read_inventory",
            sources=["inventory"],
            merge_key="inventory",
        ),
        "refresh_budget": 8,
        "notified_versions": {"inventory": "v1"},
        "min_refresh_interval_s": 0.0,
    }
    fields.update(overrides)
    return SchedulerSnapshot(**fields)


def _attempt(**overrides) -> AttemptView:
    fields = {
        "attempt_id": "a",
        "task_id": "t",
        "plan_epoch": 0,
        "obs_revision": 1,
        "state": AttemptState.QUEUED,
        "eligible": True,
        "has_observations": True,
        "depends_on": ["inventory"],
        "bound_versions": {"inventory": "v1"},
    }
    fields.update(overrides)
    return AttemptView(**fields)


def _kinds(decisions) -> list[str]:
    return [item.kind for item in decisions]


def test_same_version_waits() -> None:
    snapshot = _snapshot(
        _attempt(bound_versions={"inventory": "v2"}, sources_dirty=True),
        notified_versions={"inventory": "v2"},
    )
    for scheduler in (StrongComboScheduler(), NearComboScheduler()):
        decisions = scheduler.decide(snapshot)
        assert "refresh" not in _kinds(decisions)
        assert any(item.kind == "wait" and "same version" in item.reason for item in decisions)


def test_version_bump_refreshes() -> None:
    snapshot = _snapshot(
        _attempt(bound_versions={"inventory": "v1"}),
        notified_versions={"inventory": "v2"},
    )
    for scheduler in (StrongComboScheduler(), NearComboScheduler()):
        assert "refresh" in _kinds(scheduler.decide(snapshot))


def test_unrelated_source_ignored() -> None:
    snapshot = _snapshot(
        _attempt(depends_on=["inventory"], bound_versions={"inventory": "v1"}),
        notified_versions={"inventory": "v1", "calendar": "v9"},
    )
    for scheduler in (StrongComboScheduler(), NearComboScheduler()):
        assert "refresh" not in _kinds(scheduler.decide(snapshot))


def test_interval_blocks() -> None:
    now = 100.0
    snapshot = _snapshot(
        _attempt(bound_versions={"inventory": "v1"}, last_refresh_mono=now - 0.01),
        notified_versions={"inventory": "v2"},
        min_refresh_interval_s=0.05,
        now_mono=now,
    )
    for scheduler in (StrongComboScheduler(), NearComboScheduler()):
        decisions = scheduler.decide(snapshot)
        assert "refresh" not in _kinds(decisions)
        assert any("refresh interval" in item.reason for item in decisions)


def test_expensive_observe_waits_while_the_slot_is_busy() -> None:
    snapshot = _snapshot(
        _attempt(bound_versions={"inventory": "v1"}),
        notified_versions={"inventory": "v2"},
        observe_cost_s=1.0,
        estimated_service_s=1.0,
        capacity=BackendCapacityView(free_slots=0, supports_cancel=True),
    )
    decisions = NearComboScheduler().decide(snapshot)
    assert "refresh" not in _kinds(decisions)
    assert any("expensive observe" in item.reason for item in decisions)
    free = _snapshot(
        _attempt(bound_versions={"inventory": "v1"}),
        notified_versions={"inventory": "v2"},
        observe_cost_s=1.0,
        estimated_service_s=1.0,
        capacity=BackendCapacityView(free_slots=1, supports_cancel=True),
    )
    assert "refresh" in _kinds(NearComboScheduler().decide(free))


def test_budget_blocks() -> None:
    snapshot = _snapshot(
        _attempt(bound_versions={"inventory": "v1"}, refreshes_used=8),
        notified_versions={"inventory": "v2"},
        refresh_budget=8,
    )
    for scheduler in (StrongComboScheduler(), NearComboScheduler()):
        decisions = scheduler.decide(snapshot)
        assert "refresh" not in _kinds(decisions)
        assert any("refresh budget exhausted" in item.reason for item in decisions)


def test_premise_true_skips_cancel() -> None:
    snapshot = _snapshot(
        _attempt(state=AttemptState.INFLIGHT, sources_dirty=True, premise_holds=True)
    )
    for scheduler in (StrongComboScheduler(), NearComboScheduler()):
        assert "cancel" not in _kinds(scheduler.decide(snapshot))


def test_premise_false_cancels() -> None:
    snapshot = _snapshot(
        _attempt(state=AttemptState.INFLIGHT, sources_dirty=True, premise_holds=False)
    )
    for scheduler in (StrongComboScheduler(), NearComboScheduler()):
        decisions = scheduler.decide(snapshot)
        cancels = [item for item in decisions if item.kind == "cancel"]
        assert len(cancels) == 1
        assert cancels[0].reason == "dirty and premise failed; revoke eligibility"


def test_premise_unknown_waits() -> None:
    snapshot = _snapshot(
        _attempt(state=AttemptState.INFLIGHT, sources_dirty=True, premise_holds=None)
    )
    for scheduler in (StrongComboScheduler(), NearComboScheduler()):
        decisions = scheduler.decide(snapshot)
        assert "cancel" not in _kinds(decisions)
        assert any(item.kind == "wait" and "premise unknown" in item.reason for item in decisions)


def test_j0_pairs_lease_with_refresh() -> None:
    snapshot = _snapshot(_attempt(has_observations=False, bound_versions={}), notified_versions={})
    j0 = NearComboScheduler().decide(snapshot)
    b3 = StrongComboScheduler().decide(snapshot)
    assert _kinds(j0) == ["refresh", "admit"]
    assert any(item.reason == "j0 lease while refresh runs" for item in j0)
    assert _kinds(b3) == ["refresh"]


def test_serial_does_not_dispatch_while_refresh_inflight() -> None:
    now = 100.0
    snapshot = _snapshot(
        _attempt(
            state=AttemptState.READY,
            refresh_inflight=True,
            lease_id="lease-1",
            lease_expires_at_mono=now + 10,
        )
    )
    decisions = StrongComboScheduler().decide(snapshot)
    assert "dispatch" not in _kinds(decisions)
