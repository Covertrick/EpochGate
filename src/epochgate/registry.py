"""任务与尝试的身份登记。计划 epoch 和观测 revision 分开编号。"""

from __future__ import annotations

import time

from epochgate.events import EventLog
from epochgate.ids import IdFactory
from epochgate.models import AttemptState, RequestAttempt, Task, TaskStatus

_TERMINAL = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.TIMED_OUT,
    TaskStatus.REJECTED,
}


class Registry:
    """任务和尝试的权威身份。

    计划修订增加 ``plan_epoch``，使旧结果失去资格。
    观测更新增加 ``obs_revision``，不改写历史请求。
    """

    def __init__(
        self,
        events: EventLog | None = None,
        *,
        run_id: str = "default",
        ids: IdFactory | None = None,
    ) -> None:
        self._tasks: dict[str, Task] = {}
        self._attempts: dict[str, RequestAttempt] = {}
        self._events = events
        self._run_id = run_id
        self._ids = ids or IdFactory(None)

    def register_task(self, task: Task) -> Task:
        """登记到达的任务。

        输入：``task_id`` 唯一的任务。
        输出：存入的任务，状态为 pending。
        副作用：写登记表；可选发出 ``task_registered``，usage 带到达和截止时间。
        失败：重复 ``task_id`` 抛出 ``KeyError``。
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
                usage={
                    "arrival_mono": stored.arrival_mono,
                    "deadline_mono": stored.deadline_mono,
                    "task_class": stored.task_class,
                },
            )
        return stored

    def begin_attempt(
        self,
        task_id: str,
        *,
        plan_epoch: int | None = None,
        obs_revision: int | None = None,
    ) -> RequestAttempt:
        """为任务开启一次新尝试。

        输入：任务 id；可选的 epoch / revision，默认取任务当前值。
        输出：状态为 queued 的新尝试。
        副作用：写登记表；任务变为 running；可选发事件。
        失败：未知任务抛出 ``KeyError``；终态任务抛出 ``ValueError``。
        """
        task = self._require_task(task_id)
        if task.status in _TERMINAL:
            raise ValueError(f"cannot begin attempt on terminal task {task_id}")
        attempt = RequestAttempt(
            attempt_id=self._ids("att"),
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
        """返回 ``(plan_epoch, obs_revision)``。

        输入：任务 id。
        输出：二元组。
        副作用：无。
        失败：未知任务抛出 ``KeyError``。
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
        """撤销一次尝试的结果资格，可选推进 epoch。

        输入：尝试 id、原因、是否推进 plan_epoch 或 obs_revision。
        输出：``eligible=False`` 的尝试。未提交的尝试状态变为 replan。
        副作用：可能增加任务的 plan_epoch 或 obs_revision。
        失败：未知尝试抛出 ``KeyError``。
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
        """按 id 取任务。副作用：无。失败：未知 id 抛出 ``KeyError``。"""
        return self._require_task(task_id)

    def get_attempt(self, attempt_id: str) -> RequestAttempt:
        """按 id 取尝试。副作用：无。失败：未知 id 抛出 ``KeyError``。"""
        return self._require_attempt(attempt_id)

    def update_attempt_state(self, attempt_id: str, state: AttemptState) -> RequestAttempt:
        """更新资源生命周期状态。

        输入：尝试 id 和状态。
        输出：更新后的尝试。
        副作用：写登记表。
        失败：未知尝试抛出 ``KeyError``。
        """
        attempt = self._require_attempt(attempt_id)
        updated = attempt.model_copy(update={"state": state})
        self._attempts[attempt_id] = updated
        return updated

    def complete_task(self, task_id: str, status: TaskStatus) -> Task:
        """把任务标成终态。已经是终态时保持原状态。

        输入：任务 id 和终态。
        输出：任务。
        副作用：首次进入终态时写登记表并发事件。
        失败：未知任务抛出 ``KeyError``；非终态抛出 ``ValueError``。
        """
        if status not in _TERMINAL:
            raise ValueError(f"not a terminal status: {status}")
        task = self._require_task(task_id)
        if task.status in _TERMINAL:
            return task
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
        """成功刷新后推进观测 revision。

        输入：任务 id。
        输出：更新后的任务。
        副作用：``obs_revision`` 加一。
        失败：未知任务抛出 ``KeyError``。
        """
        task = self._require_task(task_id)
        updated = task.model_copy(update={"obs_revision": task.obs_revision + 1})
        self._tasks[task_id] = updated
        return updated

    def sync_attempt_epochs(self, attempt_id: str) -> RequestAttempt:
        """把尝试盖成任务当前的 plan_epoch 和 obs_revision。

        输入：尝试 id。
        输出：更新后的尝试。
        副作用：只改登记表，不改资格。
        失败：未知尝试或任务抛出 ``KeyError``。
        """
        attempt = self._require_attempt(attempt_id)
        plan_epoch, obs_revision = self.current_epoch(attempt.task_id)
        updated = attempt.model_copy(update={"plan_epoch": plan_epoch, "obs_revision": obs_revision})
        self._attempts[attempt_id] = updated
        return updated

    def all_tasks(self) -> list[Task]:
        """返回任务快照。副作用：无。失败：无。"""
        return list(self._tasks.values())

    def all_attempts(self) -> list[RequestAttempt]:
        """返回尝试快照。副作用：无。失败：无。"""
        return list(self._attempts.values())

    def attempts_for_task(self, task_id: str) -> list[RequestAttempt]:
        """返回一个任务的全部尝试。副作用：无。失败：无。"""
        return [attempt for attempt in self._attempts.values() if attempt.task_id == task_id]

    def _require_task(self, task_id: str) -> Task:
        if task_id not in self._tasks:
            raise KeyError(f"unknown task: {task_id}")
        return self._tasks[task_id]

    def _require_attempt(self, attempt_id: str) -> RequestAttempt:
        if attempt_id not in self._attempts:
            raise KeyError(f"unknown attempt: {attempt_id}")
        return self._attempts[attempt_id]


def replay_eligibility(events: list[tuple[str, str]]) -> dict[str, bool]:
    """按事件重放资格。已撤销的尝试不会因进程重启或再次出现 ``attempt_begun`` 而恢复。

    输入：``(event_type, attempt_id)`` 序列。``process_restart`` 不改变资格。
    输出：每个出现过的尝试当前是否仍有资格。
    副作用：无。
    失败：无。
    """
    eligible: dict[str, bool] = {}
    for kind, attempt_id in events:
        if not attempt_id:
            continue
        if kind == "attempt_begun":
            eligible.setdefault(attempt_id, True)
        elif kind == "eligibility_revoked":
            eligible[attempt_id] = False
    return eligible
