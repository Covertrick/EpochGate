"""取消边界：半截工具、提交竞态、残留包、取消失败、重规划只扣一次。"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from epochgate.environment import FakeEnvironment
from epochgate.http_streaming import HttpStreamingBackend
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.models import (
    AdmissionLease,
    AttemptState,
    BackendCapabilities,
    BackendEvent,
    BackendHandle,
    CancelReceipt,
    CandidateAction,
    InferenceRequest,
    Task,
    ToolSpec,
)
from epochgate.runtime import Runtime
from epochgate.scheduler import NearAdmissionScheduler
from mocks.streaming import MockStreamingBackend


def _runtime(env, backend, *, run_id: str, max_inflight_steps: int = 400) -> Runtime:
    ids = IdFactory(1)
    return Runtime(
        environment=env,
        backend=backend,
        scheduler=NearAdmissionScheduler(ids=ids),
        ids=ids,
        run_id=run_id,
        max_steps=max_inflight_steps,
        seed=1,
    )


def _request() -> InferenceRequest:
    return InferenceRequest(
        attempt_id="a",
        task_id="t",
        plan_epoch=0,
        obs_revision=0,
        goal="reserve one unit",
        tools=[
            ToolSpec(
                name="reserve",
                role="effect",
                parameters={
                    "type": "object",
                    "properties": {"qty": {"type": "integer"}},
                    "required": ["qty"],
                },
            )
        ],
    )


@pytest.mark.asyncio
async def test_half_tool_call_never_becomes_a_candidate() -> None:
    env = InventoryEnvironment(stock=3, schedule=[], seed=1, pace="step")
    fired = {"done": False}

    def drop_stock() -> None:
        if not fired["done"]:
            fired["done"] = True
            env.apply_exogenous(-3)

    backend = MockStreamingBackend(
        token_count=1,
        fragments=['{"qty":', "1}"],
        pause_after_fragments=1,
        on_trigger=drop_stock,
        stop_on_cancel=True,
        ids=IdFactory(1),
    )
    runtime = _runtime(env, backend, run_id="half-tool")
    now = time.monotonic()
    await runtime.run_task(
        Task(task_id="half", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")
    )
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    types = [event.event_type for event in events]
    assert "action_committed" not in types
    assert not any(event.event_type == "action_committed" for event in events)
    assert env.stock == 0


@pytest.mark.asyncio
async def test_revoke_during_check_does_not_commit() -> None:
    env = FakeEnvironment(stock=3)
    runtime = _runtime(env, MockStreamingBackend(token_count=1), run_id="race")
    now = time.monotonic()
    task = runtime.registry.register_task(
        Task(task_id="race", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")
    )
    attempt = runtime.registry.begin_attempt(task.task_id)
    runtime.registry.update_attempt_state(attempt.attempt_id, AttemptState.VALIDATING)
    lease = AdmissionLease(
        lease_id="lease",
        attempt_id=attempt.attempt_id,
        earliest_start_mono=now,
        expires_at_mono=now + 60,
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    original = env.check

    async def slow_check(candidate: CandidateAction):
        entered.set()
        await release.wait()
        return await original(candidate)

    env.check = slow_check  # type: ignore[method-assign]
    candidate = CandidateAction(
        action_id="act-race",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        plan_epoch=0,
        obs_revision=0,
        action_type="reserve",
        parameters={"qty": 1},
    )
    pending = asyncio.create_task(runtime.gateway.submit(candidate, lease))
    await entered.wait()
    revoked = await runtime.gateway.linearize_revoke(attempt.attempt_id, reason="cancelled")
    release.set()
    result = await pending
    assert revoked is True
    assert result.accepted is False
    assert env.stock == 3
    assert "action_committed" not in [
        event.event_type for event in runtime.events.iter_events(run_id=runtime.run_id)
    ]


class _LeakStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):  # type: ignore[override]
        yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"qty\\":"}}]}}]}\n\n'
        while not self.closed:
            await asyncio.sleep(0.01)
        yield b'data: {"choices":[{"delta":{"content":"LEAK1"}}]}\n\n'
        yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"LEAK2"}}]}}]}\n\n'
        raise httpx.ReadError("closed")

    async def aclose(self) -> None:
        self.closed = True


class _LeakTransport(httpx.AsyncBaseTransport):
    def __init__(self, stream: _LeakStream) -> None:
        self._stream = stream

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=self._stream)


@pytest.mark.asyncio
async def test_packets_after_close_do_not_extend_the_tool_buffer() -> None:
    stream = _LeakStream()
    backend = HttpStreamingBackend(
        base_url="http://sse.local/v1",
        api_key="local",
        model="local",
        trust_env=False,
        transport=_LeakTransport(stream),
        stop_on_cancel=True,
        ids=IdFactory(1),
    )
    handle = await backend.submit(_request())

    async def _consume() -> list[BackendEvent]:
        return [event async for event in backend.events(handle)]

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)
    receipt = await backend.cancel(handle)
    events = await task
    blob = " ".join(event.detail or "" for event in events)
    assert receipt.reclaimed is True
    assert "LEAK1" not in blob
    assert "LEAK2" not in blob
    assert "LEAK2" not in backend.argument_text.get(handle.handle_id, "")
    assert not any(event.kind == "completed" for event in events)


class _HoldBackend:
    def __init__(self, *, explode: bool) -> None:
        self.explode = explode
        self.gate = asyncio.Event()
        self.calls = 0
        self._caps = BackendCapabilities(supports_cancel=True, supports_streaming=True, max_inflight=1)

    def capabilities(self) -> BackendCapabilities:
        return self._caps

    async def submit(self, request: InferenceRequest) -> BackendHandle:
        return BackendHandle(handle_id=f"bh-{self.calls}", attempt_id=request.attempt_id)

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        if self.explode:
            raise RuntimeError("aclose failed")
        return CancelReceipt(
            handle_id=handle.handle_id,
            acknowledged=False,
            evidence="cancel_not_supported",
            grade="NOT_SUPPORTED",
            reclaimed=False,
        )

    async def events(self, handle: BackendHandle):
        self.calls += 1
        yield BackendEvent(handle_id=handle.handle_id, kind="started", mono_time=time.monotonic())
        if self.calls > 1:
            yield BackendEvent(
                handle_id=handle.handle_id,
                kind="failed",
                mono_time=time.monotonic(),
                detail="replan stopped for the test",
            )
            return
        yield BackendEvent(
            handle_id=handle.handle_id,
            kind="token_delta",
            mono_time=time.monotonic(),
            detail="x",
            usage={"tokens": 1, "usage_source": "local"},
        )
        await self.gate.wait()
        from epochgate.models import ToolCall

        yield BackendEvent(
            handle_id=handle.handle_id,
            kind="completed",
            mono_time=time.monotonic(),
            tool_call=ToolCall(call_id="c", name="reserve", arguments={"qty": 1}),
        )


async def _cancel_while_holding(backend: _HoldBackend, run_id: str) -> list:
    env = InventoryEnvironment(stock=3, schedule=[], seed=1, pace="step")
    runtime = _runtime(env, backend, run_id=run_id)
    now = time.monotonic()

    async def _revoke() -> None:
        while True:
            events = list(runtime.events.iter_events(run_id=runtime.run_id))
            if any(event.event_type == "token_delta" for event in events):
                attempt_id = next(
                    event.identity["attempt_id"] for event in events if event.event_type == "token_delta"
                )
                from epochgate.models import Decision

                await runtime._do_cancel(
                    Decision(kind="cancel", task_id="held", attempt_id=attempt_id, reason="boundary")
                )
                backend.gate.set()
                return
            await asyncio.sleep(0.01)

    asyncio.create_task(_revoke())
    await runtime.run_task(
        Task(task_id="held", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")
    )
    return list(runtime.events.iter_events(run_id=runtime.run_id)), env


@pytest.mark.asyncio
async def test_cancel_exception_blocks_the_late_result() -> None:
    events, env = await _cancel_while_holding(_HoldBackend(explode=True), "cancel-fail")
    types = [event.event_type for event in events]
    failed = next(event for event in events if event.event_type == "cancel_failed")
    blocked = failed.identity["attempt_id"]
    assert "action_committed" not in types
    assert not any(
        event.event_type == "action_rejected" and event.identity.get("attempt_id") == blocked for event in events
    )
    assert env.stock == 3


@pytest.mark.asyncio
async def test_unsupported_cancel_records_unknown_and_drops_the_late_result() -> None:
    events, env = await _cancel_while_holding(_HoldBackend(explode=False), "unsupported")
    unknown = next(event for event in events if event.event_type == "cancel_unknown")
    blocked = unknown.identity["attempt_id"]
    assert not any(
        event.event_type in {"action_committed", "action_rejected"} and event.identity.get("attempt_id") == blocked
        for event in events
    )
    assert env.stock == 3


@pytest.mark.asyncio
async def test_replan_commits_the_new_attempt_only_once() -> None:
    env = InventoryEnvironment(stock=3, schedule=[], seed=1, pace="step")
    fired = {"done": False}

    def drop_one() -> None:
        if not fired["done"]:
            fired["done"] = True
            env.apply_exogenous(-1)

    backend = MockStreamingBackend(
        token_count=4,
        trigger_at=2,
        on_trigger=drop_one,
        stop_on_cancel=True,
        ids=IdFactory(1),
    )
    runtime = _runtime(env, backend, run_id="replan-once")
    now = time.monotonic()
    await runtime.run_task(
        Task(task_id="once", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")
    )
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    committed = [event for event in events if event.event_type == "action_committed"]
    assert len(committed) == 1
    assert env.stock == 1
