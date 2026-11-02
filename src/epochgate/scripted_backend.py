"""确定性剧本后端：按 attempt 吐出文本增量与完整 reserve 工具调用。

仅用于离线正确性验收；不访问真实模型。取消证据只写本地循环是否停止。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from epochgate.ids import IdFactory
from epochgate.models import (
    BackendCapabilities,
    BackendEvent,
    BackendHandle,
    CancelReceipt,
    InferenceRequest,
    ToolCall,
)


@dataclass(frozen=True)
class ScriptShot:
    """单次 submit 的剧本。"""

    token_count: int = 4
    trigger_at: int | None = None
    stop_on_cancel: bool = True
    tool_name: str = "reserve"
    tool_arguments: dict[str, Any] = field(default_factory=lambda: {"qty": 1})
    wait_s: float = 2.0
    emit_tool: bool = True
    token_interval_s: float = 0.0
    tool_fragments: tuple[str, ...] = ()


class ScriptedValidityBackend:
    """按顺序消费 ``scripts``；每次 ``submit`` 取下一镜（用尽后重复最后一镜）。"""

    def __init__(
        self,
        scripts: Sequence[ScriptShot],
        *,
        on_trigger: Callable[[], None] | None = None,
        on_first_delta: Callable[[], None] | None = None,
        max_inflight: int = 1,
        ids: IdFactory | None = None,
    ) -> None:
        if not scripts:
            raise ValueError("scripts must be non-empty")
        self._scripts = list(scripts)
        self._on_trigger = on_trigger
        self._on_first_delta = on_first_delta
        self._first_delta_fired = False
        self._ids = ids or IdFactory(None)
        self._caps = BackendCapabilities(
            supports_cancel=True,
            supports_streaming=True,
            resource_reclaim_observable=False,
            max_inflight=max_inflight,
        )
        self._shot_index = 0
        self._requests: dict[str, InferenceRequest] = {}
        self._shots: dict[str, ScriptShot] = {}
        self._cancel_flags: dict[str, asyncio.Event] = {}
        self._stopped: dict[str, asyncio.Event] = {}
        self._stopped_by_cancel: set[str] = set()
        self._finished: set[str] = set()
        self._trigger_used: set[str] = set()
        self.trace: list[str] = []

    def capabilities(self) -> BackendCapabilities:
        return self._caps

    async def submit(self, request: InferenceRequest) -> BackendHandle:
        handle_id = self._ids("bh")
        idx = min(self._shot_index, len(self._scripts) - 1)
        self._shot_index += 1
        self._requests[handle_id] = request
        self._shots[handle_id] = self._scripts[idx]
        self._cancel_flags[handle_id] = asyncio.Event()
        self._stopped[handle_id] = asyncio.Event()
        return BackendHandle(handle_id=handle_id, attempt_id=request.attempt_id)

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        if handle.handle_id not in self._requests:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=False,
                evidence="unknown_handle",
                grade="UNKNOWN",
                reclaimed=False,
            )
        shot = self._shots[handle.handle_id]
        if handle.handle_id in self._finished:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=True,
                evidence="already_completed",
                grade="ACKNOWLEDGED",
                reclaimed=False,
            )
        self._cancel_flags[handle.handle_id].set()
        if not shot.stop_on_cancel:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=True,
                evidence="cancel_accepted_compute_may_continue",
                grade="ACKNOWLEDGED",
                reclaimed=False,
            )
        try:
            await asyncio.wait_for(self._stopped[handle.handle_id].wait(), timeout=1.0)
        except TimeoutError:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=True,
                evidence="loop_not_stopped",
                grade="ACKNOWLEDGED",
                reclaimed=False,
            )
        reclaimed = handle.handle_id in self._stopped_by_cancel
        return CancelReceipt(
            handle_id=handle.handle_id,
            acknowledged=True,
            evidence="本地循环已停" if reclaimed else "loop_not_stopped",
            grade="ACKNOWLEDGED",
            reclaimed=reclaimed,
            connection_closed=True,
        )

    def events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        return self._events(handle)

    async def _events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        if handle.handle_id not in self._requests:
            raise KeyError(handle.handle_id)
        request = self._requests[handle.handle_id]
        shot = self._shots[handle.handle_id]
        try:
            now = time.monotonic()
            yield BackendEvent(handle_id=handle.handle_id, kind="accepted", mono_time=now)
            yield BackendEvent(handle_id=handle.handle_id, kind="started", mono_time=now)
            for index in range(1, shot.token_count + 1):
                if self._should_stop(handle.handle_id, shot):
                    self._mark_cancelled(handle.handle_id)
                    return
                self.trace.append(f"token:{index}")
                yield BackendEvent(
                    handle_id=handle.handle_id,
                    kind="token_delta",
                    mono_time=time.monotonic(),
                    detail=f"t{index} ",
                    usage={"event_count": 1, "usage_source": "local", "index": index},
                )
                if not self._first_delta_fired:
                    self._first_delta_fired = True
                    if self._on_first_delta is not None:
                        self._on_first_delta()
                if index == shot.trigger_at and handle.handle_id not in self._trigger_used:
                    self._trigger_used.add(handle.handle_id)
                    if self._on_trigger is not None:
                        self._on_trigger()
                    await self._wait_for_cancel(handle.handle_id, shot.wait_s)
                    if self._should_stop(handle.handle_id, shot):
                        self._mark_cancelled(handle.handle_id)
                        return
                elif shot.token_interval_s > 0:
                    await self._sleep_check_cancel(handle.handle_id, shot, shot.token_interval_s)
                    if self._should_stop(handle.handle_id, shot):
                        self._mark_cancelled(handle.handle_id)
                        return
            for frag in shot.tool_fragments:
                if self._should_stop(handle.handle_id, shot):
                    self._mark_cancelled(handle.handle_id)
                    return
                self.trace.append(f"fragment:{frag}")
                yield BackendEvent(
                    handle_id=handle.handle_id,
                    kind="tool_call_delta",
                    mono_time=time.monotonic(),
                    detail=frag,
                    usage={"event_count": 1, "usage_source": "local", "partial_arguments": frag},
                )
                if shot.token_interval_s > 0:
                    await self._sleep_check_cancel(handle.handle_id, shot, shot.token_interval_s)
                    if self._should_stop(handle.handle_id, shot):
                        self._mark_cancelled(handle.handle_id)
                        return
            if self._should_stop(handle.handle_id, shot):
                self._mark_cancelled(handle.handle_id)
                return
            if not shot.emit_tool:
                self._finished.add(handle.handle_id)
                yield BackendEvent(
                    handle_id=handle.handle_id,
                    kind="completed",
                    mono_time=time.monotonic(),
                    usage={"tokens": shot.token_count, "usage_source": "local"},
                )
                return
            self._finished.add(handle.handle_id)
            yield BackendEvent(
                handle_id=handle.handle_id,
                kind="completed",
                mono_time=time.monotonic(),
                tool_call=ToolCall(
                    call_id=self._ids("call"),
                    name=shot.tool_name,
                    arguments=dict(shot.tool_arguments),
                ),
                usage={
                    "tokens": shot.token_count,
                    "usage_source": "local",
                    "attempt_id": request.attempt_id,
                },
            )
        finally:
            self.trace.append("stream_closed")
            self._stopped[handle.handle_id].set()

    def _should_stop(self, handle_id: str, shot: ScriptShot) -> bool:
        return shot.stop_on_cancel and self._cancel_flags[handle_id].is_set()

    def _mark_cancelled(self, handle_id: str) -> None:
        self._stopped_by_cancel.add(handle_id)
        self.trace.append("stopped_for_cancel")

    async def _wait_for_cancel(self, handle_id: str, wait_s: float) -> None:
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self._cancel_flags[handle_id].is_set():
                return
            await asyncio.sleep(0.01)

    async def _sleep_check_cancel(self, handle_id: str, shot: ScriptShot, interval_s: float) -> None:
        deadline = time.monotonic() + interval_s
        while time.monotonic() < deadline:
            if self._should_stop(handle_id, shot):
                return
            await asyncio.sleep(min(0.005, max(0.001, deadline - time.monotonic())))
