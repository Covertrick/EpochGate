"""Task / attempt registry isolating plan_epoch from observation revision."""

from __future__ import annotations

import time
import uuid

from epochgate.events import EventLog
from epochgate.models import AttemptState, RequestAttempt, Task, TaskStatus


class Registry:
    """Authoritative identity for tasks and attempts.

    Plan revisions bump ``plan_epoch`` (invalidate prior result eligibility).
    Observation updates bump ``obs_revision`` without rewriting history.
    """

    def __init__(self, events: EventLog | None = None, *, run_id: str = "default") -> None:
        self._tasks: dict[str, Task] = {}
        self._attempts: dict[str, RequestAttempt] = {}
        self._events = events
        self._run_id = run_id

    def register_task(self, task: Task) -> Task:
        """Register an arriving task.

        Inputs: Task with unique ``task_id``.
        Outputs: stored Task (status forced to pending if not set).
        Side effects: registry write; optional ``task_registered`` event.
        Failures: duplicate ``task_id`` → ``KeyError``.
        """
        if task.task_id in self._tasks:
            raise KeyError(f"task already registered: {task.task_id}")
        stored = task.model_copy(update={"status": TaskStatus.PENDING})
        self._tasks[task.task_id] = stored
        if self._events is not None:
            self._events.emit(
                run_id=self._run_id,
                event_type="task_registered",
                identity={"task_id": task.task_id},
            )
        return stored

    def begin_attempt(
        self,
        task_id: str,
        *,
        plan_epoch: int | None = None,
        obs_revision: int | None = None,
    ) -> RequestAttempt:
        """Open a new RequestAttempt for a task.

        Inputs: ``task_id``; optional epoch/revision override (default: current).
        Outputs: new attempt in ``queued`` with fresh ``attempt_id``.
        Side effects: registry write; task → running; optional event.
        Failures: unknown task → ``KeyError``; terminal task → ``ValueError``.
        """
        task = self._require_task(task_id)
        if task.status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.TIMED_OUT,
            TaskStatus.REJECTED,
        }:
            raise ValueError(f"cannot begin attempt on terminal task {task_id}")
        attempt = RequestAttempt(
            attempt_id=f"att-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            plan_epoch=task.plan_epoch if plan_epoch is None else plan_epoch,
            obs_revision=task.obs_revision if obs_revision is None else obs_revision,
            state=AttemptState.QUEUED,
            eligible=True,
            created_mono=time.monotonic(),
        )
        self._attempts[attempt.attempt_id] = attempt
        self._tasks[task_id] = task.model_copy(update={"status": TaskStatus.RUNNING})
        if self._events is not None:
            self._events.emit(
                run_id=self._run_id,
                event_type="attempt_begun",
                identity={"task_id": task_id, "attempt_id": attempt.attempt_id},
            )
        return attempt

    def current_epoch(self, task_id: str) -> tuple[int, int]:
        """Return ``(plan_epoch, obs_revision)``.

        Inputs: ``task_id``. Outputs: tuple. Side effects: none.
        Failures: unknown task → ``KeyError``.
        """
        task = self._require_task(task_id)
        return task.plan_epoch, task.obs_revision

    def supersede_attempt(
        self,
        attempt_id: str,
        *,
        reason: str,
        bump_plan_epoch: bool = False,
        bump_obs_revision: bool = False,
    ) -> RequestAttempt:
        """Revoke result eligibility for an attempt; optionally bump epochs.

        Inputs: ``attempt_id``, reason, bump flags.
        Outputs: updated attempt with ``eligible=False``.
        Side effects: may increment task plan_epoch / obs_revision.
        Failures: unknown attempt → ``KeyError``.
        """
        attempt = self._require_attempt(attempt_id)
        task = self._require_task(attempt.task_id)
        updates: dict = {"eligible": False, "reason": reason}
        if attempt.state not in {AttemptState.COMMITTED}:
            updates["state"] = AttemptState.REPLAN
        superseded = attempt.model_copy(update=updates)
        self._attempts[attempt_id] = superseded

        task_updates: dict = {}
        if bump_plan_epoch:
            task_updates["plan_epoch"] = task.plan_epoch + 1
        if bump_obs_revision:
            task_updates["obs_revision"] = task.obs_revision + 1
        if task_updates:
            self._tasks[task.task_id] = task.model_copy(update=task_updates)

        if self._events is not None:
            self._events.emit(
                run_id=self._run_id,
                event_type="attempt_superseded",
                identity={"task_id": attempt.task_id, "attempt_id": attempt_id},
                reason=reason,
            )
        return superseded

    def get_task(self, task_id: str) -> Task:
        """Lookup task. Side effects: none. Failures: ``KeyError``."""
        return self._require_task(task_id)

    def get_attempt(self, attempt_id: str) -> RequestAttempt:
        """Lookup attempt. Side effects: none. Failures: ``KeyError``."""
        return self._require_attempt(attempt_id)

    def update_attempt_state(self, attempt_id: str, state: AttemptState) -> RequestAttempt:
        """Set resource lifecycle state. Side effects: registry write. Failures: ``KeyError``."""
        attempt = self._require_attempt(attempt_id)
        updated = attempt.model_copy(update={"state": state})
        self._attempts[attempt_id] = updated
        return updated

    def complete_task(self, task_id: str, status: TaskStatus) -> Task:
        """Mark task terminal. Side effects: registry write. Failures: ``KeyError`` / ``ValueError``."""
        if status not in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.TIMED_OUT,
            TaskStatus.REJECTED,
        }:
            raise ValueError(f"not a terminal status: {status}")
        task = self._require_task(task_id)
        updated = task.model_copy(update={"status": status})
        self._tasks[task_id] = updated
        if self._events is not None:
            self._events.emit(
                run_id=self._run_id,
                event_type="task_completed",
                identity={"task_id": task_id},
                reason=status.value,
            )
        return updated

    def bump_obs_revision(self, task_id: str) -> Task:
        """Advance observation revision after a successful refresh.

        Inputs: ``task_id``. Outputs: updated Task.
        Side effects: ``obs_revision += 1``. Failures: ``KeyError``.
        """
        task = self._require_task(task_id)
        updated = task.model_copy(update={"obs_revision": task.obs_revision + 1})
        self._tasks[task_id] = updated
        return updated

    def sync_attempt_epochs(self, attempt_id: str) -> RequestAttempt:
        """Stamp an attempt with the task's current plan_epoch / obs_revision.

        Inputs: ``attempt_id``.
        Outputs: updated RequestAttempt.
        Side effects: registry write only; does not change eligibility.
        Failures: unknown attempt/task → ``KeyError``.
        """
        attempt = self._require_attempt(attempt_id)
        plan_epoch, obs_revision = self.current_epoch(attempt.task_id)
        updated = attempt.model_copy(update={"plan_epoch": plan_epoch, "obs_revision": obs_revision})
        self._attempts[attempt_id] = updated
        return updated

    def all_tasks(self) -> list[Task]:
        """Snapshot of tasks. Side effects: none."""
        return list(self._tasks.values())

    def all_attempts(self) -> list[RequestAttempt]:
        """Snapshot of attempts. Side effects: none."""
        return list(self._attempts.values())

    def attempts_for_task(self, task_id: str) -> list[RequestAttempt]:
        """Attempts for one task. Side effects: none."""
        return [a for a in self._attempts.values() if a.task_id == task_id]

    def _require_task(self, task_id: str) -> Task:
        if task_id not in self._tasks:
            raise KeyError(f"unknown task: {task_id}")
        return self._tasks[task_id]

    def _require_attempt(self, attempt_id: str) -> RequestAttempt:
        if attempt_id not in self._attempts:
            raise KeyError(f"unknown attempt: {attempt_id}")
        return self._attempts[attempt_id]
