"""v1.0 的统一负载、口径、打分和公平槽。"""

from __future__ import annotations

import json

import pytest

from epochgate.config import choose_frozen, load_config, repo_root
from epochgate.experiment import run_cell, trace_signature
from epochgate.explain import explain_task
from epochgate.metrics import bootstrap_ci, score_trace
from epochgate.models import (
    AttemptState,
    AttemptView,
    BackendCapacityView,
    RefreshRecipe,
    SchedulerSnapshot,
    TaskStatus,
    TaskView,
)
from epochgate.public_env import second_environment_status
from epochgate.scheduler import JointScheduler, NearComboScheduler


def test_frozen_seeds_do_not_overlap() -> None:
    dev = load_config(repo_root() / "configs" / "dev.yaml")
    frozen = load_config(repo_root() / "configs" / "frozen.yaml")
    assert not dev.overlaps_dev_and_test()
    assert not frozen.overlaps_dev_and_test()
    assert frozen.min_refresh_interval_s in dev.grid_interval
    assert frozen.fair_weight in dev.grid_weight
    assert frozen.fair_every_n in dev.grid_fair_every_n


def test_choose_frozen_prefers_higher_cohort_then_fewer_refreshes() -> None:
    chosen = choose_frozen(
        [
            {"cohort": 0.5, "refresh_count": 1, "fair_every_n": 4, "fair_weight": 1, "min_refresh_interval_s": 0.05},
            {"cohort": 1, "refresh_count": 3, "fair_every_n": 0, "fair_weight": 0, "min_refresh_interval_s": 0},
            {"cohort": 1, "refresh_count": 1, "fair_every_n": 4, "fair_weight": 1, "min_refresh_interval_s": 0.05},
        ]
    )
    assert chosen["refresh_count"] == 1
    assert chosen["fair_every_n"] == 4


def test_window_completion_stays_in_cohort_only(tmp_path) -> None:
    path = tmp_path / "late.jsonl"
    events = [
        {
            "event_type": "run_started",
            "mono_time": 0,
            "identity": {},
            "usage": {"window_start_mono": 0, "window_end_mono": 10},
        },
        {
            "event_type": "task_registered",
            "mono_time": 0,
            "identity": {"task_id": "a"},
            "usage": {"arrival_mono": 0, "deadline_mono": 30},
        },
        {
            "event_type": "task_completed",
            "mono_time": 12,
            "reason": "succeeded",
            "identity": {"task_id": "a"},
            "usage": {},
        },
        {
            "event_type": "run_finished",
            "mono_time": 12,
            "identity": {},
            "usage": {"drain_duration": 2, "scheduler_cpu_s": 0.01, "metadata_bytes": 4, "performance_claim": False},
        },
        {
            "event_type": "action_rejected",
            "mono_time": 12,
            "reason": "stale plan_epoch",
            "identity": {"task_id": "a"},
            "usage": {"tokens": 9},
        },
        {
            "event_type": "action_committed",
            "mono_time": 12,
            "identity": {"task_id": "a"},
            "usage": {
                "effect_stale": True,
                "observation_ages": [
                    {"source_id": "inventory", "age_s": 1},
                    {"source_id": "calendar", "age_s": 4},
                ],
            },
        },
    ]
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    scored = score_trace(path)
    assert scored["cohort_success_rate"] == 1
    assert scored["window_success"] == 0
    assert scored["effective_throughput"] == 0
    assert scored["drain_duration"] == 2
    assert scored["confirmable_waste_tokens"] == 0
    assert scored["stale_commit"] == 1
    assert scored["candidate_stale"] == 1
    assert scored["observation_age_max"] == 4
    assert scored["performance_claim"] == 0
    assert scored["log_io_bytes"] > 0


