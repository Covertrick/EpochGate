"""Runtime：组装快照、执行决策。不得直接调用 environment.commit。"""

from __future__ import annotations

import asyncio
import time

from epochgate.backend import InferenceBackend
from epochgate.environment import EnvironmentAdapter
from epochgate.events import EventLog
from epochgate.gateway import ActionGateway
from epochgate.ids import IdFactory
from epochgate.models import (
    AdmissionLease,
    AttemptState,
    AttemptView,
    BackendCapacityView,
    BackendHandle,
    CancelReceipt,
    CandidateAction,
    Decision,
    InferenceRequest,
    ObservationView,
    RefreshRecipe,
    SchedulerSnapshot,
    Task,
    TaskStatus,
    TaskView,
    ToolCall,
    ToolSpec,
)
from epochgate.observation import ObservationStore
from epochgate.refresh_pool import RefreshPool
from epochgate.registry import Registry
from epochgate.scheduler import HappyPathScheduler, Scheduler

_TERMINAL = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.TIMED_OUT,
    TaskStatus.REJECTED,
}

_SLOT_HELD = {
    AttemptState.INFLIGHT,
    AttemptState.RESULT_READY,
    AttemptState.VALIDATING,
    AttemptState.COMMITTED,
}

_REPLAN_MARKERS = (
    "stale",
    "version conflict",
    "lease expired",
    "attempt not eligible",
    "lease attempt_id mismatch",
)


