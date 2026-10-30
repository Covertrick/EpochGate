"""吞吐和观测年龄分位数只从 JSONL 计算。"""

from __future__ import annotations

import json

from epochgate.metrics import _percentile, score_trace


def _write(path, events: list[dict]) -> None:
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def test_window_throughput_and_age_percentiles(tmp_path) -> None:
    path = tmp_path / "window.jsonl"
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
            "usage": {"arrival_mono": 0, "deadline_mono": 20},
        },
        {
            "event_type": "task_registered",
            "mono_time": 0,
            "identity": {"task_id": "b"},
            "usage": {"arrival_mono": 0, "deadline_mono": 20},
        },
        {
            "event_type": "task_completed",
            "mono_time": 5,
            "reason": "succeeded",
            "identity": {"task_id": "a"},
            "usage": {},
        },
        {
            "event_type": "task_completed",
            "mono_time": 6,
            "reason": "succeeded",
            "identity": {"task_id": "b"},
            "usage": {},
        },
        {
            "event_type": "action_committed",
            "mono_time": 11,
            "identity": {"task_id": "a"},
            "usage": {"sample_time_mono": 10},
        },
        {
            "event_type": "action_committed",
            "mono_time": 12,
            "identity": {"task_id": "a"},
            "usage": {"sample_time_mono": 10},
        },
        {
            "event_type": "action_rejected",
            "mono_time": 14,
            "reason": "version conflict",
            "identity": {"task_id": "b"},
            "usage": {"sample_time_mono": 10},
        },
        {
            "event_type": "observations_refreshed",
            "mono_time": 2,
            "reason": "n=1",
            "identity": {"task_id": "a"},
            "usage": {},
        },
        {
            "event_type": "decision_wait",
            "mono_time": 3,
            "reason": "same version",
            "identity": {"task_id": "a"},
            "usage": {},
        },
        {
            "event_type": "decision_wait",
            "mono_time": 4,
            "reason": "refresh interval",
            "identity": {"task_id": "b"},
            "usage": {},
        },
        {
            "event_type": "decision_wait",
            "mono_time": 4.5,
            "reason": "refresh budget exhausted",
            "identity": {"task_id": "b"},
            "usage": {},
        },
    ]
    _write(path, events)
    scored = score_trace(path)
    assert scored["effective_throughput"] == 0.2
    assert scored["observation_age_p50"] == 2
    assert scored["observation_age_p95"] == _percentile([1, 2, 4], 0.95)
    assert scored["observation_age_max"] == 4
    assert scored["refresh_count"] == 1
    assert scored["refresh_skipped"] == 3
    assert scored["confirmable_waste_tokens"] == 0


def test_missing_window_and_tokens_stay_zero(tmp_path) -> None:
    path = tmp_path / "bare.jsonl"
    _write(
        path,
        [
            {
                "event_type": "task_registered",
                "mono_time": 1,
                "identity": {"task_id": "a"},
                "usage": {"arrival_mono": 0, "deadline_mono": 10},
            },
            {
                "event_type": "task_completed",
                "mono_time": 2,
                "reason": "succeeded",
                "identity": {"task_id": "a"},
                "usage": {},
            },
            {
                "event_type": "action_rejected",
                "mono_time": 2,
                "reason": "stale plan_epoch",
                "identity": {"task_id": "a"},
                "usage": {},
            },
        ],
    )
    scored = score_trace(path)
    assert scored["effective_throughput"] == 0
    assert scored["confirmable_waste_tokens"] == 0
