"""只读快照上的调度策略。本模块不得 import 网关、环境或后端实现。"""

from __future__ import annotations

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


def _unreachable(snapshot: SchedulerSnapshot, task: TaskView) -> bool:
    if snapshot.estimated_service_s <= 0:
        return False
    return task.deadline_mono < snapshot.now_mono + snapshot.estimated_service_s


def _slo_ttl(task_class: str, default_ttl: float) -> tuple[float, str]:
    if task_class == "strict":
        return 5.0, "strict"
    if task_class == "loose":
        return 30.0, "loose"
    return default_ttl, task_class or "default"


def _admit_with_slo(
    snapshot: SchedulerSnapshot,
    attempt: AttemptView,
    task: TaskView,
    reason: str,
    ids: IdFactory,
    default_ttl: float,
) -> Decision:
    ttl, quota = _slo_ttl(task.task_class, default_ttl)
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
