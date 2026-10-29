"""B0 与规则策略同种子可重放，指标只从 JSONL 读取。"""

from __future__ import annotations

import json

import pytest

from epochgate.experiment import run_once
from epochgate.metrics import score_trace


def _signature(path) -> list[tuple[str, str | None, str | None]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        identity = event.get("identity") or {}
        rows.append((event["event_type"], event.get("reason"), identity.get("task_id")))
    return rows


def test_score_trace_reads_only_the_file(tmp_path) -> None:
    path = tmp_path / "hand.jsonl"
    events = [
        {
            "event_type": "task_registered",
            "mono_time": 10,
            "reason": None,
            "identity": {"task_id": "a"},
            "usage": {"arrival_mono": 1, "deadline_mono": 20},
        },
        {
            "event_type": "decision_wait",
            "mono_time": 12,
            "reason": "no free inference slots; waiting task=a",
            "identity": {"task_id": "a"},
            "usage": {},
        },
        {
            "event_type": "action_rejected",
            "mono_time": 14,
            "reason": "stale plan_epoch",
            "identity": {"task_id": "a"},
            "usage": {},
        },
        {
            "event_type": "task_completed",
            "mono_time": 15,
            "reason": "succeeded",
            "identity": {"task_id": "a"},
            "usage": {},
        },
    ]
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    scored = score_trace(path)
    assert scored["tasks"] == 1
    assert scored["on_time_success"] == 1
    assert scored["cohort_success_rate"] == 1
    assert scored["stale_rejects"] == 1
    assert scored["stale_candidate"] == 1
    assert scored["provider_rejected"] == 0
    assert scored["gateway_rejected"] == 0
    assert scored["waits"] == 1
    assert scored["cancels"] == 0
    assert scored["confirmable_waste_tokens"] == 0
    assert scored["observation_age_max"] == 0


def _succeeded(path) -> list[str]:
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event["event_type"] == "task_completed" and event.get("reason") == "succeeded":
            found.append(event["identity"]["task_id"])
    return found


def _count(path, event_type: str, reason_part: str | None = None) -> int:
    total = 0
    for event_type_row, reason, _task_id in _signature(path):
        if event_type_row != event_type:
            continue
        if reason_part is not None and reason_part not in (reason or ""):
            continue
        total += 1
    return total


@pytest.mark.asyncio
async def test_same_seed_replays_and_policies_differ(tmp_path) -> None:
    paths = {}
    for policy in ("b0", "b1", "b2", "near"):
        first = tmp_path / f"{policy}-a.jsonl"
        second = tmp_path / f"{policy}-b.jsonl"
        await run_once(seed=7, policy=policy, jsonl_path=first)
        await run_once(seed=7, policy=policy, jsonl_path=second)
        assert _signature(first) == _signature(second)
        paths[policy] = first
    assert _succeeded(paths["b0"]) == ["t0"]
    assert _succeeded(paths["b1"]) == ["t1"]
    assert _succeeded(paths["b0"]) != _succeeded(paths["b1"])
    assert _succeeded(paths["b2"]) == ["t0"]
    assert _succeeded(paths["near"]) == ["t1"]
    def _task_refreshes(path, task_id: str) -> int:
        return sum(
            1
            for event_type, _reason, row_task in _signature(path)
            if event_type == "observations_refreshed" and row_task == task_id
        )

    assert _count(paths["b2"], "observations_refreshed", "recheck before dispatch") >= 1
    assert _task_refreshes(paths["b2"], "t0") == _task_refreshes(paths["b0"], "t0") + 1
