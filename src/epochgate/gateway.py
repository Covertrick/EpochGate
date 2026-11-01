"""动作网关。整个包里只有本模块可以调用 ``environment.commit``。"""

from __future__ import annotations

import time
from typing import Mapping

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
    """重新绑定观测，核对 epoch 和租约，再经环境提交。"""

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
        usage: Mapping | None = None,
    ) -> CommitResult:
        """检查资格并提交。

        输入：候选动作，以及仍须对应该尝试的准入租约。可选 usage 里的 tokens 原样写入追踪。
        输出：接受或带原因的拒绝。
        副作用：可能调用 ``environment.check`` 和 ``environment.commit``；
        更新尝试状态；追加追踪事件。
        失败：环境内部错误向上抛。策略拒绝返回 ``accepted=False``，不抛异常。
        列出的观测 id 缺一则拒绝，不再改用该任务的最新观测。
        """
        now = time.monotonic()
        attempt = self._registry.get_attempt(candidate.attempt_id)
        extra = dict(usage or {})

        if lease.attempt_id != candidate.attempt_id:
            return self._reject(candidate, ["lease attempt_id mismatch"], extra=extra)
        if lease.expires_at_mono < now:
            return self._reject(candidate, ["lease expired"], extra=extra)
        if not attempt.eligible:
            return self._reject(candidate, ["attempt not eligible"], extra=extra)

        plan_epoch, obs_revision = self._registry.current_epoch(candidate.task_id)
        if candidate.plan_epoch != plan_epoch:
            return self._reject(
                candidate,
                [f"stale plan_epoch candidate={candidate.plan_epoch} current={plan_epoch}"],
                extra=extra,
            )
        if candidate.obs_revision != obs_revision:
            return self._reject(
                candidate,
                [f"stale obs_revision candidate={candidate.obs_revision} current={obs_revision}"],
                extra=extra,
            )

        bound_obs, missing = self._resolve_observations(candidate)
        if missing:
            return self._reject(
                candidate,
                [f"observation bindings missing: {','.join(missing)}"],
                observations=bound_obs,
                extra=extra,
            )

        self._registry.update_attempt_state(candidate.attempt_id, AttemptState.VALIDATING)
        check = await self._env.check(candidate)
        if not check.ok:
            return self._reject(candidate, check.reasons, observations=bound_obs, extra=extra)

        conditions = self._build_conditions(bound_obs)
        result = await self._env.commit(candidate, conditions)
        event_usage = self._usage(bound_obs, extra)

        if result.effect_stale:
            event_usage["effect_stale"] = True
            event_usage["reject_class"] = "stale_commit"
        event_usage["action_parameters"] = dict(candidate.parameters)
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
                usage=event_usage,
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
                usage={**event_usage, "reject_class": _reject_class(result.reasons)},
            )
        return result

    def _resolve_observations(self, candidate: CandidateAction) -> tuple[list[Observation], list[str]]:
        if not candidate.observation_ids:
            return self._observations.latest_for_task(candidate.task_id), []
        found: list[Observation] = []
        missing: list[str] = []
        for obs_id in candidate.observation_ids:
            try:
                found.append(self._observations.get(obs_id))
            except KeyError:
                missing.append(obs_id)
        return found, missing

    @staticmethod
    def _build_conditions(observations: list[Observation]) -> dict:
        conditions: dict = {}
        if len(observations) == 1:
            conditions["source_version"] = observations[0].source_version
            conditions["source_id"] = observations[0].source_id
        elif observations:
            conditions["source_versions"] = {obs.source_id: obs.source_version for obs in observations}
        return conditions

    def _usage(self, observations: list[Observation], extra: Mapping | None) -> dict:
        usage: dict = {}
        now = time.monotonic()
        if observations:
            usage["sample_time_mono"] = min(obs.sample_time_mono for obs in observations)
            usage["observation_ages"] = [
                {
                    "source_id": obs.source_id,
                    "age_s": max(0.0, now - obs.sample_time_mono),
                }
                for obs in observations
            ]
        if extra:
            provider = extra.get("provider_usage")
            if isinstance(provider, Mapping):
                usage["provider_usage"] = dict(provider)
            if extra.get("usage_source") == "backend":
                usage["usage_source"] = "backend"
                if "tokens" in extra:
                    usage["tokens"] = extra["tokens"]
        return usage

    def _reject(
        self,
        candidate: CandidateAction,
        reasons: list[str],
        *,
        observations: list[Observation] | None = None,
        extra: Mapping | None = None,
    ) -> CommitResult:
        self._events.emit(
            run_id=self._run_id,
            event_type="action_rejected",
            identity={
                "task_id": candidate.task_id,
                "attempt_id": candidate.attempt_id,
                "action_id": candidate.action_id,
            },
            reason="; ".join(reasons),
            usage={
                **self._usage(observations or [], extra),
                "reject_class": _reject_class(reasons),
                "action_parameters": dict(candidate.parameters),
            },
        )
        try:
            self._registry.update_attempt_state(candidate.attempt_id, AttemptState.REJECTED)
        except KeyError:
            pass
        return CommitResult(accepted=False, reasons=reasons)


def _reject_class(reasons: list[str]) -> str:
    blob = " ".join(reasons).lower()
    if (
        "stale plan_epoch" in blob
        or "stale obs_revision" in blob
        or "observation bindings missing" in blob
        or blob.startswith("stale")
    ):
        return "candidate_stale"
    if "version conflict" in blob or "insufficient" in blob:
        return "provider_reject"
    if "lease" in blob or "not eligible" in blob:
        return "gateway_intercept"
    return "gateway_intercept"
