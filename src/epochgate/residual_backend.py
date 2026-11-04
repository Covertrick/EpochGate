"""可控后端残余：本地读可停，但共享后端令牌仍被占用 D 秒。

仅用于 L0 / 脚本与 vLLM 对照。禁止作为 DeepSeek 真实臂。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
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


class ResidualTokenBackend:
    """假 GPU 令牌桶：cancel 后本地事件停止，令牌在 residual_hold_s 后才归还。"""

    def __init__(
        self,
        *,
        residual_hold_s: float = 0.0,
        token_interval_s: float = 0.05,
        max_tokens: int = 40,
        backend_slots: int = 1,
        max_inflight: int = 2,
        stop_on_cancel: bool = True,
        ids: IdFactory | None = None,
    ) -> None:
        self._residual_hold_s = max(0.0, float(residual_hold_s))
        self._token_interval_s = max(0.0, float(token_interval_s))
        self._max_tokens = max(1, int(max_tokens))
        self._backend_slots = max(1, int(backend_slots))
        self._stop_on_cancel = stop_on_cancel
        self._ids = ids or IdFactory(None)
        self._caps = BackendCapabilities(
            supports_cancel=True,
            supports_streaming=True,
            resource_reclaim_observable=True,
            max_inflight=max_inflight,
            streaming_capability=True,
            local_read_abort=stop_on_cancel,
            client_stream_close_issued=stop_on_cancel,
            provider_cancel_ack_semantics="ACKNOWLEDGED",
            server_generation_observability=True,
            server_generation_stopped_observed=False,
            server_resource_reclaim_observability=True,
            server_resource_reclaim_observed=False,
        )
        self._sem = asyncio.Semaphore(self._backend_slots)
        self._requests: dict[str, InferenceRequest] = {}
        self._cancel: dict[str, asyncio.Event] = {}
        self._stopped: dict[str, asyncio.Event] = {}
        self._stopped_by_cancel: set[str] = set()
        self._finished: set[str] = set()
        self._holding: dict[str, float] = {}
        self.trace: list[str] = []

    def capabilities(self) -> BackendCapabilities:
        return self._caps

    @property
    def residual_hold_s(self) -> float:
        return self._residual_hold_s

    async def submit(self, request: InferenceRequest) -> BackendHandle:
        handle_id = self._ids("bh")
        self._requests[handle_id] = request
        self._cancel[handle_id] = asyncio.Event()
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
        if handle.handle_id in self._finished:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=True,
                evidence="already_completed",
                grade="ACKNOWLEDGED",
                reclaimed=False,
            )
        self._cancel[handle.handle_id].set()
        if not self._stop_on_cancel:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=True,
                evidence="cancel_accepted_compute_may_continue",
                grade="ACKNOWLEDGED",
                reclaimed=False,
            )
        try:
            await asyncio.wait_for(self._stopped[handle.handle_id].wait(), timeout=2.0)
        except TimeoutError:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=True,
                evidence="loop_not_stopped",
                grade="ACKNOWLEDGED",
                reclaimed=False,
                connection_closed=True,
            )
        reclaimed = handle.handle_id in self._stopped_by_cancel
        return CancelReceipt(
            handle_id=handle.handle_id,
            acknowledged=True,
            evidence="local_loop_stopped_backend_may_hold" if reclaimed else "loop_not_stopped",
            grade="ACKNOWLEDGED",
            reclaimed=reclaimed,
            connection_closed=True,
        )

    def events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        return self._events(handle)

    async def _events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        hid = handle.handle_id
        if hid not in self._requests:
            raise KeyError(hid)
        await self._sem.acquire()
        self._holding[hid] = time.monotonic()
        self.trace.append(f"acquire:{hid}")
        try:
            now = time.monotonic()
            yield BackendEvent(handle_id=hid, kind="accepted", mono_time=now)
            yield BackendEvent(handle_id=hid, kind="started", mono_time=now)
            for i in range(self._max_tokens):
                if self._stop_on_cancel and self._cancel[hid].is_set():
                    self._stopped_by_cancel.add(hid)
                    break
                await asyncio.sleep(self._token_interval_s)
                if self._stop_on_cancel and self._cancel[hid].is_set():
                    self._stopped_by_cancel.add(hid)
                    break
                yield BackendEvent(
                    handle_id=hid,
                    kind="token_delta",
                    mono_time=time.monotonic(),
                    detail=f"t{i} ",
                    usage={"event_count": 1, "usage_source": "local", "index": i},
                )
            if not (self._stop_on_cancel and self._cancel[hid].is_set()):
                yield BackendEvent(
                    handle_id=hid,
                    kind="completed",
                    mono_time=time.monotonic(),
                    tool_call=ToolCall(
                        call_id=self._ids("call"),
                        name="reserve",
                        arguments={"qty": 1},
                    ),
                    usage={"tokens": self._max_tokens, "usage_source": "local"},
                )
        finally:
            hold = self._residual_hold_s if hid in self._stopped_by_cancel else 0.0
            if hold > 0:
                self.trace.append(f"residual:{hid}:{hold}")
                await asyncio.sleep(hold)
            self._sem.release()
            self._holding.pop(hid, None)
            self._finished.add(hid)
            self._stopped[hid].set()
            self.trace.append(f"release:{hid}")

    def residual_status(self, handle_id: str) -> dict[str, Any]:
        """脚本真值：是否仍占后端令牌。"""
        holding = handle_id in self._holding
        return {
            "backend_stop_status": "delayed" if holding else "stopped",
            "holding": holding,
            "residual_hold_s": self._residual_hold_s,
            "observation_level": "scripted-truth",
        }
