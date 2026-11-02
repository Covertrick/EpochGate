"""CI 用的可中断假流式后端。不参与真实模型记录。

词元计数只标 ``usage_source=local``。``reclaimed`` 只在本地生成循环
因取消而停止之后才为真，证据写「本地循环已停」，不写显存或图形处理器。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
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


class MockStreamingBackend:
    """按脚本流出词元和工具参数增量。完整工具调用只在循环走完时交出。"""

    def __init__(
        self,
        *,
        token_count: int = 8,
        trigger_at: int | None = None,
        on_trigger: Callable[[], None] | None = None,
        stop_on_cancel: bool = True,
        fragments: list[str] | None = None,
        pause_after_fragments: int | None = None,
        tool_name: str = "reserve",
        tool_arguments: dict[str, Any] | None = None,
        wait_s: float = 2.0,
        max_inflight: int = 1,
        ids: IdFactory | None = None,
    ) -> None:
        if token_count < 0:
            raise ValueError("token_count must be >= 0")
        self._token_count = token_count
        self._trigger_at = trigger_at
        self._on_trigger = on_trigger
        self._stop_on_cancel = stop_on_cancel
        self._fragments = list(fragments or [])
        self._pause_after_fragments = pause_after_fragments
        self._tool_name = tool_name
        self._tool_arguments = dict(tool_arguments or {"qty": 1})
        self._wait_s = wait_s
        self._ids = ids or IdFactory(None)
        self._caps = BackendCapabilities(
            supports_cancel=True,
            supports_streaming=True,
            resource_reclaim_observable=True,
            max_inflight=max_inflight,
        )
        self._requests: dict[str, InferenceRequest] = {}
        self._cancel_flags: dict[str, asyncio.Event] = {}
        self._stopped: dict[str, asyncio.Event] = {}
        self._stopped_by_cancel: set[str] = set()
        self._finished: set[str] = set()
        self._closed: set[str] = set()
        self._trigger_used = False
        self.trace: list[str] = []

    def capabilities(self) -> BackendCapabilities:
        """返回流式且可观察本地停止的能力。副作用：无。失败：无。"""
        return self._caps

    async def submit(self, request: InferenceRequest) -> BackendHandle:
        """登记一次生成。

        输入：推理请求。
        输出：句柄。
        副作用：为该句柄准备取消和停止信号。
        失败：无。
        """
        handle_id = self._ids("bh")
        self._requests[handle_id] = request
        self._cancel_flags[handle_id] = asyncio.Event()
        self._stopped[handle_id] = asyncio.Event()
        return BackendHandle(handle_id=handle_id, attempt_id=request.attempt_id)

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        """请求停止本地循环。

        输入：句柄。
        输出：取消回执。只有循环因取消退出时 ``reclaimed`` 才为真。
        副作用：置取消标记。``stop_on_cancel`` 为真时等待循环退出。
        失败：未知句柄返回未确认回执，不抛异常。
        """
        if handle.handle_id not in self._requests:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=False,
                evidence="unknown_handle",
                grade="UNKNOWN",
                reclaimed=False,
            )
        if handle.handle_id in self._finished:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=True,
                evidence="already_completed",
                grade="ACKNOWLEDGED",
                reclaimed=False,
            )
        self._cancel_flags[handle.handle_id].set()
        if not self._stop_on_cancel:
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
        )

    def events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        """流出增量。半截参数只出现在 ``tool_call_delta``，不组装成工具调用。

        输入：句柄。
        输出：accepted、started、token_delta、tool_call_delta，以及走完时的 completed。
        副作用：取消被尊重时记下循环已关闭。
        失败：未知句柄抛出 ``KeyError``。
        """
        return self._events(handle)

    async def _events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        if handle.handle_id not in self._requests:
            raise KeyError(handle.handle_id)
        request = self._requests[handle.handle_id]
        try:
            now = time.monotonic()
            yield BackendEvent(handle_id=handle.handle_id, kind="accepted", mono_time=now)
            yield BackendEvent(handle_id=handle.handle_id, kind="started", mono_time=now)
            for index in range(1, self._token_count + 1):
                if self._should_stop(handle.handle_id):
                    self._mark_cancelled(handle.handle_id)
                    return
                self.trace.append(f"token:{index}")
                yield BackendEvent(
                    handle_id=handle.handle_id,
                    kind="token_delta",
                    mono_time=time.monotonic(),
                    detail=f"t{index}",
                    usage={"tokens": 1, "usage_source": "local", "index": index},
                )
                if index == self._trigger_at and not self._trigger_used:
                    self._fire_trigger()
                    await self._wait_for_cancel(handle.handle_id)
                    if self._should_stop(handle.handle_id):
                        self._mark_cancelled(handle.handle_id)
                        return
            emitted = 0
            for fragment in self._fragments:
                if self._should_stop(handle.handle_id):
                    self._mark_cancelled(handle.handle_id)
                    return
                self.trace.append(f"fragment:{fragment}")
                yield BackendEvent(
                    handle_id=handle.handle_id,
                    kind="tool_call_delta",
                    mono_time=time.monotonic(),
                    detail=fragment,
                    usage={
                        "usage_source": "local",
                        "partial_arguments": fragment,
                    },
                )
                emitted += 1
                if (
                    self._pause_after_fragments is not None
                    and emitted == self._pause_after_fragments
                    and not self._trigger_used
                ):
                    self._fire_trigger()
                    await self._wait_for_cancel(handle.handle_id)
                    if self._should_stop(handle.handle_id):
                        self._mark_cancelled(handle.handle_id)
                        return
            if self._should_stop(handle.handle_id):
                self._mark_cancelled(handle.handle_id)
                return
            self._finished.add(handle.handle_id)
            yield BackendEvent(
                handle_id=handle.handle_id,
                kind="completed",
                mono_time=time.monotonic(),
                tool_call=ToolCall(
                    call_id=self._ids("call"),
                    name=self._tool_name,
                    arguments=dict(self._tool_arguments),
                ),
                usage={
                    "tokens": self._token_count,
                    "usage_source": "local",
                    "attempt_id": request.attempt_id,
                },
            )
        finally:
            self._closed.add(handle.handle_id)
            self.trace.append("stream_closed")
            self._stopped[handle.handle_id].set()

    def _should_stop(self, handle_id: str) -> bool:
        return self._stop_on_cancel and self._cancel_flags[handle_id].is_set()

    def _mark_cancelled(self, handle_id: str) -> None:
        self._stopped_by_cancel.add(handle_id)
        self.trace.append("stopped_for_cancel")

    def _fire_trigger(self) -> None:
        self._trigger_used = True
        if self._on_trigger is not None:
            self._on_trigger()

    async def _wait_for_cancel(self, handle_id: str) -> None:
        deadline = time.monotonic() + self._wait_s
        while time.monotonic() < deadline:
            if self._cancel_flags[handle_id].is_set():
                return
            await asyncio.sleep(0.01)
