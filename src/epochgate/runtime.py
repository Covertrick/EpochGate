"""Runtime orchestrator: snapshot → decide → execute. Never calls env.commit."""

from __future__ import annotations

import time
import uuid

from epochgate.backend import InferenceBackend
from epochgate.environment import EnvironmentAdapter
from epochgate.events import EventLog
from epochgate.gateway import ActionGateway
from epochgate.models import (
    AdmissionLease,
    AttemptState,
    AttemptView,
    BackendCapacityView,
    Decision,
    InferenceRequest,
    ObservationView,
    RefreshRecipe,
    SchedulerSnapshot,
    Task,
    TaskStatus,
    TaskView,
)
from epochgate.observation import ObservationStore
from epochgate.registry import Registry
from epochgate.scheduler import HappyPathScheduler, Scheduler


class Runtime:
    """Wires registry, observations, scheduler, backend, and gateway.

    Dependency direction: Runtime → collaborators → models.
    Only ActionGateway may call ``environment.commit``.
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
        max_steps: int = 16,
    ) -> None:
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
        self.events = events or EventLog()
        self.registry = Registry(self.events, run_id=self.run_id)
        self.observations = ObservationStore()
        self.environment = environment
        self.backend = backend
        self.scheduler = scheduler or HappyPathScheduler()
        self.gateway = ActionGateway(
            environment,
            self.registry,
            self.observations,
            self.events,
            run_id=self.run_id,
        )
        self.default_refresh = default_refresh or RefreshRecipe(
            read_tool="read_inventory",
            sources=["inventory"],
            merge_key="inventory",
        )
        self.max_steps = max_steps
        self._leases: dict[str, AdmissionLease] = {}
        self._inflight = 0

    async def run_task(self, task: Task) -> Task:
        """Execute one task to a terminal status via the happy-path loop.

        Inputs: Task (arrival/deadline in monotonic time).
        Outputs: terminal Task from the registry.
        Side effects: drives observe / admit / dispatch / gateway submit;
        never calls ``environment.commit`` directly.
        Failures: step budget exhausted → ``RuntimeError``.
        """
        self.registry.register_task(task)
        attempt = self.registry.begin_attempt(task.task_id)
        self.events.emit(
            run_id=self.run_id,
            event_type="runtime_start",
            identity={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
        )

        for _ in range(self.max_steps):
            current = self.registry.get_task(task.task_id)
            if current.status in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.TIMED_OUT,
                TaskStatus.REJECTED,
            }:
                return current

            snapshot = self.build_snapshot(task_id=task.task_id)
            decisions = self.scheduler.decide(snapshot)
            if not decisions:
                raise RuntimeError("scheduler returned no decisions before terminal state")

            for decision in decisions:
                await self._apply(decision)

            current = self.registry.get_task(task.task_id)
            if current.status in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.TIMED_OUT,
                TaskStatus.REJECTED,
            }:
                return current

        raise RuntimeError(f"exceeded max_steps={self.max_steps} for task {task.task_id}")

    def build_snapshot(self, *, task_id: str | None = None) -> SchedulerSnapshot:
        """Assemble a cropped SchedulerSnapshot. Side effects: none on stores."""
        tasks = self.registry.all_tasks()
        if task_id is not None:
            tasks = [t for t in tasks if t.task_id == task_id]

        task_views = [
            TaskView(
                task_id=t.task_id,
                status=t.status,
                goal=t.goal,
                plan_epoch=t.plan_epoch,
                obs_revision=t.obs_revision,
                deadline_mono=t.deadline_mono,
            )
            for t in tasks
        ]

        attempt_views: list[AttemptView] = []
        obs_views: list[ObservationView] = []
        for t in tasks:
            latest = self.observations.latest_for_task(t.task_id)
            for obs in latest:
                obs_views.append(
                    ObservationView(
                        observation_id=obs.observation_id,
                        source_id=obs.source_id,
                        source_version=obs.source_version,
                        sample_time_mono=obs.sample_time_mono,
                        task_id=t.task_id,
                    )
                )
            for attempt in self.registry.attempts_for_task(t.task_id):
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
            capacity=BackendCapacityView(free_slots=free, supports_cancel=caps.supports_cancel),
            default_refresh=self.default_refresh,
        )

    async def _apply(self, decision: Decision) -> None:
        if decision.kind == "refresh":
            await self._do_refresh(decision)
        elif decision.kind == "admit":
            self._do_admit(decision)
        elif decision.kind == "dispatch":
            await self._do_dispatch(decision)
        elif decision.kind == "finish":
            self._do_finish(decision)
        elif decision.kind == "wait":
            self.events.emit(
                run_id=self.run_id,
                event_type="decision_wait",
                identity={"task_id": decision.task_id, "attempt_id": decision.attempt_id or ""},
                reason=decision.reason,
            )
        else:
            raise ValueError(f"unknown decision kind: {decision.kind}")

    async def _do_refresh(self, decision: Decision) -> None:
        if decision.attempt_id is None or decision.refresh_recipe is None:
            raise ValueError("refresh requires attempt_id and refresh_recipe")
        self.registry.update_attempt_state(decision.attempt_id, AttemptState.OBSERVING)
        batch = await self.environment.observe(decision.refresh_recipe)
        for obs in batch.observations:
            self.observations.record(obs, task_id=decision.task_id)
        # Keep attempt identity; bump task obs_revision and re-stamp attempt view via begin? 
        # For v0: bump task revision and update attempt's obs_revision field by superseding eligibility? 
        # Simpler: bump task obs_revision and patch attempt through a small registry helper.
        self.registry.bump_obs_revision(decision.task_id)
        self.registry.sync_attempt_epochs(decision.attempt_id)
        self.events.emit(
            run_id=self.run_id,
            event_type="observations_refreshed",
            identity={"task_id": decision.task_id, "attempt_id": decision.attempt_id},
            reason=f"n={len(batch.observations)}",
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

    async def _do_dispatch(self, decision: Decision) -> None:
        if decision.attempt_id is None:
            raise ValueError("dispatch requires attempt_id")
        attempt = self.registry.get_attempt(decision.attempt_id)
        task = self.registry.get_task(decision.task_id)
        lease = self._leases.get(decision.attempt_id)
        if lease is None:
            raise RuntimeError("dispatch without lease")

        latest = self.observations.latest_for_task(decision.task_id)
        self.registry.update_attempt_state(decision.attempt_id, AttemptState.INFLIGHT)
        self._inflight += 1
        try:
            handle = await self.backend.submit(
                InferenceRequest(
                    attempt_id=attempt.attempt_id,
                    task_id=task.task_id,
                    plan_epoch=attempt.plan_epoch,
                    obs_revision=attempt.obs_revision,
                    goal=task.goal,
                    observation_ids=[o.observation_id for o in latest],
                )
            )
            candidate = None
            async for event in self.backend.events(handle):
                if event.kind == "completed":
                    candidate = event.candidate
            if candidate is None:
                self.registry.update_attempt_state(decision.attempt_id, AttemptState.REJECTED)
                self.registry.complete_task(decision.task_id, TaskStatus.FAILED)
                return

            self.registry.update_attempt_state(decision.attempt_id, AttemptState.RESULT_READY)
            # Only gateway may commit.
            result = await self.gateway.submit(candidate, lease)
            if not result.accepted:
                self.registry.complete_task(decision.task_id, TaskStatus.REJECTED)
        finally:
            self._inflight = max(0, self._inflight - 1)

    def _do_finish(self, decision: Decision) -> None:
        attempt = None
        if decision.attempt_id is not None:
            attempt = self.registry.get_attempt(decision.attempt_id)
        if attempt is not None and attempt.state == AttemptState.COMMITTED:
            self.registry.complete_task(decision.task_id, TaskStatus.SUCCEEDED)
        else:
            self.registry.complete_task(decision.task_id, TaskStatus.FAILED)
