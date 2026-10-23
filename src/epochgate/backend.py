"""Inference backend protocol and a deterministic fake for demos/tests."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

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
    """Model-serving boundary. Scheduler never imports a concrete backend."""

    def capabilities(self) -> BackendCapabilities:
        """Report backend features. Side effects: none."""
        ...

    async def submit(self, request: InferenceRequest) -> BackendHandle:
        """Accept an inference request.

        Inputs: InferenceRequest with attempt identity and observation refs.
        Outputs: BackendHandle.
        Side effects: starts or queues generation (Fake completes immediately).
        Failures: overload may raise ``RuntimeError`` (Fake does not).
        """
        ...

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        """Request cancellation. Happy path does not call this."""
        ...

    def events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        """Stream lifecycle events for a handle.

        Side effects: none (read-only stream).
        Failures: unknown handle → ``KeyError``.
        """
        ...


class FakeBackend:
    """Returns a fixed reserve action bound to the request identity."""

    def __init__(self, *, qty: int = 1) -> None:
        self._qty = qty
        self._caps = BackendCapabilities(supports_cancel=False, supports_streaming=False, max_inflight=4)
        self._completed: dict[str, CandidateAction] = {}

    def capabilities(self) -> BackendCapabilities:
        """Return static capabilities. Side effects: none."""
        return self._caps

    async def submit(self, request: InferenceRequest) -> BackendHandle:
        """Synthesize a CandidateAction and store it for ``events``.

        Inputs: InferenceRequest.
        Outputs: BackendHandle.
        Side effects: records completed candidate keyed by handle_id.
        Failures: none in this fake.
        """
        handle_id = f"bh-{uuid.uuid4().hex[:12]}"
        candidate = CandidateAction(
            action_id=f"act-{uuid.uuid4().hex[:12]}",
            task_id=request.task_id,
            attempt_id=request.attempt_id,
            plan_epoch=request.plan_epoch,
            obs_revision=request.obs_revision,
            action_type="reserve",
            parameters={"qty": self._qty, "goal": request.goal},
            observation_ids=list(request.observation_ids),
        )
        self._completed[handle_id] = candidate
        return BackendHandle(handle_id=handle_id, attempt_id=request.attempt_id)

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        """Not implemented in v0 happy path."""
        raise NotImplementedError("FakeBackend.cancel is out of scope for v0")

    async def events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        """Yield accepted → started → completed for a known handle."""
        if handle.handle_id not in self._completed:
            raise KeyError(f"unknown handle: {handle.handle_id}")
        now = time.monotonic()
        yield BackendEvent(handle_id=handle.handle_id, kind="accepted", mono_time=now)
        yield BackendEvent(handle_id=handle.handle_id, kind="started", mono_time=now)
        yield BackendEvent(
            handle_id=handle.handle_id,
            kind="completed",
            mono_time=time.monotonic(),
            candidate=self._completed[handle.handle_id],
        )
