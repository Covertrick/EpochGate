"""Action Gateway: the only module allowed to call ``environment.commit``."""

from __future__ import annotations

import time

from epochgate.environment import EnvironmentAdapter
from epochgate.events import EventLog
from epochgate.models import (
    AdmissionLease,
    AttemptState,
    CandidateAction,
    CommitResult,
    Observation,
)
from epochgate.observation import ObservationStore
from epochgate.registry import Registry


class ActionGateway:
    """Re-bind observations, check epoch/lease, then commit via the environment."""

    def __init__(
        self,
        environment: EnvironmentAdapter,
        registry: Registry,
        observations: ObservationStore,
        events: EventLog,
        *,
        run_id: str = "default",
    ) -> None:
        self._env = environment
        self._registry = registry
        self._observations = observations
        self._events = events
        self._run_id = run_id

    async def submit(
        self,
        candidate: CandidateAction,
        lease: AdmissionLease,
    ) -> CommitResult:
        """Validate eligibility and commit.

        Inputs: CandidateAction + AdmissionLease that must still match the attempt.
        Outputs: CommitResult (accepted / rejected with reasons).
        Side effects: may call ``environment.check`` and ``environment.commit``;
        updates attempt state; appends trace events.
        Failures: environment internal errors propagate; policy rejects return
        ``CommitResult(accepted=False)`` without raising.
        """
        now = time.monotonic()
        attempt = self._registry.get_attempt(candidate.attempt_id)

        if lease.attempt_id != candidate.attempt_id:
            return self._reject(candidate, ["lease attempt_id mismatch"])
        if lease.expires_at_mono < now:
            return self._reject(candidate, ["lease expired"])
        if not attempt.eligible:
            return self._reject(candidate, ["attempt not eligible"])

        plan_epoch, obs_revision = self._registry.current_epoch(candidate.task_id)
        if candidate.plan_epoch != plan_epoch:
            return self._reject(
                candidate,
                [f"stale plan_epoch candidate={candidate.plan_epoch} current={plan_epoch}"],
            )
        if candidate.obs_revision != obs_revision:
            return self._reject(
                candidate,
                [f"stale obs_revision candidate={candidate.obs_revision} current={obs_revision}"],
            )

        # Re-bind real observations; never trust model-only source claims.
        bound_obs = self._resolve_observations(candidate)
        if candidate.observation_ids and not bound_obs:
            return self._reject(candidate, ["observation bindings missing"])

        self._registry.update_attempt_state(candidate.attempt_id, AttemptState.VALIDATING)
        check = await self._env.check(candidate)
        if not check.ok:
            return self._reject(candidate, check.reasons)

        conditions = self._build_conditions(bound_obs)
        result = await self._env.commit(candidate, conditions)

        if result.accepted:
            self._registry.update_attempt_state(candidate.attempt_id, AttemptState.COMMITTED)
            self._events.emit(
                run_id=self._run_id,
                event_type="action_committed",
                identity={
                    "task_id": candidate.task_id,
                    "attempt_id": candidate.attempt_id,
                    "action_id": candidate.action_id,
                },
                reason=result.commit_id,
            )
        else:
            self._registry.update_attempt_state(candidate.attempt_id, AttemptState.REJECTED)
            self._events.emit(
                run_id=self._run_id,
                event_type="action_rejected",
                identity={
                    "task_id": candidate.task_id,
                    "attempt_id": candidate.attempt_id,
                    "action_id": candidate.action_id,
                },
                reason="; ".join(result.reasons),
            )
        return result

    def _resolve_observations(self, candidate: CandidateAction) -> list[Observation]:
        found: list[Observation] = []
        for obs_id in candidate.observation_ids:
            try:
                found.append(self._observations.get(obs_id))
            except KeyError:
                continue
        if not found:
            found = self._observations.latest_for_task(candidate.task_id)
        return found

    @staticmethod
    def _build_conditions(observations: list[Observation]) -> dict:
        conditions: dict = {}
        if len(observations) == 1:
            conditions["source_version"] = observations[0].source_version
            conditions["source_id"] = observations[0].source_id
        elif observations:
            conditions["source_versions"] = {
                o.source_id: o.source_version for o in observations
            }
        return conditions

    def _reject(self, candidate: CandidateAction, reasons: list[str]) -> CommitResult:
        self._events.emit(
            run_id=self._run_id,
            event_type="action_rejected",
            identity={
                "task_id": candidate.task_id,
                "attempt_id": candidate.attempt_id,
                "action_id": candidate.action_id,
            },
            reason="; ".join(reasons),
        )
        try:
            self._registry.update_attempt_state(candidate.attempt_id, AttemptState.REJECTED)
        except KeyError:
            pass
        return CommitResult(accepted=False, reasons=reasons)