def test_backend_tokens_count_only_with_source(tmp_path) -> None:
    path = tmp_path / "tokens.jsonl"
    path.write_text(
        json.dumps(
            {
                "event_type": "action_rejected",
                "mono_time": 1,
                "reason": "version conflict",
                "identity": {"task_id": "a"},
                "usage": {"tokens": 3, "usage_source": "backend", "reject_class": "provider_reject"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    scored = score_trace(path)
    assert scored["confirmable_waste_tokens"] == 3
    assert scored["provider_reject"] == 1
    assert scored["performance_claim"] == 1


def test_j1_refreshes_when_version_gaps() -> None:
    now = 100.0
    snapshot = SchedulerSnapshot(
        now_mono=now,
        run_id="j1",
        tasks=[
            TaskView(
                task_id="t",
                status=TaskStatus.RUNNING,
                goal="g",
                plan_epoch=0,
                obs_revision=1,
                arrival_mono=now,
                deadline_mono=now + 50,
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
                depends_on=["inventory"],
                bound_versions={"inventory": "v1"},
                refreshes_used=1,
            )
        ],
        observations=[],
        capacity=BackendCapacityView(free_slots=1, supports_cancel=True),
        default_refresh=RefreshRecipe(read_tool="read_inventory", sources=["inventory"], merge_key="inventory"),
        notified_versions={"inventory": "v2"},
        risk_lambda=0.5,
        epsilon=0.05,
        observe_cost_s=0.05,
        estimated_service_s=0.2,
    )
    decisions = JointScheduler().decide(snapshot)
    assert any(item.kind == "refresh" for item in decisions)
    closed = snapshot.model_copy(update={"notified_versions": {"inventory": "v1"}, "risk_lambda": 0.5})
    attempt = snapshot.attempts[0].model_copy(
        update={"bound_versions": {"inventory": "v1"}, "premise_holds": True, "state": AttemptState.READY, "lease_id": "L", "lease_expires_at_mono": now + 10}
    )
    ready = closed.model_copy(update={"attempts": [attempt]})
    assert any(item.kind == "dispatch" for item in JointScheduler().decide(ready))


def test_fair_slot_prefers_loose_when_waits_tie() -> None:
    now = 50.0
    snapshot = SchedulerSnapshot(
        now_mono=now,
        run_id="fair",
        tasks=[
            TaskView(
                task_id="short",
                status=TaskStatus.RUNNING,
                goal="g",
                plan_epoch=0,
                obs_revision=1,
                arrival_mono=now,
                deadline_mono=now + 10,
                task_class="strict",
            ),
            TaskView(
                task_id="long",
                status=TaskStatus.RUNNING,
                goal="g",
                plan_epoch=0,
                obs_revision=1,
                arrival_mono=now,
                deadline_mono=now + 40,
                task_class="loose",
            ),
        ],
        attempts=[
            AttemptView(
                attempt_id="a-short",
                task_id="short",
                plan_epoch=0,
                obs_revision=1,
                state=AttemptState.READY,
                eligible=True,
                has_observations=True,
                lease_id="ls",
                lease_expires_at_mono=now + 5,
                depends_on=["inventory"],
                bound_versions={"inventory": "v1"},
            ),
            AttemptView(
                attempt_id="a-long",
                task_id="long",
                plan_epoch=0,
                obs_revision=1,
                state=AttemptState.READY,
                eligible=True,
                has_observations=True,
                lease_id="ll",
                lease_expires_at_mono=now + 30,
                depends_on=["inventory"],
                bound_versions={"inventory": "v1"},
            ),
        ],
        observations=[],
        capacity=BackendCapacityView(free_slots=1),
        notified_versions={"inventory": "v1"},
        fair_every_n=1,
        dispatches_since_fair_slot=0,
        estimated_service_s=0,
    )
    decisions = NearComboScheduler(fair_every_n=1).decide(snapshot)
    dispatched = [item for item in decisions if item.kind == "dispatch"]
    assert dispatched[0].task_id == "long"
    assert "fair slot" in dispatched[0].reason


def test_second_environment_has_no_ranking() -> None:
    status = second_environment_status()
    assert status["ranking"] is None
    assert status["gaia2"]["available"] is False
    assert status["realtimegym"]["available"] is False


def test_bootstrap_interval_covers_the_mean() -> None:
    rows = [{"effective_throughput": value, "stale_rejects": 0, "wait_p50": 0, "wait_p95": 0, "wait_p99": 0} for value in (1, 2, 3)]
    summary = bootstrap_ci(rows, ["effective_throughput"], seed=1, samples=80)
    assert summary["effective_throughput"]["low"] <= summary["effective_throughput"]["mean"]
    assert summary["effective_throughput"]["high"] >= summary["effective_throughput"]["mean"]


@pytest.mark.asyncio
async def test_same_seed_matrix_replays(tmp_path) -> None:
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    await run_cell(seed=11, policy="j0", jsonl_path=first)
    await run_cell(seed=11, policy="j0", jsonl_path=second)
    assert trace_signature(first) == trace_signature(second)


def test_explain_demo_trace_prints_the_three_labels() -> None:
    lines = explain_task(repo_root() / "results" / "explain" / "demo.jsonl", "t-explain")
    text = "\n".join(lines)
    assert "stale_observation" in text
    assert "late_output_rejected" in text
    assert "eligibility_revoked" in text
    assert "api_key" not in text
