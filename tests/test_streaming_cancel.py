"""流式假后端的四条取消不变量，以及种子 1–3 的配对。"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from epochgate.explain import explain_task
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.models import Task, TaskStatus
from epochgate.runtime import Runtime
from epochgate.scheduler import NearAdmissionScheduler
from mocks.paired import run_paired_case
from mocks.streaming import MockStreamingBackend


def _runtime(env, backend, *, run_id: str) -> Runtime:
    ids = IdFactory(1)
    return Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=run_id,
        max_steps=400,
        seed=1,
    )


@pytest.mark.asyncio
async def test_stream_completes_and_commits() -> None:
    env = InventoryEnvironment(stock=3, schedule=[], seed=1, pace="step")
    backend = MockStreamingBackend(token_count=4, ids=IdFactory(1))
    runtime = _runtime(env, backend, run_id="stream-a")
    now = time.monotonic()
    result = await runtime.run_task(
        Task(task_id="t-a", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")
    )
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    types = [event.event_type for event in events]
    deltas = [event for event in events if event.event_type == "token_delta"]
    assert result.status == TaskStatus.SUCCEEDED
    assert env.stock == 2
    assert len(deltas) == 4
    assert types.index("token_delta") < types.index("action_committed")
    assert all(event.usage.get("usage_source") == "local" for event in deltas)
    assert "action_rejected" not in types


@pytest.mark.asyncio
async def test_cancel_after_fifth_token_stops_the_local_loop() -> None:
    env = InventoryEnvironment(stock=3, schedule=[], seed=1, pace="step")
    fired = {"done": False}

    def drop_stock() -> None:
        if not fired["done"]:
            fired["done"] = True
            env.apply_exogenous(-3)

    backend = MockStreamingBackend(
        token_count=8,
        trigger_at=5,
        on_trigger=drop_stock,
        stop_on_cancel=True,
        ids=IdFactory(1),
    )
    runtime = _runtime(env, backend, run_id="stream-b")
    now = time.monotonic()
    result = await runtime.run_task(
        Task(task_id="t-b", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")
    )
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    types = [event.event_type for event in events]
    assert types.index("attempt_superseded") < types.index("cancel_requested")
    assert types.index("cancel_requested") < types.index("cancel_acknowledged")
    assert types.index("cancel_acknowledged") < types.index("resource_reclaimed")
    assert "stopped_for_cancel" in backend.trace
    assert "stream_closed" in backend.trace
    revoked = next(event for event in events if event.event_type == "cancel_requested")
    attempt_id = revoked.identity["attempt_id"]
    own = [
        event
        for event in events
        if event.event_type == "token_delta" and event.identity.get("attempt_id") == attempt_id
    ]
    assert len(own) == 5
    assert all(float(event.mono_time) < float(revoked.mono_time) for event in own)
    reclaimed = next(event for event in events if event.event_type == "resource_reclaimed")
    assert reclaimed.reason == "本地循环已停"
    assert "gpu" not in (reclaimed.reason or "").lower()
    assert "显存" not in (reclaimed.reason or "")
    assert "action_committed" not in types
    assert result.status != TaskStatus.SUCCEEDED
    before = len(own)
    await asyncio.sleep(1.0)
    again = list(runtime.events.iter_events(run_id=runtime.run_id))
    own_again = [
        event
        for event in again
        if event.event_type == "token_delta" and event.identity.get("attempt_id") == attempt_id
    ]
    assert len(own_again) == before


@pytest.mark.asyncio
async def test_partial_tool_arguments_never_reach_the_gateway() -> None:
    fragment = '{"qty":1,"note":"PARTIAL-7f3a"'
    marker = "PARTIAL-7f3a"
    env = InventoryEnvironment(stock=3, schedule=[], seed=1, pace="step")
    fired = {"done": False}

    def drop_stock() -> None:
        if not fired["done"]:
            fired["done"] = True
            env.apply_exogenous(-3)

    backend = MockStreamingBackend(
        token_count=2,
        fragments=[fragment, ',"rest":1}'],
        pause_after_fragments=1,
        on_trigger=drop_stock,
        stop_on_cancel=True,
        ids=IdFactory(1),
    )
    runtime = _runtime(env, backend, run_id="stream-c")
    now = time.monotonic()
    await runtime.run_task(
        Task(task_id="t-c", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")
    )
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    hits = [event for event in events if marker in event.model_dump_json()]
    assert hits
    assert {event.event_type for event in hits} == {"tool_call_delta"}
    for event in events:
        if event.event_type in {"action_committed", "action_rejected"}:
            assert marker not in event.model_dump_json()


@pytest.mark.asyncio
async def test_late_result_after_cancel_is_rejected(tmp_path) -> None:
    env = InventoryEnvironment(stock=3, schedule=[], seed=1, pace="step")
    fired = {"done": False}

    def drop_stock() -> None:
        if not fired["done"]:
            fired["done"] = True
            env.apply_exogenous(-3)

    backend = MockStreamingBackend(
        token_count=8,
        trigger_at=5,
        on_trigger=drop_stock,
        stop_on_cancel=False,
        ids=IdFactory(1),
    )
    runtime = _runtime(env, backend, run_id="stream-d")
    now = time.monotonic()
    result = await runtime.run_task(
        Task(task_id="t-d", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")
    )
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    revoked = next(event for event in events if event.event_type == "cancel_requested")
    attempt_id = revoked.identity["attempt_id"]
    late = [
        event
        for event in events
        if event.event_type == "action_rejected" and event.identity.get("attempt_id") == attempt_id
    ]
    assert late
    assert "attempt not eligible" in (late[0].reason or "")
    assert "stopped_for_cancel" not in backend.trace
    assert result.status != TaskStatus.SUCCEEDED
    path = tmp_path / "late.jsonl"
    runtime.events.export_jsonl(path, run_id=runtime.run_id)
    explained = explain_task(path, "t-d")
    assert any("late_output_rejected" in line and "attempt not eligible" in line for line in explained)


@pytest.mark.asyncio
async def test_development_seeds_pair_stop_against_reject_only() -> None:
    rows = []
    for seed in (1, 2, 3):
        arm_a = await run_paired_case(seed=seed, arm="A")
        arm_b = await run_paired_case(seed=seed, arm="B")
        rows.extend([arm_a, arm_b])
        assert arm_a["length_aligned"] is True
        assert arm_b["length_aligned"] is True
        assert arm_a["local_tokens_before_revoke"] == arm_b["local_tokens_before_revoke"] == 5
        assert arm_a["extra_tokens_until_local_stop"] > arm_b["extra_tokens_until_local_stop"] == 0
        assert arm_a["old_result_submitted"] is True
        assert arm_b["old_result_submitted"] is False
        assert arm_a["local_loop_stopped"] is False
        assert arm_b["local_loop_stopped"] is True
        assert arm_a["local_stop_latency_s"] is None
        assert arm_b["local_stop_latency_s"] is not None
        assert arm_b["local_stop_latency_s"] < 1.0
        assert arm_a["committed"] is False
        assert arm_b["committed"] is False
        assert arm_a["provider_usage"] is None
        assert arm_b["provider_usage"] is None
        assert arm_a["unknown_compute"] is False
        assert arm_b["usage_sources_on_deltas"] == ["local"]
        assert arm_b["usage_sources_on_deltas"] == ["local"]
    assert [row["seed"] for row in rows] == [1, 1, 2, 2, 3, 3]
    encoded = json.dumps(rows)
    assert "api_key" not in encoded
    assert "EPOCHGATE" not in encoded
