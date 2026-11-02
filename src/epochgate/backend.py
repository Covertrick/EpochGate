"""推理后端协议、确定性假后端，以及可取消的延迟。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from typing import Protocol, runtime_checkable

from epochgate.ids import IdFactory
from epochgate.models import (
    BackendCapabilities,
    BackendEvent,
    BackendHandle,
    CancelReceipt,
    CandidateAction,
    InferenceRequest,
)


@runtime_checkable
class InferenceBackend(Protocol):
    """模型服务边界。调度器不得 import 具体后端。"""

    def capabilities(self) -> BackendCapabilities:
        """报告后端能力。

        输入：无。
        输出：``BackendCapabilities``。
        副作用：无。
        失败：无。
        """
        ...

    async def submit(self, request: InferenceRequest) -> BackendHandle:
        """受理一次推理。

        输入：带尝试身份和观测上下文的 ``InferenceRequest``。
        输出：``BackendHandle``。
        副作用：开始或排队生成。假后端在 submit 时绑定候选。
        失败：过载可以抛出 ``RuntimeError``。假后端不抛。
        """
        ...

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        """请求取消。回执不是资源已释放的证明。

        输入：句柄。
        输出：``CancelReceipt``。
        副作用：假后端只记录取消标记。
        失败：未知句柄可以拒绝受理，不抛异常。
        """
        ...

    def events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        """按句柄流出生命周期事件。

        输入：句柄。
        输出：accepted、started、completed 或 failed。
        副作用：无写入。
        失败：未知句柄抛出 ``KeyError``。
        """
        ...


class FakeBackend:
    """返回固定写动作。默认立即完成、不支持取消。"""

    def __init__(
        self,
        *,
        qty: int = 1,
        action_type: str = "reserve",
        delay_s: float = 0.0,
        supports_cancel: bool = False,
        max_inflight: int = 4,
        on_start: Callable[[], None] | None = None,
        ids: IdFactory | None = None,
    ) -> None:
        self._qty = qty
        self._action_type = action_type
        self._delay_s = delay_s
        self._on_start = on_start
        self._ids = ids or IdFactory(None)
        self._caps = BackendCapabilities(
            supports_cancel=supports_cancel,
            supports_streaming=False,
            resource_reclaim_observable=False,
            max_inflight=max_inflight,
            streaming_capability=False,
            local_read_abort=False,
            provider_cancel_request=False,
            provider_cancel_ack_semantics="ACKNOWLEDGED" if supports_cancel else "NOT_SUPPORTED",
            server_generation_observability=False,
            server_resource_reclaim_observability=False,
        )
        self._completed: dict[str, CandidateAction] = {}
        self._cancelled: set[str] = set()

    def capabilities(self) -> BackendCapabilities:
        """返回构造时的能力。副作用：无。失败：无。"""
        return self._caps

    async def submit(self, request: InferenceRequest) -> BackendHandle:
        """按请求当时的 epoch 生成候选，供 events 取用。

        输入：``InferenceRequest``。观测正文可以出现在 context 里，假后端不解读。
        输出：句柄。
        副作用：记下候选。
        失败：无。
        """
        handle_id = self._ids("bh")
        candidate = CandidateAction(
            action_id=self._ids("act"),
            task_id=request.task_id,
            attempt_id=request.attempt_id,
            plan_epoch=request.plan_epoch,
            obs_revision=request.obs_revision,
            action_type=self._action_type,
            parameters={"qty": self._qty, "goal": request.goal},
            observation_ids=list(request.observation_ids),
        )
        self._completed[handle_id] = candidate
        return BackendHandle(handle_id=handle_id, attempt_id=request.attempt_id)

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        """记录取消。确认只表示请求已收下，计算仍可能跑完。

        输入：句柄。
        输出：回执。不支持取消时 ``acknowledged=False``。
        副作用：支持取消时记下标记。
        失败：无。
        """
        if handle.handle_id not in self._completed:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=False,
                evidence="unknown_handle",
                grade="UNKNOWN",
            )
        if not self._caps.supports_cancel:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=False,
                evidence="cancel_not_supported",
                grade="NOT_SUPPORTED",
            )
        self._cancelled.add(handle.handle_id)
        return CancelReceipt(
            handle_id=handle.handle_id,
            acknowledged=True,
            evidence="cancel_accepted_compute_may_continue",
            grade="ACKNOWLEDGED",
            reclaimed=False,
        )

    async def events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        """流出受理、开始和完成。延迟期间仍会在结束时给出迟到结果。

        输入：句柄。
        输出：事件序列。取消不会吞掉 completed。
        副作用：可选的 on_start 回调。
        失败：未知句柄抛出 ``KeyError``。
        """
        if handle.handle_id not in self._completed:
            raise KeyError(f"unknown handle: {handle.handle_id}")
        now = time.monotonic()
        yield BackendEvent(handle_id=handle.handle_id, kind="accepted", mono_time=now)
        yield BackendEvent(handle_id=handle.handle_id, kind="started", mono_time=now)
        if self._on_start is not None:
            self._on_start()
        remaining = self._delay_s
        while remaining > 0:
            step = min(0.01, remaining)
            await asyncio.sleep(step)
            remaining -= step
            if handle.handle_id in self._cancelled:
                break
        yield BackendEvent(
            handle_id=handle.handle_id,
            kind="completed",
            mono_time=time.monotonic(),
            candidate=self._completed[handle.handle_id],
        )
