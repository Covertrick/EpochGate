"""故障必须出现在 trace 里，否则这个 cell 不能算成功。"""

from __future__ import annotations

import time

import httpx
import pytest

from epochgate.environment import FakeEnvironment
from epochgate.events import EventLog
from epochgate.experiment import run_chaos_cell
from epochgate.faults import FaultPlan, FaultSpec, assert_triggers_match, load_scenario
from epochgate.http_backend import HttpToolBackend
from epochgate.models import InferenceRequest, Task, ToolSpec
from epochgate.runtime import Runtime
from epochgate.scheduler import HappyPathScheduler


class _OkTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"id": "ok", "function": {"name": "reserve", "arguments": '{"qty": 1}'}}
                            ]
                        }
                    }
                ]
            },
        )


def _request() -> InferenceRequest:
    return InferenceRequest(
        attempt_id="a",
        task_id="t",
        plan_epoch=0,
        obs_revision=0,
        goal="reserve",
        tools=[
            ToolSpec(
                name="reserve",
                role="effect",
                parameters={"type": "object", "properties": {"qty": {"type": "integer"}}, "required": ["qty"]},
            )
        ],
    )


def test_shipped_scenario_has_the_three_faults() -> None:
    plan = load_scenario("scenarios/chaos.yaml", seed=1)
    kinds = [item.kind for item in plan.faults]
    assert kinds == ["latency_spike", "value_fault", "crash"]
    spike = plan.faults[0]
    assert spike.min_ms == 2000
    assert spike.max_ms == 8000
    assert spike.probability == 0.3


@pytest.mark.asyncio
async def test_probability_one_records_fault_kind(tmp_path) -> None:
    scenario = tmp_path / "short.yaml"
    scenario.write_text(
        "seed: 3\nfaults:\n  - kind: latency_spike\n    probability: 1\n    min_ms: 1\n    max_ms: 2\n",
        encoding="utf-8",
    )
    scored = await run_chaos_cell(seed=3, scenario=scenario, jsonl_path=tmp_path / "chaos.jsonl", requests=2)
    text = (tmp_path / "chaos.jsonl").read_text(encoding="utf-8")
    assert '"fault_injected":true' in text or '"fault_injected": true' in text
    assert "latency_spike" in text
    assert scored["fault_injection_rate"] == 1


@pytest.mark.asyncio
async def test_untriggered_fault_cell_is_invalid(tmp_path) -> None:
    scenario = tmp_path / "never.yaml"
    scenario.write_text(
        "faults:\n  - kind: crash\n    probability: 1\n    modes: [http_500]\n",
        encoding="utf-8",
    )
    plan = FaultPlan(faults=[FaultSpec(kind="crash", probability=1, modes=["http_500"])], seed=1)
    plan.request_count = 1
    with pytest.raises(RuntimeError, match="fault cell invalid"):
        assert_triggers_match(plan, [])


@pytest.mark.asyncio
async def test_value_and_crash_faults_are_visible() -> None:
    plan = FaultPlan(
        faults=[FaultSpec(kind="value_fault", probability=1, modes=["bad_arguments"])],
        seed=1,
    )
    backend = HttpToolBackend(
        base_url="http://fault.local/v1",
        api_key="local",
        model="local",
        trust_env=False,
        transport=_OkTransport(),
        fault_plan=plan,
    )
    events = [event async for event in backend.events(await backend.submit(_request()))]
    assert events[-1].kind == "failed"
    assert events[-1].usage.get("fault_injected") is True
    assert events[-1].usage.get("fault_kind") == "value_fault"

    crash = FaultPlan(faults=[FaultSpec(kind="crash", probability=1, modes=["disconnect"])], seed=1)
    backend = HttpToolBackend(
        base_url="http://fault.local/v1",
        api_key="local",
        model="local",
        trust_env=False,
        transport=_OkTransport(),
        fault_plan=crash,
    )
    events = [event async for event in backend.events(await backend.submit(_request()))]
    assert events[-1].usage.get("fault_kind") == "crash"


class _DecodedGzipHeaderTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import gzip

        body = (
            b'{"choices":[{"message":{"tool_calls":[{"id":"ok","function":'
            b'{"name":"reserve","arguments":"{\\"qty\\": 1}"}}]}}]}'
        )
        return httpx.Response(
            200,
            content=gzip.compress(body),
            headers={"content-type": "application/json", "content-encoding": "gzip"},
        )


@pytest.mark.asyncio
async def test_latency_keeps_fault_when_body_was_already_decoded() -> None:
    plan = FaultPlan(
        faults=[FaultSpec(kind="latency_spike", probability=1, min_ms=1, max_ms=1)],
        seed=1,
    )
    backend = HttpToolBackend(
        base_url="http://fault.local/v1",
        api_key="local",
        model="local",
        trust_env=False,
        transport=_DecodedGzipHeaderTransport(),
        fault_plan=plan,
    )
    events = [event async for event in backend.events(await backend.submit(_request()))]
    assert events[-1].kind == "completed"
    assert events[-1].usage.get("fault_kind") == "latency_spike"
    assert events[-1].tool_call is not None


@pytest.mark.asyncio
async def test_runtime_trace_records_injected_fault() -> None:
    plan = FaultPlan(
        faults=[FaultSpec(kind="latency_spike", probability=1, min_ms=1, max_ms=1)],
        seed=2,
    )
    backend = HttpToolBackend(
        base_url="http://fault.local/v1",
        api_key="local",
        model="local",
        trust_env=False,
        transport=_OkTransport(),
        fault_plan=plan,
    )
    runtime = Runtime(
        environment=FakeEnvironment(stock=3),
        backend=backend,
        scheduler=HappyPathScheduler(),
        events=EventLog(),
    )
    now = time.monotonic()
    result = await runtime.run_task(
        Task(task_id="t-fault", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")
    )
    faults = [
        event
        for event in runtime.events.iter_events(run_id=runtime.run_id)
        if event.event_type == "fault_injected"
    ]
    assert faults
    assert faults[0].usage["fault_injected"] is True
    assert faults[0].reason == "latency_spike"
    assert_triggers_match(plan, list(runtime.events.iter_events(run_id=runtime.run_id)))
    assert result.status.value == "succeeded"