class Runtime:
    """把登记表、观测、调度、后端和网关接在一起。

    依赖方向是 Runtime → 协作者 → 模型。
    只有 ActionGateway 可以调用 ``environment.commit``。
    工具名来自环境目录，本模块按 role 分发。
    """

    def __init__(
        self,
        *,
        environment: EnvironmentAdapter,
        backend: InferenceBackend,
        scheduler: Scheduler | None = None,
        events: EventLog | None = None,
        run_id: str | None = None,
        default_refresh: RefreshRecipe | None = None,
        max_steps: int = 64,
        max_turns: int = 4,
        inline_dispatch: bool = False,
        ids: IdFactory | None = None,
        seed: int | None = None,
        refresh_budget: int = 8,
        service_prior_s: float = 0.0,
    ) -> None:
        self.ids = ids or IdFactory(seed)
        self.run_id = run_id or self.ids("run")
        self.events = events or EventLog()
        self.registry = Registry(self.events, run_id=self.run_id, ids=self.ids)
        self.observations = ObservationStore(ids=self.ids)
        self.environment = environment
        self.backend = backend
        self.scheduler = scheduler or HappyPathScheduler(ids=self.ids)
        self.gateway = ActionGateway(
            environment,
            self.registry,
            self.observations,
            self.events,
            run_id=self.run_id,
        )
        self.default_refresh = default_refresh if default_refresh is not None else environment.default_refresh()
        self.refresh_pool = RefreshPool(per_task_budget=refresh_budget)
        self.max_steps = max_steps
        self.max_turns = max_turns
        self.inline_dispatch = inline_dispatch
        self._leases: dict[str, AdmissionLease] = {}
        self._inflight = 0
        self._inflight_attempts: dict[str, str] = {}
        self._handles: dict[str, BackendHandle] = {}
        self._workers: set[asyncio.Task[None]] = set()
        self._dirty: set[str] = set()
        self._cursor = "0"
        self._sessions: dict[str, list[dict]] = {}
        self._refreshing: set[str] = set()
        self._premise: dict[str, bool] = {}
        self._service_prior_s = service_prior_s
        self._last_service_s: float | None = None
        self._dispatch_started: dict[str, float] = {}

    async def run_task(self, task: Task) -> Task:
        """把单个任务跑到终态。

        输入：带 monotonic 到达和截止时间的任务。
        输出：登记表中的终态任务。
        副作用：驱动观察、准入、派发和网关提交。
        失败：超出步数上限时抛出 ``RuntimeError``。
        """
        finished = await self.run([task])
        return finished[0]

    async def run(self, tasks: list[Task]) -> list[Task]:
        """同时推进多个任务，共享后端槽位。

        输入：任务列表。
        输出：与输入顺序一致的终态任务。
        副作用：轮询环境变化、执行调度决策、必要时取消在途推理。
        失败：没有进展或超出步数上限时抛出 ``RuntimeError``。
        """
        for task in tasks:
            self.registry.register_task(task)
            attempt = self.registry.begin_attempt(task.task_id)
            self.events.emit(
                run_id=self.run_id,
                event_type="runtime_start",
                identity={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
            )

        for _ in range(self.max_steps):
            if self._all_terminal(tasks):
                break
            await self._poll_changes()
            self._drop_expired_leases()
            await self._probe_premises()
            if self._all_terminal(tasks):
                break
            decisions = self.scheduler.decide(self.build_snapshot())
            if not decisions and not self._workers:
                raise RuntimeError("scheduler returned no decisions before terminal state")
            progressed = False
            for decision in decisions:
                progressed = await self._apply(decision) or progressed
            if self._workers:
                await self._reap_workers()
            elif not progressed:
                raise RuntimeError("runtime stalled without inflight work")
        else:
            await self._drain_workers()
            if not self._all_terminal(tasks):
                raise RuntimeError(f"exceeded max_steps={self.max_steps}")

        await self._drain_workers()
        if not self._all_terminal(tasks):
            raise RuntimeError(f"exceeded max_steps={self.max_steps}")
        return [self.registry.get_task(task.task_id) for task in tasks]

    def build_snapshot(self, *, task_id: str | None = None) -> SchedulerSnapshot:
        """组装裁剪后的调度快照。

        输入：可选任务 id；为空时包含全部任务。
        输出：``SchedulerSnapshot``。观测只有元数据，没有 payload。
        副作用：不写仓库。
        失败：无。
        """
        tasks = self.registry.all_tasks()
        if task_id is not None:
            tasks = [task for task in tasks if task.task_id == task_id]

        task_views = [
            TaskView(
                task_id=task.task_id,
                status=task.status,
                goal=task.goal,
                plan_epoch=task.plan_epoch,
                obs_revision=task.obs_revision,
                arrival_mono=task.arrival_mono,
                deadline_mono=task.deadline_mono,
                task_class=task.task_class,
            )
            for task in tasks
        ]
        attempt_views: list[AttemptView] = []
        obs_views: list[ObservationView] = []
        for task in tasks:
            latest = self.observations.latest_for_task(task.task_id)
            for obs in latest:
                obs_views.append(
                    ObservationView(
                        observation_id=obs.observation_id,
                        source_id=obs.source_id,
                        source_version=obs.source_version,
                        sample_time_mono=obs.sample_time_mono,
                        task_id=task.task_id,
                    )
                )
            dirty = any(obs.source_id in self._dirty for obs in latest)
            for attempt in self.registry.attempts_for_task(task.task_id):
                lease = self._leases.get(attempt.attempt_id)
                attempt_views.append(
                    AttemptView(
                        attempt_id=attempt.attempt_id,
                        task_id=attempt.task_id,
                        plan_epoch=attempt.plan_epoch,
                        obs_revision=attempt.obs_revision,
                        state=attempt.state,
                        eligible=attempt.eligible,
                        has_observations=bool(latest),
                        lease_id=None if lease is None else lease.lease_id,
                        lease_expires_at_mono=None if lease is None else lease.expires_at_mono,
                        sources_dirty=dirty,
                        refreshes_used=self.refresh_pool.used(task.task_id),
                        refresh_inflight=attempt.attempt_id in self._refreshing,
                        premise_holds=self._premise.get(attempt.attempt_id),
                    )
                )

        caps = self.backend.capabilities()
        free = max(0, caps.max_inflight - self._inflight)
        return SchedulerSnapshot(
            now_mono=time.monotonic(),
            run_id=self.run_id,
            tasks=task_views,
            attempts=attempt_views,
            observations=obs_views,
            capacity=BackendCapacityView(
                free_slots=free,
                supports_cancel=caps.supports_cancel,
                inflight_task_ids=list(self._inflight_attempts.values()),
            ),
            default_refresh=self.default_refresh,
            dirty_sources=sorted(self._dirty),
            refresh_budget=self.refresh_pool.budget(),
            estimated_service_s=self._estimated_service_s(),
        )

    async def _apply(self, decision: Decision) -> bool:
        if decision.kind == "refresh":
            self._start_refresh(decision)
            return True
        if decision.kind == "admit":
            self._do_admit(decision)
            return True
        if decision.kind == "dispatch":
            await self._start_dispatch(decision)
            return True
        if decision.kind == "finish":
            self._do_finish(decision)
            return True
        if decision.kind == "timeout":
            await self._do_timeout(decision)
            return True
        if decision.kind == "cancel":
            await self._do_cancel(decision)
            return True
        if decision.kind == "reject":
            self._do_reject(decision)
            return True
        if decision.kind == "wait":
            self.events.emit(
                run_id=self.run_id,
                event_type="decision_wait",
                identity={"task_id": decision.task_id, "attempt_id": decision.attempt_id or ""},
                reason=decision.reason,
            )
            return False
        raise ValueError(f"unknown decision kind: {decision.kind}")

    def _start_refresh(self, decision: Decision) -> None:
        if decision.attempt_id is None or decision.refresh_recipe is None:
            raise ValueError("refresh requires attempt_id and refresh_recipe")
        attempt_id = decision.attempt_id
        if attempt_id in self._refreshing:
            return
        self._refreshing.add(attempt_id)
        if attempt_id not in self._leases:
            self.registry.update_attempt_state(attempt_id, AttemptState.OBSERVING)
        was_dirty = self._task_dirty(decision.task_id)

        async def _wrap() -> None:
            try:
                await self._finish_refresh(decision, was_dirty=was_dirty)
            finally:
                self._refreshing.discard(attempt_id)

        self._workers.add(asyncio.create_task(_wrap()))

    async def _finish_refresh(self, decision: Decision, *, was_dirty: bool) -> None:
        if decision.attempt_id is None or decision.refresh_recipe is None:
            return
        batch = await self.refresh_pool.observe(
            decision.task_id,
            decision.refresh_recipe,
            self.environment,
        )
        sample_time: float | None = None
        for obs in batch.observations:
            stored = self.observations.record(obs, task_id=decision.task_id)
            self._dirty.discard(stored.source_id)
            if sample_time is None or stored.sample_time_mono < sample_time:
                sample_time = stored.sample_time_mono
        self.registry.bump_obs_revision(decision.task_id)
        self.registry.sync_attempt_epochs(decision.attempt_id)
        if was_dirty:
            self._sessions[decision.attempt_id] = [
                {"role": "user", "content": "rebuilt from current observations"}
            ]
        attempt = self.registry.get_attempt(decision.attempt_id)
        if attempt.state == AttemptState.OBSERVING and decision.attempt_id not in self._leases:
            self.registry.update_attempt_state(decision.attempt_id, AttemptState.QUEUED)
        usage: dict = {}
        if sample_time is not None:
            usage["sample_time_mono"] = sample_time
        self.events.emit(
            run_id=self.run_id,
            event_type="observations_refreshed",
            identity={"task_id": decision.task_id, "attempt_id": decision.attempt_id},
            reason=f"n={len(batch.observations)}; {decision.reason}",
            usage=usage,
        )

    def _do_admit(self, decision: Decision) -> None:
        if decision.attempt_id is None or decision.lease is None:
            raise ValueError("admit requires attempt_id and lease")
        self._leases[decision.attempt_id] = decision.lease
        self.registry.update_attempt_state(decision.attempt_id, AttemptState.READY)
        self.events.emit(
            run_id=self.run_id,
            event_type="lease_granted",
            identity={"task_id": decision.task_id, "attempt_id": decision.attempt_id},
            reason=decision.lease.lease_id,
        )

    async def _start_dispatch(self, decision: Decision) -> None:
        if decision.attempt_id is None:
            raise ValueError("dispatch requires attempt_id")
        caps = self.backend.capabilities()
        if self._inflight >= caps.max_inflight:
            self.events.emit(
                run_id=self.run_id,
                event_type="decision_wait",
                identity={"task_id": decision.task_id, "attempt_id": decision.attempt_id},
                reason=f"no free inference slots; waiting task={decision.task_id}",
            )
            return
        attempt_id = decision.attempt_id
        self._dispatch_started[attempt_id] = time.monotonic()
        self._inflight += 1
        self._inflight_attempts[attempt_id] = decision.task_id
        self.registry.update_attempt_state(attempt_id, AttemptState.INFLIGHT)
        if self.inline_dispatch:
            try:
                await self._dispatch_worker(decision)
            finally:
                self._release_slot(attempt_id)
            return

        async def _wrap() -> None:
            try:
                await self._dispatch_worker(decision)
            finally:
                self._release_slot(attempt_id)

        self._workers.add(asyncio.create_task(_wrap()))

    async def _dispatch_worker(self, decision: Decision) -> None:
        attempt_id = decision.attempt_id
        if attempt_id is None:
            return
        lease = self._leases.get(attempt_id)
        if lease is None:
            return
        for _ in range(self.max_turns):
            task = self.registry.get_task(decision.task_id)
            if task.status in _TERMINAL:
                return
            attempt = self.registry.get_attempt(attempt_id)
            if not attempt.eligible:
                return
            latest = self.observations.latest_for_task(decision.task_id)
            request = InferenceRequest(
                attempt_id=attempt.attempt_id,
                task_id=task.task_id,
                plan_epoch=attempt.plan_epoch,
                obs_revision=attempt.obs_revision,
                goal=task.goal,
                observation_ids=[obs.observation_id for obs in latest],
                context=self._inference_context(attempt_id, latest),
                tools=self._catalog(),
            )
            handle = await self.backend.submit(request)
            self._handles[attempt_id] = handle
            self.events.emit(
                run_id=self.run_id,
                event_type="backend_request",
                identity={"task_id": decision.task_id, "attempt_id": attempt_id},
            )
            candidate: CandidateAction | None = None
            tool_call: ToolCall | None = None
            failed_detail: str | None = None
            backend_usage: dict = {}
            async for event in self.backend.events(handle):
                if event.usage and event.usage.get("fault_injected"):
                    backend_usage = dict(event.usage)
                    self.events.emit(
                        run_id=self.run_id,
                        event_type="fault_injected",
                        identity={"task_id": decision.task_id, "attempt_id": attempt_id},
                        reason=str(event.usage.get("fault_kind") or ""),
                        usage=dict(event.usage),
                    )
                if event.kind == "completed":
                    candidate = event.candidate
                    tool_call = event.tool_call
                    if event.usage:
                        backend_usage = dict(event.usage)
                elif event.kind == "failed":
                    failed_detail = event.detail or "backend failed"
            if tool_call is not None and candidate is None:
                spec = self._tool_index().get(tool_call.name)
                if spec is None:
                    self._fail_if_open(decision.task_id, failed_detail or f"unknown tool {tool_call.name}")
                    return
                if spec.role == "read":
                    await self._record_read_tool(decision.task_id, attempt_id, tool_call)
                    continue
                candidate = self._candidate_from_tool(request, tool_call)
            if candidate is None:
                self._fail_if_open(decision.task_id, failed_detail or "backend returned no action")
                return
            self.registry.update_attempt_state(attempt_id, AttemptState.RESULT_READY)
            result = await self.gateway.submit(candidate, lease, usage=backend_usage)
            self._note_service(attempt_id)
            if result.accepted:
                return
            self._handle_rejection(decision.task_id, attempt_id, result.reasons)
            return
        self._fail_if_open(decision.task_id, "exceeded tool turns")

    async def _record_read_tool(self, task_id: str, attempt_id: str, tool_call: ToolCall) -> None:
        recipe = RefreshRecipe(
            read_tool=tool_call.name,
            args=dict(tool_call.arguments),
            sources=[tool_call.name],
            merge_key=tool_call.name,
        )
        batch = await self.refresh_pool.observe(task_id, recipe, self.environment)
        payloads: list[dict] = []
        for obs in batch.observations:
            stored = self.observations.record(obs, task_id=task_id)
            self._dirty.discard(stored.source_id)
            payloads.append(
                {
                    "observation_id": stored.observation_id,
                    "source_id": stored.source_id,
                    "source_version": stored.source_version,
                    "payload": dict(stored.payload),
                }
            )
        self.registry.bump_obs_revision(task_id)
        self.registry.sync_attempt_epochs(attempt_id)
        self._sessions.setdefault(attempt_id, []).append(
            {"role": "tool", "name": tool_call.name, "content": payloads}
        )
        self.events.emit(
            run_id=self.run_id,
            event_type="observations_refreshed",
            identity={"task_id": task_id, "attempt_id": attempt_id},
            reason=f"n={len(batch.observations)}",
        )

    def _candidate_from_tool(self, request: InferenceRequest, tool_call: ToolCall) -> CandidateAction:
        return CandidateAction(
            action_id=self.ids("act"),
            task_id=request.task_id,
            attempt_id=request.attempt_id,
            plan_epoch=request.plan_epoch,
            obs_revision=request.obs_revision,
            action_type=tool_call.name,
            parameters=dict(tool_call.arguments),
            observation_ids=list(request.observation_ids),
        )

    def _estimated_service_s(self) -> float:
        if self._last_service_s is not None:
            return self._last_service_s
        return self._service_prior_s

    def _note_service(self, attempt_id: str) -> None:
        started = self._dispatch_started.pop(attempt_id, None)
        if started is None:
            return
        self._last_service_s = max(0.0, time.monotonic() - started)

    def _do_reject(self, decision: Decision) -> None:
        task = self.registry.get_task(decision.task_id)
        if task.status in _TERMINAL:
            return
        self.events.emit(
            run_id=self.run_id,
            event_type="admission_rejected",
            identity={"task_id": decision.task_id, "attempt_id": decision.attempt_id or ""},
            reason=decision.reason,
            usage={"task_class": task.task_class},
        )
        self.registry.complete_task(decision.task_id, TaskStatus.REJECTED)

    def _handle_rejection(self, task_id: str, attempt_id: str, reasons: list[str]) -> None:
        task = self.registry.get_task(task_id)
        if task.status in _TERMINAL:
            return
        blob = " ".join(reasons).lower()
        replan = any(marker in blob for marker in _REPLAN_MARKERS)
        if replan and task.deadline_mono >= time.monotonic():
            attempt = self.registry.get_attempt(attempt_id)
            if attempt.eligible:
                self.registry.supersede_attempt(attempt_id, reason="stale result")
            if not self._has_open_attempt(task_id):
                self.registry.begin_attempt(task_id)
            return
        self.registry.complete_task(task_id, TaskStatus.REJECTED)

    def _fail_if_open(self, task_id: str, reason: str) -> None:
        task = self.registry.get_task(task_id)
        if task.status in _TERMINAL:
            return
        self.events.emit(
            run_id=self.run_id,
            event_type="backend_failed",
            identity={"task_id": task_id},
            reason=reason,
        )
        self.registry.complete_task(task_id, TaskStatus.FAILED)

    def _do_finish(self, decision: Decision) -> None:
        attempt = None
        if decision.attempt_id is not None:
            attempt = self.registry.get_attempt(decision.attempt_id)
        if attempt is not None and attempt.state == AttemptState.COMMITTED:
            self.registry.complete_task(decision.task_id, TaskStatus.SUCCEEDED)
        else:
            self.registry.complete_task(decision.task_id, TaskStatus.FAILED)

    async def _do_timeout(self, decision: Decision) -> None:
        task = self.registry.get_task(decision.task_id)
        if task.status in _TERMINAL:
            return
        attempts = self.registry.attempts_for_task(decision.task_id)
        if any(attempt.state == AttemptState.COMMITTED for attempt in attempts):
            self.registry.complete_task(decision.task_id, TaskStatus.SUCCEEDED)
            return
        for attempt in attempts:
            if attempt.state == AttemptState.INFLIGHT:
                await self._emit_cancel(attempt.attempt_id, decision.task_id, "deadline")
            if attempt.eligible:
                self.registry.supersede_attempt(attempt.attempt_id, reason="deadline")
        self.registry.complete_task(decision.task_id, TaskStatus.TIMED_OUT)

    async def _do_cancel(self, decision: Decision) -> None:
        task = self.registry.get_task(decision.task_id)
        if task.status in _TERMINAL or decision.attempt_id is None:
            return
        await self._emit_cancel(decision.attempt_id, decision.task_id, decision.reason)
        attempt = self.registry.get_attempt(decision.attempt_id)
        if attempt.eligible:
            self.registry.supersede_attempt(decision.attempt_id, reason="cancelled")
        task = self.registry.get_task(decision.task_id)
        if task.status not in _TERMINAL and task.deadline_mono >= time.monotonic():
            if not self._has_open_attempt(decision.task_id):
                self.registry.begin_attempt(decision.task_id)

    async def _emit_cancel(self, attempt_id: str, task_id: str, reason: str) -> None:
        handle = self._handles.get(attempt_id)
        identity = {"task_id": task_id, "attempt_id": attempt_id}
        self.events.emit(
            run_id=self.run_id,
            event_type="cancel_requested",
            identity=identity,
            reason=reason,
        )
        if handle is None:
            receipt = CancelReceipt(handle_id="", acknowledged=False, evidence="no_handle")
        else:
            receipt = await self.backend.cancel(handle)
        self.events.emit(
            run_id=self.run_id,
            event_type="cancel_acknowledged",
            identity=identity,
            reason=receipt.evidence,
            usage={"acknowledged": receipt.acknowledged},
        )

    def _drop_expired_leases(self) -> None:
        now = time.monotonic()
        for attempt_id, lease in list(self._leases.items()):
            if lease.expires_at_mono >= now:
                continue
            try:
                attempt = self.registry.get_attempt(attempt_id)
            except KeyError:
                self._leases.pop(attempt_id, None)
                continue
            if attempt.state in _SLOT_HELD:
                continue
            self._leases.pop(attempt_id, None)
            self.events.emit(
                run_id=self.run_id,
                event_type="lease_expired",
                identity={"task_id": attempt.task_id, "attempt_id": attempt_id},
                reason="lease expired; re-evaluate admission",
            )

    async def _poll_changes(self) -> None:
        batch = await self.environment.changes(self._cursor)
        self._cursor = batch.next_cursor or self._cursor
        for event in batch.events:
            self._dirty.add(event.source_id)
            self.events.emit(
                run_id=self.run_id,
                event_type="source_dirty",
                identity={"source_id": event.source_id},
                reason=event.kind,
            )

    def _task_dirty(self, task_id: str) -> bool:
        latest = self.observations.latest_for_task(task_id)
        if not latest:
            return bool(self._dirty)
        return any(obs.source_id in self._dirty for obs in latest)

    async def _probe_premises(self) -> None:
        """在途且 dirty 时只调用 check，供调度器决定要不要取消。"""
        self._premise = {}
        effect = next((spec.name for spec in self._catalog() if spec.role == "effect"), None)
        if effect is None:
            return
        for task in self.registry.all_tasks():
            if task.status in _TERMINAL:
                continue
            latest = self.observations.latest_for_task(task.task_id)
            if not any(obs.source_id in self._dirty for obs in latest):
                continue
            for attempt in self.registry.attempts_for_task(task.task_id):
                if attempt.state != AttemptState.INFLIGHT:
                    continue
                probe = CandidateAction(
                    action_id="probe",
                    task_id=task.task_id,
                    attempt_id=attempt.attempt_id,
                    plan_epoch=attempt.plan_epoch,
                    obs_revision=attempt.obs_revision,
                    action_type=effect,
                    parameters={"qty": 1},
                    observation_ids=[obs.observation_id for obs in latest],
                )
                result = await self.environment.check(probe)
                self._premise[attempt.attempt_id] = result.ok

    def _inference_context(self, attempt_id: str, latest: list) -> dict:
        return {
            "observations": [
                {
                    "observation_id": obs.observation_id,
                    "source_id": obs.source_id,
                    "source_version": obs.source_version,
                    "payload": dict(obs.payload),
                }
                for obs in latest
            ],
            "messages": list(self._sessions.get(attempt_id, [])),
        }

    def _catalog(self) -> list[ToolSpec]:
        return list(self.environment.tool_catalog())

    def _tool_index(self) -> dict[str, ToolSpec]:
        return {spec.name: spec for spec in self._catalog()}

    def _has_open_attempt(self, task_id: str) -> bool:
        task = self.registry.get_task(task_id)
        if task.status in _TERMINAL:
            return False
        return any(
            attempt.eligible and attempt.state != AttemptState.COMMITTED
            for attempt in self.registry.attempts_for_task(task_id)
        )

    def _all_terminal(self, tasks: list[Task]) -> bool:
        return all(self.registry.get_task(task.task_id).status in _TERMINAL for task in tasks)

    def _release_slot(self, attempt_id: str) -> None:
        self._inflight = max(0, self._inflight - 1)
        self._inflight_attempts.pop(attempt_id, None)

    async def _reap_workers(self) -> None:
        if not self._workers:
            return
        done, _pending = await asyncio.wait(
            self._workers,
            timeout=0,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            done, _pending = await asyncio.wait(
                self._workers,
                timeout=0.02,
                return_when=asyncio.FIRST_COMPLETED,
            )
        for worker in done:
            self._workers.discard(worker)
            error = worker.exception()
            if error is not None:
                raise error

    async def _drain_workers(self) -> None:
        if not self._workers:
            return
        done, _pending = await asyncio.wait(self._workers)
        self._workers.clear()
        for worker in done:
            error = worker.exception()
            if error is not None:
                raise error
