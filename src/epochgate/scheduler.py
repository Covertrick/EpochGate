"""Joint scheduler: decisions from a cropped snapshot only.

This module must not import gateway / environment / backend concrete types.
It must not hold Registry, ObservationStore, or evaluator references.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from epochgate.models import (
    AdmissionLease,
    AttemptState,
    Decision,
    SchedulerSnapshot,
    TaskStatus,
)

_TERMINAL = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.TIMED_OUT,
    TaskStatus.REJECTED,
}


@runtime_checkable
class Scheduler(Protocol):
    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        """Produce intents from a cropped snapshot.

        Inputs: SchedulerSnapshot (no live store refs, no evaluator).
        Outputs: list of Decision (may be empty).
        Side effects: none.
        Failures: invalid snapshot fields → ``ValueError``.
        """
        ...


class HappyPathScheduler:
    """Fixed-rule policy for the minimal demo (no scoring formula).

    Per eligible attempt on a non-terminal task:
    - committed → finish
    - no observations → refresh
    - observations present, no lease → admit
    - ready + valid lease + free slot → dispatch
    - otherwise → wait
    """

    def __init__(self, *, lease_ttl_s: float = 30.0) -> None:
        self._lease_ttl_s = lease_ttl_s

    def decide(self, snapshot: SchedulerSnapshot) -> list[Decision]:
        """See Protocol docstring. Pure function of snapshot content."""
        if snapshot.now_mono <= 0:
            raise ValueError("snapshot.now_mono must be positive monotonic time")

        tasks = {t.task_id: t for t in snapshot.tasks}
        decisions: list[Decision] = []

        for attempt in snapshot.attempts:
            task = tasks.get(attempt.task_id)
            if task is None or task.status in _TERMINAL or not attempt.eligible:
                continue

            if attempt.state == AttemptState.COMMITTED:
                decisions.append(
                    Decision(
                        kind="finish",
                        task_id=attempt.task_id,
                        attempt_id=attempt.attempt_id,
                        reason="attempt committed; complete task",
                    )
                )
                continue

            if not attempt.has_observations:
                if snapshot.default_refresh is None:
                    raise ValueError("default_refresh required when observations are missing")
                decisions.append(
                    Decision(
                        kind="refresh",
                        task_id=attempt.task_id,
                        attempt_id=attempt.attempt_id,
                        reason="missing observations; refresh before admit",
                        refresh_recipe=snapshot.default_refresh,
                    )
                )
                continue

            if attempt.lease_id is None:
                lease = AdmissionLease(
                    lease_id=f"lease-{uuid.uuid4().hex[:12]}",
                    attempt_id=attempt.attempt_id,
                    earliest_start_mono=snapshot.now_mono,
                    expires_at_mono=snapshot.now_mono + self._lease_ttl_s,
                )
                decisions.append(
                    Decision(
                        kind="admit",
                        task_id=attempt.task_id,
                        attempt_id=attempt.attempt_id,
                        reason="observations ready; grant short admission lease",
                        lease=lease,
                    )
                )
                continue

            if attempt.state == AttemptState.READY:
                if (
                    attempt.lease_expires_at_mono is not None
                    and attempt.lease_expires_at_mono < snapshot.now_mono
                ):
                    decisions.append(
                        Decision(
                            kind="wait",
                            task_id=attempt.task_id,
                            attempt_id=attempt.attempt_id,
                            reason="lease expired; wait for re-admit",
                        )
                    )
                    continue
                if snapshot.capacity.free_slots <= 0:
                    decisions.append(
                        Decision(
                            kind="wait",
                            task_id=attempt.task_id,
                            attempt_id=attempt.attempt_id,
                            reason="no free inference slots",
                        )
                    )
                    continue
                decisions.append(
                    Decision(
                        kind="dispatch",
                        task_id=attempt.task_id,
                        attempt_id=attempt.attempt_id,
                        reason="lease valid; dispatch to backend",
                    )
                )
                continue

            decisions.append(
                Decision(
                    kind="wait",
                    task_id=attempt.task_id,
                    attempt_id=attempt.attempt_id,
                    reason=f"in progress ({attempt.state.value})",
                )
            )

        return decisions
