"""只读快照上的调度策略。本模块不得 import 网关、环境或后端实现。"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from epochgate.ids import IdFactory
from epochgate.models import (
    AdmissionLease,
    AttemptState,
    AttemptView,
    Decision,
    SchedulerSnapshot,
    TaskStatus,
    TaskView,
)

_TERMINAL = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.TIMED_OUT,
    TaskStatus.REJECTED,
}

_IN_FLIGHT = {
    AttemptState.INFLIGHT,
    AttemptState.RESULT_READY,
    AttemptState.VALIDATING,
}


@runtime_checkable
class Scheduler(Protocol):
    """调度策略。只根据快照给出意图。"""

    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        """由裁剪快照产生意图。

        输入：``SchedulerSnapshot``。没有仓库活引用，也没有 evaluator。
        输出：``Decision`` 列表，可以为空。
        副作用：无。
        失败：快照时间非法时抛出 ``ValueError``。
        """
        ...


class HappyPathScheduler:
    """演示用固定规则，不含打分。"""

    def __init__(self, *, lease_ttl_s: float = 30.0, ids: IdFactory | None = None) -> None:
        self._lease_ttl_s = lease_ttl_s
        self._ids = ids or IdFactory(None)

    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        """无观测则刷新，然后准入，有槽则派发。

        输入：快照。
        输出：决策列表。
        副作用：无。
        失败：时间为非正或缺少默认配方时抛出 ``ValueError``。
        """
        self._require_now(snapshot)
        tasks = {t.task_id: t for t in snapshot.tasks}
        decisions: list[Decision] = []
        timed: set[str] = set()
        slots = snapshot.capacity.free_slots
        for attempt in snapshot.attempts:
            task = tasks.get(attempt.task_id)
            if task is None or task.status in _TERMINAL or not attempt.eligible:
                continue
            if self._append_timeout(decisions, timed, task, attempt, snapshot):
                continue
            if attempt.state == AttemptState.COMMITTED:
                decisions.append(_finish(attempt, "attempt committed; complete task"))
                continue
            if attempt.refresh_inflight:
                decisions.append(_wait(attempt, "refresh in flight"))
                continue
            if not attempt.has_observations and attempt.state not in _IN_FLIGHT:
                decisions.append(self._refresh(snapshot, attempt, "missing observations; refresh before admit"))
                continue
            if attempt.state not in _IN_FLIGHT and (attempt.lease_id is None or _lease_expired(attempt, snapshot)):
                decisions.append(self._admit(snapshot, attempt, "observations ready; grant short admission lease"))
                continue
            if attempt.state == AttemptState.READY:
                if slots <= 0:
                    decisions.append(_wait_slot(snapshot, attempt))
                    continue
                slots -= 1
                decisions.append(_dispatch(attempt, "lease valid; dispatch to backend"))
                continue
            decisions.append(_wait(attempt, f"in progress ({attempt.state.value})"))
        return decisions

    def _refresh(self, snapshot: SchedulerSnapshot, attempt: AttemptView, reason: str) -> Decision:
        if snapshot.default_refresh is None:
            raise ValueError("default_refresh required when observations are missing")
        return Decision(
            kind="refresh",
            task_id=attempt.task_id,
            attempt_id=attempt.attempt_id,
            reason=reason,
            refresh_recipe=snapshot.default_refresh,
        )

    def _admit(self, snapshot: SchedulerSnapshot, attempt: AttemptView, reason: str) -> Decision:
        lease = AdmissionLease(
            lease_id=self._ids("lease"),
            attempt_id=attempt.attempt_id,
            earliest_start_mono=snapshot.now_mono,
            expires_at_mono=snapshot.now_mono + self._lease_ttl_s,
        )
        return Decision(
            kind="admit",
            task_id=attempt.task_id,
            attempt_id=attempt.attempt_id,
            reason=reason,
            lease=lease,
        )

    @staticmethod
    def _require_now(snapshot: SchedulerSnapshot) -> None:
        if snapshot.now_mono <= 0:
            raise ValueError("snapshot.now_mono must be positive monotonic time")

    @staticmethod
    def _append_timeout(
        decisions: list[Decision],
        timed: set[str],
        task: TaskView,
        attempt: AttemptView,
        snapshot: SchedulerSnapshot,
    ) -> bool:
        if task.deadline_mono >= snapshot.now_mono:
            return False
        if task.task_id not in timed:
            decisions.append(
                Decision(
                    kind="timeout",
                    task_id=task.task_id,
                    attempt_id=attempt.attempt_id,
                    reason=f"deadline passed; waiting task={task.task_id}",
                )
            )
            timed.add(task.task_id)
        return True


class RuleScheduler:
    """规则联合策略：截止时间、dirty 刷新、有槽则按最早截止派发，在途 dirty 时取消。"""

    def __init__(self, *, lease_ttl_s: float = 30.0, ids: IdFactory | None = None) -> None:
        self._lease_ttl_s = lease_ttl_s
        self._ids = ids or IdFactory(None)

    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        """按规则给出刷新、准入、派发、取消、超时或等待。

        输入：快照。
        输出：决策列表。派发顺序是最早截止，其次到达时间。
        副作用：无。
        失败：时间为非正或需要刷新但没有配方时抛出 ``ValueError``。
        """
        HappyPathScheduler._require_now(snapshot)
        tasks = {t.task_id: t for t in snapshot.tasks}
        ordered = sorted(
            snapshot.attempts,
            key=lambda attempt: (
                tasks[attempt.task_id].deadline_mono if attempt.task_id in tasks else 0.0,
                tasks[attempt.task_id].arrival_mono if attempt.task_id in tasks else 0.0,
                attempt.attempt_id,
            ),
        )
        decisions: list[Decision] = []
        timed: set[str] = set()
        slots = snapshot.capacity.free_slots
        helper = HappyPathScheduler(lease_ttl_s=self._lease_ttl_s, ids=self._ids)
        for attempt in ordered:
            task = tasks.get(attempt.task_id)
            if task is None or task.status in _TERMINAL or not attempt.eligible:
                continue
            if helper._append_timeout(decisions, timed, task, attempt, snapshot):
                continue
            if attempt.state == AttemptState.COMMITTED:
                decisions.append(_finish(attempt, "attempt committed; complete task"))
                continue
            if attempt.refresh_inflight:
                decisions.append(_wait(attempt, "refresh in flight"))
                continue
            if (
                attempt.state == AttemptState.INFLIGHT
                and attempt.sources_dirty
                and snapshot.capacity.supports_cancel
            ):
                decisions.append(
                    Decision(
                        kind="cancel",
                        task_id=attempt.task_id,
                        attempt_id=attempt.attempt_id,
                        reason="dirty during inference; revoke eligibility",
                    )
                )
                continue
            needs_refresh = (not attempt.has_observations or attempt.sources_dirty) and attempt.state not in _IN_FLIGHT
            if needs_refresh:
                if attempt.refreshes_used >= snapshot.refresh_budget:
                    decisions.append(_wait(attempt, "refresh budget exhausted"))
                    continue
                decisions.append(helper._refresh(snapshot, attempt, "refresh for missing or dirty observations"))
                continue
            if attempt.state not in _IN_FLIGHT and (
                attempt.lease_id is None or _lease_expired(attempt, snapshot)
            ):
                decisions.append(helper._admit(snapshot, attempt, "grant admission after freshness check"))
                continue
            if attempt.state == AttemptState.READY:
                if slots <= 0:
                    decisions.append(_wait_slot(snapshot, attempt))
                    continue
                slots -= 1
                decisions.append(
                    _dispatch(attempt, "edf then fcfs fallback; lease valid; dispatch to backend")
                )
                continue
            decisions.append(_wait(attempt, f"in progress ({attempt.state.value})"))
        return decisions


class FcfsEnqueueScheduler:
    """B0：入队时观察一次，之后按到达顺序派发。忽略 dirty，不发取消。"""

    def __init__(self, *, lease_ttl_s: float = 30.0, ids: IdFactory | None = None) -> None:
        self._lease_ttl_s = lease_ttl_s
        self._ids = ids or IdFactory(None)

    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        """没有观测才刷新；已有观测后不再因 dirty 刷新。

        输入：快照。
        输出：按到达顺序的决策。
        副作用：无。
        失败：时间为非正或需要刷新但没有配方时抛出 ``ValueError``。
        """
        HappyPathScheduler._require_now(snapshot)
        tasks = {t.task_id: t for t in snapshot.tasks}
        ordered = sorted(
            snapshot.attempts,
            key=lambda attempt: (
                tasks[attempt.task_id].arrival_mono if attempt.task_id in tasks else 0.0,
                attempt.attempt_id,
            ),
        )
        decisions: list[Decision] = []
        timed: set[str] = set()
        slots = snapshot.capacity.free_slots
        helper = HappyPathScheduler(lease_ttl_s=self._lease_ttl_s, ids=self._ids)
        for attempt in ordered:
            task = tasks.get(attempt.task_id)
            if task is None or task.status in _TERMINAL or not attempt.eligible:
                continue
            if helper._append_timeout(decisions, timed, task, attempt, snapshot):
                continue
            if attempt.state == AttemptState.COMMITTED:
                decisions.append(_finish(attempt, "attempt committed; complete task"))
                continue
            if attempt.refresh_inflight:
                decisions.append(_wait(attempt, "refresh in flight"))
                continue
            if not attempt.has_observations and attempt.state not in _IN_FLIGHT:
                decisions.append(helper._refresh(snapshot, attempt, "fcfs observe once at enqueue"))
                continue
            if attempt.state not in _IN_FLIGHT and (
                attempt.lease_id is None or _lease_expired(attempt, snapshot)
            ):
                decisions.append(helper._admit(snapshot, attempt, "fcfs enqueue; grant admission"))
                continue
            if attempt.state == AttemptState.READY:
                if slots <= 0:
                    decisions.append(_wait_slot(snapshot, attempt))
                    continue
                slots -= 1
                decisions.append(_dispatch(attempt, "fcfs enqueue; dispatch to backend"))
                continue
            decisions.append(_wait(attempt, f"in progress ({attempt.state.value})"))
        return decisions


class EdfFixedScheduler:
    """B1：最早截止优先。只在没有观测时观察一次，不因 dirty 重刷或取消。"""

    def __init__(self, *, lease_ttl_s: float = 30.0, ids: IdFactory | None = None) -> None:
        self._lease_ttl_s = lease_ttl_s
        self._ids = ids or IdFactory(None)

    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        """按截止时间派发，观察策略固定。

        输入：快照。
        输出：决策列表。
        副作用：无。
        失败：时间为非正或需要刷新但没有配方时抛出 ``ValueError``。
        """
        return _ordered_once(
            snapshot,
            order="deadline",
            helper=HappyPathScheduler(lease_ttl_s=self._lease_ttl_s, ids=self._ids),
            refresh_reason="b1 observe once; ignore dirty",
            admit_reason="b1 fixed observation; grant admission",
            dispatch_reason="b1 edf; dispatch to backend",
        )


class FcfsRecheckScheduler:
    """B2：先来先服务，派发前再观察一次。不因 dirty 取消。"""

    def __init__(self, *, lease_ttl_s: float = 30.0, ids: IdFactory | None = None) -> None:
        self._lease_ttl_s = lease_ttl_s
        self._ids = ids or IdFactory(None)

    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        """入队观察一次；准备派发且只观察过一次时，先再刷一次。

        输入：快照。
        输出：按到达顺序的决策。
        副作用：无。
        失败：时间为非正或需要刷新但没有配方时抛出 ``ValueError``。
        """
        HappyPathScheduler._require_now(snapshot)
        tasks = {item.task_id: item for item in snapshot.tasks}
        ordered = sorted(
            snapshot.attempts,
            key=lambda attempt: (
                tasks[attempt.task_id].arrival_mono if attempt.task_id in tasks else 0.0,
                attempt.attempt_id,
            ),
        )
        decisions: list[Decision] = []
        timed: set[str] = set()
        slots = snapshot.capacity.free_slots
        helper = HappyPathScheduler(lease_ttl_s=self._lease_ttl_s, ids=self._ids)
        for attempt in ordered:
            task = tasks.get(attempt.task_id)
            if task is None or task.status in _TERMINAL or not attempt.eligible:
                continue
            if helper._append_timeout(decisions, timed, task, attempt, snapshot):
                continue
            if attempt.state == AttemptState.COMMITTED:
                decisions.append(_finish(attempt, "attempt committed; complete task"))
                continue
            if attempt.refresh_inflight:
                decisions.append(_wait(attempt, "refresh in flight"))
                continue
            if not attempt.has_observations and attempt.state not in _IN_FLIGHT:
                decisions.append(helper._refresh(snapshot, attempt, "b2 observe once at enqueue"))
                continue
            if attempt.state not in _IN_FLIGHT and (
                attempt.lease_id is None or _lease_expired(attempt, snapshot)
            ):
                decisions.append(helper._admit(snapshot, attempt, "b2 fcfs; grant admission"))
                continue
            if attempt.state == AttemptState.READY:
                if slots <= 0:
                    decisions.append(_wait_slot(snapshot, attempt))
                    continue
                if attempt.refreshes_used == 1:
                    slots -= 1
                    decisions.append(helper._refresh(snapshot, attempt, "b2 recheck before dispatch"))
                    continue
                slots -= 1
                decisions.append(_dispatch(attempt, "b2 fcfs after recheck; dispatch to backend"))
                continue
            decisions.append(_wait(attempt, f"in progress ({attempt.state.value})"))
        return decisions


class NearAdmissionScheduler:
    """临近准入：同一轮可以同时发租约和刷新，两者都就绪才派发。"""

    def __init__(self, *, lease_ttl_s: float = 30.0, ids: IdFactory | None = None) -> None:
        self._lease_ttl_s = lease_ttl_s
        self._ids = ids or IdFactory(None)

    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        """刷新与准入并行。前提仍成立时不因 dirty 取消。

        输入：快照。``premise_holds is False`` 才允许取消在途尝试。
        输出：决策列表。顺序是最早截止，其次到达时间。
        副作用：无。
        失败：时间为非正或需要刷新但没有配方时抛出 ``ValueError``。
        """
        HappyPathScheduler._require_now(snapshot)
        tasks = {item.task_id: item for item in snapshot.tasks}
        ordered = sorted(
            snapshot.attempts,
            key=lambda attempt: (
                tasks[attempt.task_id].deadline_mono if attempt.task_id in tasks else 0.0,
                tasks[attempt.task_id].arrival_mono if attempt.task_id in tasks else 0.0,
                attempt.attempt_id,
            ),
        )
        decisions: list[Decision] = []
        timed: set[str] = set()
        slots = snapshot.capacity.free_slots
        helper = HappyPathScheduler(lease_ttl_s=self._lease_ttl_s, ids=self._ids)
        for attempt in ordered:
            task = tasks.get(attempt.task_id)
            if task is None or task.status in _TERMINAL or not attempt.eligible:
                continue
            if helper._append_timeout(decisions, timed, task, attempt, snapshot):
                continue
            if attempt.state == AttemptState.COMMITTED:
                decisions.append(_finish(attempt, "attempt committed; complete task"))
                continue
            if (
                attempt.state == AttemptState.INFLIGHT
                and attempt.sources_dirty
                and attempt.premise_holds is False
                and snapshot.capacity.supports_cancel
            ):
                decisions.append(
                    Decision(
                        kind="cancel",
                        task_id=attempt.task_id,
                        attempt_id=attempt.attempt_id,
                        reason="dirty and premise failed; revoke eligibility",
                    )
                )
                continue
            if _unreachable(snapshot, task) and attempt.state not in _IN_FLIGHT:
                decisions.append(
                    Decision(
                        kind="reject",
                        task_id=attempt.task_id,
                        attempt_id=attempt.attempt_id,
                        reason="infeasible before deadline",
                    )
                )
                continue
            if attempt.refresh_inflight:
                if attempt.state not in _IN_FLIGHT and (
                    attempt.lease_id is None or _lease_expired(attempt, snapshot)
                ):
                    decisions.append(
                        _admit_with_slo(snapshot, attempt, task, "near admission; lease while refresh runs", self._ids, self._lease_ttl_s)
                    )
                else:
                    decisions.append(_wait(attempt, "refresh in flight"))
                continue
            needs_refresh = (not attempt.has_observations or attempt.sources_dirty) and attempt.state not in _IN_FLIGHT
            if needs_refresh:
                if attempt.refreshes_used >= snapshot.refresh_budget:
                    decisions.append(_wait(attempt, "refresh budget exhausted"))
                else:
                    decisions.append(helper._refresh(snapshot, attempt, "near admission; refresh in parallel with lease"))
            if attempt.state not in _IN_FLIGHT and (
                attempt.lease_id is None or _lease_expired(attempt, snapshot)
            ):
                decisions.append(
                    _admit_with_slo(snapshot, attempt, task, "near admission; grant short lease", self._ids, self._lease_ttl_s)
                )
                continue
            if (
                attempt.state == AttemptState.READY
                and attempt.has_observations
                and not needs_refresh
            ):
                if slots <= 0:
                    decisions.append(_wait_slot(snapshot, attempt))
                    continue
                slots -= 1
                decisions.append(_dispatch(attempt, "near admission; observation and lease both ready"))
                continue
            if not needs_refresh:
                decisions.append(_wait(attempt, f"in progress ({attempt.state.value})"))
        return decisions


class FifoAdmissionScheduler:
    """F4 队列公平性合同专用调度器：严格 FIFO 队列顺序。

    冻结规则：
    1. 初始任务 attempt（plan_epoch == 0 且为该 task 首 attempt）按 task.arrival_mono 入队；
    2. 被取消或被拒后的重规划 attempt 按其 attempt.created_mono 进入队尾（A/B 使用完全相同的入队规则，无特权插队）；
    3. 同一时刻按 attempt_id 稳定打破平局；
    4. 仅当 free_slots > 0 时按队列顺序派发队首就绪 attempt。
    """

    def __init__(self, *, lease_ttl_s: float = 30.0, replan_to_tail: bool = True, ids: IdFactory | None = None) -> None:
        self._lease_ttl_s = lease_ttl_s
        self._replan_to_tail = replan_to_tail
        self._ids = ids or IdFactory(None)

    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        HappyPathScheduler._require_now(snapshot)
        tasks = {item.task_id: item for item in snapshot.tasks}
        first_att_by_task: dict[str, str] = {}
        for att in snapshot.attempts:
            if att.task_id not in first_att_by_task:
                first_att_by_task[att.task_id] = att.attempt_id

        def _fifo_key(attempt: AttemptView) -> tuple[float, str]:
            task = tasks.get(attempt.task_id)
            arrival = task.arrival_mono if task is not None else 0.0
            is_replan = first_att_by_task.get(attempt.task_id) != attempt.attempt_id
            if self._replan_to_tail and is_replan and attempt.created_mono > 0.0:
                queue_ts = max(arrival, attempt.created_mono)
            else:
                queue_ts = arrival
            return (queue_ts, attempt.attempt_id)

        ordered = sorted(snapshot.attempts, key=_fifo_key)
        decisions: list[Decision] = []
        timed: set[str] = set()
        slots = snapshot.capacity.free_slots
        helper = HappyPathScheduler(lease_ttl_s=self._lease_ttl_s, ids=self._ids)
        for attempt in ordered:
            task = tasks.get(attempt.task_id)
            if task is None or task.status in _TERMINAL or not attempt.eligible:
                continue
            if helper._append_timeout(decisions, timed, task, attempt, snapshot):
                continue
            if attempt.state == AttemptState.COMMITTED:
                decisions.append(_finish(attempt, "attempt committed; complete task"))
                continue
            if (
                attempt.state == AttemptState.INFLIGHT
                and attempt.sources_dirty
                and attempt.premise_holds is False
                and snapshot.capacity.supports_cancel
            ):
                decisions.append(
                    Decision(
                        kind="cancel",
                        task_id=attempt.task_id,
                        attempt_id=attempt.attempt_id,
                        reason="dirty and premise failed; revoke eligibility",
                    )
                )
                continue
            if _unreachable(snapshot, task) and attempt.state not in _IN_FLIGHT:
                decisions.append(
                    Decision(
                        kind="reject",
                        task_id=attempt.task_id,
                        attempt_id=attempt.attempt_id,
                        reason="infeasible before deadline",
                    )
                )
                continue
            if attempt.refresh_inflight:
                if attempt.state not in _IN_FLIGHT and (
                    attempt.lease_id is None or _lease_expired(attempt, snapshot)
                ):
                    decisions.append(
                        _admit_with_slo(snapshot, attempt, task, "fifo; lease while refresh runs", self._ids, self._lease_ttl_s)
                    )
                else:
                    decisions.append(_wait(attempt, "refresh in flight"))
                continue
            needs_refresh = (not attempt.has_observations or attempt.sources_dirty) and attempt.state not in _IN_FLIGHT
            if needs_refresh:
                if attempt.refreshes_used >= snapshot.refresh_budget:
                    decisions.append(_wait(attempt, "refresh budget exhausted"))
                else:
                    decisions.append(helper._refresh(snapshot, attempt, "fifo; refresh in parallel with lease"))
            if attempt.state not in _IN_FLIGHT and (
                attempt.lease_id is None or _lease_expired(attempt, snapshot)
            ):
                decisions.append(
                    _admit_with_slo(snapshot, attempt, task, "fifo; grant short lease", self._ids, self._lease_ttl_s)
                )
                continue
            if (
                attempt.state == AttemptState.READY
                and attempt.has_observations
                and not needs_refresh
            ):
                if slots <= 0:
                    decisions.append(_wait_slot(snapshot, attempt))
                    continue
                slots -= 1
                decisions.append(_dispatch(attempt, "fifo; observation and lease both ready"))
                continue
            if not needs_refresh:
                decisions.append(_wait(attempt, f"in progress ({attempt.state.value})"))
        return decisions


class EvdfScheduler:
    """虚拟截止时间优先。等待越久，虚拟截止越早。不是研究文档里的 J0。"""

    def __init__(self, *, lease_ttl_s: float = 30.0, weight: float = 1.0, ids: IdFactory | None = None) -> None:
        self._lease_ttl_s = lease_ttl_s
        self._weight = weight
        self._ids = ids or IdFactory(None)

    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        """按虚拟截止时间派发。只在没有观测时观察一次。

        输入：快照。虚拟截止时间是物理截止减去 ``weight * 已等待秒数``。
        输出：决策列表。
        副作用：无。
        失败：时间为非正或需要刷新但没有配方时抛出 ``ValueError``。
        """
        HappyPathScheduler._require_now(snapshot)
        tasks = {item.task_id: item for item in snapshot.tasks}

        def sort_key(attempt: AttemptView) -> tuple:
            task = tasks.get(attempt.task_id)
            arrival = task.arrival_mono if task is not None else 0.0
            deadline = task.deadline_mono if task is not None else 0.0
            waited = max(0.0, snapshot.now_mono - arrival)
            virtual = deadline - self._weight * waited
            return (virtual, arrival, attempt.attempt_id)

        decisions: list[Decision] = []
        timed: set[str] = set()
        slots = snapshot.capacity.free_slots
        helper = HappyPathScheduler(lease_ttl_s=self._lease_ttl_s, ids=self._ids)
        for attempt in sorted(snapshot.attempts, key=sort_key):
            task = tasks.get(attempt.task_id)
            if task is None or task.status in _TERMINAL or not attempt.eligible:
                continue
            if helper._append_timeout(decisions, timed, task, attempt, snapshot):
                continue
            if attempt.state == AttemptState.COMMITTED:
                decisions.append(_finish(attempt, "attempt committed; complete task"))
                continue
            if attempt.refresh_inflight:
                decisions.append(_wait(attempt, "refresh in flight"))
                continue
            if not attempt.has_observations and attempt.state not in _IN_FLIGHT:
                decisions.append(helper._refresh(snapshot, attempt, "evdf observe once"))
                continue
            if attempt.state not in _IN_FLIGHT and (
                attempt.lease_id is None or _lease_expired(attempt, snapshot)
            ):
                decisions.append(
                    _admit_with_slo(snapshot, attempt, task, "evdf grant admission", self._ids, self._lease_ttl_s)
                )
                continue
            if attempt.state == AttemptState.READY:
                if slots <= 0:
                    decisions.append(_wait_slot(snapshot, attempt))
                    continue
                slots -= 1
                decisions.append(_dispatch(attempt, "evdf virtual deadline; dispatch to backend"))
                continue
            decisions.append(_wait(attempt, f"in progress ({attempt.state.value})"))
        return decisions


class BudgetedEdScheduler:
    """按最早截止排序，并用版本、间隔和预算决定要不要刷新。

    ``serial`` 在刷新当拍不发租约。``near`` 可以同一拍发租约。不是把现有类改名。
    """

    def __init__(
        self,
        *,
        admission: str,
        lease_ttl_s: float = 30.0,
        ids: IdFactory | None = None,
        weight: float = 0.0,
        fair_every_n: int = 0,
        scoring: bool = False,
    ) -> None:
        if admission not in {"serial", "near"}:
            raise ValueError("admission must be serial or near")
        self._admission = admission
        self._label = "j1" if scoring else ("b3" if admission == "serial" else "j0")
        self._lease_ttl_s = lease_ttl_s
        self._ids = ids or IdFactory(None)
        self._weight = weight
        self._fair_every_n = fair_every_n
        self._scoring = scoring

    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        """对每个未终态尝试按固定顺序给出超时、取消、刷新、租约或派发。

        输入：快照。排序键是截止时间、到达时间、attempt id。
        输出：决策列表。
        副作用：无。
        失败：时间为非正，或需要刷新但没有配方时抛出 ``ValueError``。
        """
        HappyPathScheduler._require_now(snapshot)
        tasks = {item.task_id: item for item in snapshot.tasks}
        weight = snapshot.fair_weight if snapshot.fair_weight else self._weight
        ordered = sorted(
            snapshot.attempts,
            key=lambda attempt: _virtual_key(snapshot, tasks.get(attempt.task_id), attempt, weight),
        )
        fair_every = snapshot.fair_every_n if snapshot.fair_every_n else self._fair_every_n
        fair_id = _fair_target(snapshot, ordered, tasks, fair_every)
        decisions: list[Decision] = []
        timed: set[str] = set()
        slots = snapshot.capacity.free_slots
        helper = HappyPathScheduler(lease_ttl_s=self._lease_ttl_s, ids=self._ids)
        for attempt in ordered:
            task = tasks.get(attempt.task_id)
            if task is None or task.status in _TERMINAL or not attempt.eligible:
                continue
            if helper._append_timeout(decisions, timed, task, attempt, snapshot):
                continue
            if attempt.state == AttemptState.COMMITTED:
                decisions.append(_finish(attempt, "attempt committed; complete task"))
                continue
            if (
                attempt.state == AttemptState.INFLIGHT
                and attempt.premise_holds is False
                and snapshot.capacity.supports_cancel
            ):
                decisions.append(
                    Decision(
                        kind="cancel",
                        task_id=attempt.task_id,
                        attempt_id=attempt.attempt_id,
                        reason="dirty and premise failed; revoke eligibility",
                    )
                )
                continue
            if (
                attempt.state == AttemptState.INFLIGHT
                and attempt.sources_dirty
                and attempt.premise_holds is None
            ):
                decisions.append(_wait(attempt, "premise unknown"))
                continue
            if _unreachable(snapshot, task) and attempt.state not in _IN_FLIGHT:
                decisions.append(
                    Decision(
                        kind="reject",
                        task_id=attempt.task_id,
                        attempt_id=attempt.attempt_id,
                        reason="infeasible before deadline",
                    )
                )
                continue
            refresh = None
            if self._scoring and attempt.state not in _IN_FLIGHT:
                choice = _best_utility(snapshot, attempt, task)
                if choice == "wait":
                    decisions.append(_wait(attempt, "j1 wait"))
                    continue
                if choice == "refresh":
                    refresh = _budgeted_refresh(snapshot, attempt, helper, self._label)
                    if refresh is None:
                        refresh = helper._refresh(snapshot, attempt, f"{self._label} score refresh")
            elif attempt.premise_holds is not True:
                refresh = _budgeted_refresh(snapshot, attempt, helper, self._label)
            if (
                refresh is not None
                and refresh.kind == "refresh"
                and snapshot.capacity.free_slots <= 0
                and snapshot.estimated_service_s > 0
                and snapshot.observe_cost_s >= snapshot.estimated_service_s
            ):
                refresh = _wait(attempt, "expensive observe waits for a free slot")
            if refresh is not None and refresh.kind == "wait":
                decisions.append(refresh)
                continue
            issued_refresh = refresh is not None and refresh.kind == "refresh"
            if issued_refresh:
                decisions.append(refresh)
            blocked = issued_refresh or attempt.refresh_inflight
            if blocked and self._admission == "serial":
                if not issued_refresh:
                    decisions.append(_wait(attempt, "refresh in flight"))
                continue
            if blocked and self._admission == "near":
                if attempt.state not in _IN_FLIGHT and (
                    attempt.lease_id is None or _lease_expired(attempt, snapshot)
                ):
                    decisions.append(
                        _admit_with_slo(
                            snapshot,
                            attempt,
                            task,
                            f"{self._label} lease while refresh runs",
                            self._ids,
                            self._lease_ttl_s,
                        )
                    )
                elif not issued_refresh:
                    decisions.append(_wait(attempt, "refresh in flight"))
                continue
            if attempt.state not in _IN_FLIGHT and (
                attempt.lease_id is None or _lease_expired(attempt, snapshot)
            ):
                decisions.append(
                    _admit_with_slo(
                        snapshot,
                        attempt,
                        task,
                        f"{self._label} grant admission",
                        self._ids,
                        self._lease_ttl_s,
                    )
                )
                continue
            if (
                attempt.state == AttemptState.READY
                and attempt.has_observations
                and not attempt.refresh_inflight
                and not _lease_expired(attempt, snapshot)
                and not _version_gap(snapshot, attempt)
            ):
                if slots <= 0:
                    decisions.append(_wait_slot(snapshot, attempt))
                    continue
                if fair_id is not None and attempt.attempt_id != fair_id:
                    decisions.append(_wait(attempt, "fair slot held"))
                    continue
                slots -= 1
                if fair_id is not None and attempt.attempt_id == fair_id:
                    reason = "fair slot; oldest executable"
                elif self._scoring:
                    reason = "j1 dispatch"
                elif self._admission == "serial":
                    reason = "b3 dispatch after refresh"
                else:
                    reason = "j0 observation and lease both ready"
                decisions.append(_dispatch(attempt, reason))
                continue
            if self._scoring and attempt.state not in _IN_FLIGHT and _version_gap(snapshot, attempt):
                decisions.append(_wait(attempt, "j1 version gap"))
                continue
            if attempt.state in _IN_FLIGHT or attempt.premise_holds is True:
                decisions.append(_wait(attempt, f"in progress ({attempt.state.value})"))
        return decisions


class StrongComboScheduler(BudgetedEdScheduler):
    """B3：事件触发刷新，刷新当拍不发租约。"""

    def __init__(
        self,
        *,
        lease_ttl_s: float = 30.0,
        ids: IdFactory | None = None,
        weight: float = 0.0,
        fair_every_n: int = 0,
    ) -> None:
        super().__init__(
            admission="serial",
            lease_ttl_s=lease_ttl_s,
            ids=ids,
            weight=weight,
            fair_every_n=fair_every_n,
        )


class NearComboScheduler(BudgetedEdScheduler):
    """J0：事件触发刷新，同一拍可以发租约。风险打分关闭。不是 EVDF。"""

    def __init__(
        self,
        *,
        lease_ttl_s: float = 30.0,
        ids: IdFactory | None = None,
        weight: float = 0.0,
        fair_every_n: int = 0,
    ) -> None:
        super().__init__(
            admission="near",
            lease_ttl_s=lease_ttl_s,
            ids=ids,
            weight=weight,
            fair_every_n=fair_every_n,
            scoring=False,
        )


class JointScheduler(BudgetedEdScheduler):
    """J1：临近准入加上 U(i,a)。关闭打分时退回 J0，不另起一套准入。"""

    def __init__(
        self,
        *,
        lease_ttl_s: float = 30.0,
        ids: IdFactory | None = None,
        weight: float = 0.0,
        fair_every_n: int = 0,
        scoring: bool = True,
    ) -> None:
        super().__init__(
            admission="near",
            lease_ttl_s=lease_ttl_s,
            ids=ids,
            weight=weight,
            fair_every_n=fair_every_n,
            scoring=scoring,
        )


class EventRefreshScheduler:
    """独立刷新族：只在缺少观测或通知版本变化时刷新。没有临近准入，也不打分。"""

    def __init__(self, *, lease_ttl_s: float = 30.0, ids: IdFactory | None = None) -> None:
        self._lease_ttl_s = lease_ttl_s
        self._ids = ids or IdFactory(None)

    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        """按到达顺序派发。版本差才刷新，刷新当拍不发租约。

        输入：快照。
        输出：决策列表。
        副作用：无。
        失败：时间为非正或需要刷新但没有配方时抛出 ``ValueError``。
        """
        helper = HappyPathScheduler(lease_ttl_s=self._lease_ttl_s, ids=self._ids)
        HappyPathScheduler._require_now(snapshot)
        tasks = {item.task_id: item for item in snapshot.tasks}
        ordered = sorted(
            snapshot.attempts,
            key=lambda attempt: (
                tasks[attempt.task_id].arrival_mono if attempt.task_id in tasks else 0.0,
                attempt.attempt_id,
            ),
        )
        decisions: list[Decision] = []
        timed: set[str] = set()
        slots = snapshot.capacity.free_slots
        for attempt in ordered:
            task = tasks.get(attempt.task_id)
            if task is None or task.status in _TERMINAL or not attempt.eligible:
                continue
            if helper._append_timeout(decisions, timed, task, attempt, snapshot):
                continue
            if attempt.state == AttemptState.COMMITTED:
                decisions.append(_finish(attempt, "attempt committed; complete task"))
                continue
            if attempt.state in _IN_FLIGHT:
                decisions.append(_wait(attempt, f"in progress ({attempt.state.value})"))
                continue
            if attempt.refresh_inflight:
                decisions.append(_wait(attempt, "refresh in flight"))
                continue
            refresh = _budgeted_refresh(snapshot, attempt, helper, "event")
            if refresh is not None and refresh.kind == "refresh":
                refresh = Decision(
                    kind="refresh",
                    task_id=refresh.task_id,
                    attempt_id=refresh.attempt_id,
                    reason="event refresh",
                    refresh_recipe=refresh.refresh_recipe,
                )
            if refresh is not None and refresh.kind == "wait":
                decisions.append(refresh)
                continue
            if refresh is not None:
                decisions.append(refresh)
                continue
            if attempt.lease_id is None or _lease_expired(attempt, snapshot):
                decisions.append(helper._admit(snapshot, attempt, "event grant admission"))
                continue
            if (
                attempt.state == AttemptState.READY
                and attempt.has_observations
                and not _version_gap(snapshot, attempt)
            ):
                if slots <= 0:
                    decisions.append(_wait_slot(snapshot, attempt))
                    continue
                slots -= 1
                decisions.append(_dispatch(attempt, "event dispatch"))
                continue
            decisions.append(_wait(attempt, f"in progress ({attempt.state.value})"))
        return decisions


def _virtual_key(
    snapshot: SchedulerSnapshot,
    task: TaskView | None,
    attempt: AttemptView,
    weight: float,
) -> tuple:
    arrival = task.arrival_mono if task is not None else 0.0
    deadline = task.deadline_mono if task is not None else 0.0
    waited = max(0.0, snapshot.now_mono - arrival)
    return (deadline - weight * waited, arrival, attempt.attempt_id)


def _fair_target(
    snapshot: SchedulerSnapshot,
    ordered: list[AttemptView],
    tasks: dict[str, TaskView],
    fair_every: int,
) -> str | None:
    if fair_every <= 0 or snapshot.dispatches_since_fair_slot + 1 < fair_every:
        return None
    best: AttemptView | None = None
    best_key: tuple | None = None
    for attempt in ordered:
        task = tasks.get(attempt.task_id)
        if task is None or not _dispatchable(snapshot, attempt):
            continue
        waited = max(0.0, snapshot.now_mono - task.arrival_mono)
        loose_first = 0 if task.task_class == "loose" else 1
        key = (-waited, loose_first, attempt.attempt_id)
        if best_key is None or key < best_key:
            best = attempt
            best_key = key
    if best is None:
        return None
    return best.attempt_id


def _dispatchable(snapshot: SchedulerSnapshot, attempt: AttemptView) -> bool:
    return (
        attempt.state == AttemptState.READY
        and attempt.has_observations
        and not attempt.refresh_inflight
        and not _lease_expired(attempt, snapshot)
        and not _version_gap(snapshot, attempt)
        and attempt.eligible
    )


def _best_utility(snapshot: SchedulerSnapshot, attempt: AttemptView, task: TaskView) -> str:
    scores = {
        action: _utility(snapshot, attempt, task, action) for action in ("execute", "refresh", "wait")
    }
    best = max(scores, key=lambda action: (scores[action], action == "execute", action == "refresh"))
    if scores[best] <= 0:
        return "wait"
    return best


def _utility(
    snapshot: SchedulerSnapshot,
    attempt: AttemptView,
    task: TaskView,
    action: str,
) -> float:
    service = snapshot.estimated_service_s
    observe = snapshot.observe_cost_s
    slack = task.deadline_mono - snapshot.now_mono
    if action == "execute":
        need = service
        model_cost = service
        observe_cost = 0.0
    elif action == "refresh":
        need = service + observe
        model_cost = 0.0
        observe_cost = observe
    else:
        need = service + max(snapshot.min_refresh_interval_s, 0.0)
        model_cost = 0.0
        observe_cost = 0.0
    if action == "wait":
        return 0.0
    if service <= 0:
        finish = 1.0
    else:
        finish = 1.0 if slack >= need else 0.0
    available = _availability(snapshot, attempt, action)
    if action == "execute" and not attempt.has_observations:
        available = 0.0
    return finish * available / (model_cost + snapshot.kappa * observe_cost + snapshot.epsilon)


def _availability(snapshot: SchedulerSnapshot, attempt: AttemptView, action: str) -> float:
    if action == "refresh":
        if attempt.refreshes_used >= snapshot.refresh_budget:
            return 0.0
        gap = _version_gap(snapshot, attempt)
        useful = gap or not attempt.has_observations or attempt.premise_holds is False
        return 1.0 if useful else 0.0
    gap = _version_gap(snapshot, attempt)
    if action == "wait" and (gap or not attempt.has_observations):
        return 0.0
    if attempt.premise_holds is True and not gap:
        return 1.0
    if attempt.premise_holds is False and action == "execute":
        return 0.0
    if attempt.last_refresh_mono is None:
        age = 0.0
    else:
        age = max(0.0, snapshot.now_mono - attempt.last_refresh_mono)
    if gap:
        age = max(age, 1.0)
    lam = snapshot.risk_lambda
    if snapshot.change_count > 0:
        lam = max(lam, snapshot.change_count / max(age, snapshot.epsilon))
    return math.exp(-lam * max(age, 0.0))


def _budgeted_refresh(
    snapshot: SchedulerSnapshot,
    attempt: AttemptView,
    helper: HappyPathScheduler,
    label: str,
) -> Decision | None:
    if attempt.state in _IN_FLIGHT:
        return None
    missing = not attempt.has_observations
    gap = _version_gap(snapshot, attempt)
    if not missing and not gap and not attempt.sources_dirty:
        return None
    if attempt.refreshes_used >= snapshot.refresh_budget:
        return _wait(attempt, "refresh budget exhausted")
    if _interval_blocked(snapshot, attempt):
        return _wait(attempt, "refresh interval")
    if missing or gap or not _same_version(snapshot, attempt):
        return helper._refresh(snapshot, attempt, f"{label} event refresh")
    return _wait(attempt, "same version")


def _version_gap(snapshot: SchedulerSnapshot, attempt: AttemptView) -> bool:
    dirty = set(snapshot.dirty_sources)
    for source in attempt.depends_on:
        if source in snapshot.notified_versions:
            if attempt.bound_versions.get(source) != snapshot.notified_versions[source]:
                return True
        elif source in dirty:
            return True
    return False


def _same_version(snapshot: SchedulerSnapshot, attempt: AttemptView) -> bool:
    if not attempt.depends_on:
        return False
    for source in attempt.depends_on:
        notified = snapshot.notified_versions.get(source)
        bound = attempt.bound_versions.get(source)
        if notified is None or bound is None or bound != notified:
            return False
    return True


def _interval_blocked(snapshot: SchedulerSnapshot, attempt: AttemptView) -> bool:
    if attempt.last_refresh_mono is None:
        return False
    return snapshot.now_mono - attempt.last_refresh_mono < snapshot.min_refresh_interval_s


def _unreachable(snapshot: SchedulerSnapshot, task: TaskView) -> bool:
    if snapshot.estimated_service_s <= 0:
        return False
    return task.deadline_mono < snapshot.now_mono + snapshot.estimated_service_s


def _slo_ttl(task_class: str, default_ttl: float, snapshot: SchedulerSnapshot) -> tuple[float, str]:
    if task_class == "strict":
        return snapshot.lease_strict_s, "strict"
    if task_class == "loose":
        return snapshot.lease_loose_s, "loose"
    return default_ttl, task_class or "default"


def _admit_with_slo(
    snapshot: SchedulerSnapshot,
    attempt: AttemptView,
    task: TaskView,
    reason: str,
    ids: IdFactory,
    default_ttl: float,
) -> Decision:
    ttl, quota = _slo_ttl(task.task_class, default_ttl, snapshot)
    lease = AdmissionLease(
        lease_id=ids("lease"),
        attempt_id=attempt.attempt_id,
        earliest_start_mono=snapshot.now_mono,
        expires_at_mono=snapshot.now_mono + ttl,
        quota_class=quota,
    )
    return Decision(
        kind="admit",
        task_id=attempt.task_id,
        attempt_id=attempt.attempt_id,
        reason=reason,
        lease=lease,
    )


def _ordered_once(
    snapshot: SchedulerSnapshot,
    *,
    order: str,
    helper: HappyPathScheduler,
    refresh_reason: str,
    admit_reason: str,
    dispatch_reason: str,
) -> list[Decision]:
    HappyPathScheduler._require_now(snapshot)
    tasks = {item.task_id: item for item in snapshot.tasks}

    def sort_key(attempt: AttemptView) -> tuple:
        task = tasks.get(attempt.task_id)
        arrival = task.arrival_mono if task is not None else 0.0
        deadline = task.deadline_mono if task is not None else 0.0
        if order == "deadline":
            return (deadline, arrival, attempt.attempt_id)
        return (arrival, attempt.attempt_id)

    decisions: list[Decision] = []
    timed: set[str] = set()
    slots = snapshot.capacity.free_slots
    for attempt in sorted(snapshot.attempts, key=sort_key):
        task = tasks.get(attempt.task_id)
        if task is None or task.status in _TERMINAL or not attempt.eligible:
            continue
        if helper._append_timeout(decisions, timed, task, attempt, snapshot):
            continue
        if attempt.state == AttemptState.COMMITTED:
            decisions.append(_finish(attempt, "attempt committed; complete task"))
            continue
        if attempt.refresh_inflight:
            decisions.append(_wait(attempt, "refresh in flight"))
            continue
        if not attempt.has_observations and attempt.state not in _IN_FLIGHT:
            decisions.append(helper._refresh(snapshot, attempt, refresh_reason))
            continue
        if attempt.state not in _IN_FLIGHT and (
            attempt.lease_id is None or _lease_expired(attempt, snapshot)
        ):
            decisions.append(helper._admit(snapshot, attempt, admit_reason))
            continue
        if attempt.state == AttemptState.READY:
            if slots <= 0:
                decisions.append(_wait_slot(snapshot, attempt))
                continue
            slots -= 1
            decisions.append(_dispatch(attempt, dispatch_reason))
            continue
        decisions.append(_wait(attempt, f"in progress ({attempt.state.value})"))
    return decisions


def _lease_expired(attempt: AttemptView, snapshot: SchedulerSnapshot) -> bool:
    return (
        attempt.lease_expires_at_mono is not None
        and attempt.lease_expires_at_mono < snapshot.now_mono
    )


def _finish(attempt: AttemptView, reason: str) -> Decision:
    return Decision(
        kind="finish",
        task_id=attempt.task_id,
        attempt_id=attempt.attempt_id,
        reason=reason,
    )


def _dispatch(attempt: AttemptView, reason: str) -> Decision:
    return Decision(
        kind="dispatch",
        task_id=attempt.task_id,
        attempt_id=attempt.attempt_id,
        reason=reason,
    )


def _wait(attempt: AttemptView, reason: str) -> Decision:
    return Decision(
        kind="wait",
        task_id=attempt.task_id,
        attempt_id=attempt.attempt_id,
        reason=reason,
    )


def _wait_slot(snapshot: SchedulerSnapshot, attempt: AttemptView) -> Decision:
    holders = ",".join(snapshot.capacity.inflight_task_ids) or "unknown"
    return _wait(
        attempt,
        f"no free inference slots; waiting task={attempt.task_id}; inflight={holders}",
    )
