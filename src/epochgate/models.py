"""Core domain entities and scheduler-visible views.

All wall-clock-independent durations and ordering use ``time.monotonic()``
values carried as ``float`` fields. Display clocks are out of scope for v0.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"


class AttemptState(str, Enum):
    """Resource lifecycle for a request attempt (independent of result eligibility)."""

    QUEUED = "queued"
    OBSERVING = "observing"
    READY = "ready"
    INFLIGHT = "inflight"
    RESULT_READY = "result_ready"
    VALIDATING = "validating"
    COMMITTED = "committed"
    REJECTED = "rejected"
    REPLAN = "replan"


class Task(BaseModel):
    task_id: str
    arrival_mono: float
    deadline_mono: float
    goal: str
    task_class: str = "default"
    status: TaskStatus = TaskStatus.PENDING
    plan_epoch: int = 0
    obs_revision: int = 0


class RequestAttempt(BaseModel):
    attempt_id: str
    task_id: str
    plan_epoch: int
    obs_revision: int
    state: AttemptState = AttemptState.QUEUED
    eligible: bool = True
    created_mono: float
    reason: str | None = None


class Observation(BaseModel):
    observation_id: str
    source_id: str
    source_version: str | None = None
    sample_time_mono: float
    received_time_mono: float
    payload: Mapping[str, Any] = Field(default_factory=dict)
    payload_hash: str | None = None
    visibility: Literal["public", "tenant"] = "public"


class Dependency(BaseModel):
    observation_id: str
    predicate_id: str
    scope: str = "exact"
    completeness: Literal["required", "unknown", "coarse"] = "required"


class RefreshRecipe(BaseModel):
    read_tool: str
    args: Mapping[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    timeout_s: float = 5.0
    cost_estimate: float = 1.0
    merge_key: str | None = None


class AdmissionLease(BaseModel):
    lease_id: str
    attempt_id: str
    earliest_start_mono: float
    expires_at_mono: float
    quota_class: str = "default"


class CandidateAction(BaseModel):
    action_id: str
    task_id: str
    attempt_id: str
    plan_epoch: int
    obs_revision: int
    action_type: str
    parameters: Mapping[str, Any] = Field(default_factory=dict)
    predicates: list[Dependency] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)


class TraceEvent(BaseModel):
    run_id: str
    seq: int
    mono_time: float
    event_type: str
    identity: Mapping[str, str] = Field(default_factory=dict)
    reason: str | None = None
    usage: Mapping[str, Any] = Field(default_factory=dict)


class ObservationBatch(BaseModel):
    observations: list[Observation] = Field(default_factory=list)


class ValidationResult(BaseModel):
    ok: bool
    reasons: list[str] = Field(default_factory=list)


class CommitResult(BaseModel):
    accepted: bool
    commit_id: str | None = None
    reasons: list[str] = Field(default_factory=list)


class BackendCapabilities(BaseModel):
    supports_cancel: bool = False
    supports_streaming: bool = False
    max_inflight: int = 1


class InferenceRequest(BaseModel):
    attempt_id: str
    task_id: str
    plan_epoch: int
    obs_revision: int
    goal: str
    observation_ids: list[str] = Field(default_factory=list)
    context: Mapping[str, Any] = Field(default_factory=dict)


class BackendHandle(BaseModel):
    handle_id: str
    attempt_id: str


class BackendEvent(BaseModel):
    handle_id: str
    kind: Literal["accepted", "started", "completed", "failed"]
    mono_time: float
    candidate: CandidateAction | None = None
    detail: str | None = None


class CancelReceipt(BaseModel):
    handle_id: str
    acknowledged: bool
    evidence: str


# --- Scheduler-visible views (trimmed; no evaluator / no live refs) ---


class TaskView(BaseModel):
    task_id: str
    status: TaskStatus
    goal: str
    plan_epoch: int
    obs_revision: int
    deadline_mono: float


class AttemptView(BaseModel):
    attempt_id: str
    task_id: str
    plan_epoch: int
    obs_revision: int
    state: AttemptState
    eligible: bool
    has_observations: bool
    lease_id: str | None = None
    lease_expires_at_mono: float | None = None


class ObservationView(BaseModel):
    observation_id: str
    source_id: str
    source_version: str | None
    sample_time_mono: float
    task_id: str | None = None


class BackendCapacityView(BaseModel):
    free_slots: int
    supports_cancel: bool = False


class SchedulerSnapshot(BaseModel):
    """Cropped view assembled by Runtime. Scheduler must not hold live stores."""

    now_mono: float
    run_id: str
    tasks: list[TaskView]
    attempts: list[AttemptView]
    observations: list[ObservationView]
    capacity: BackendCapacityView
    default_refresh: RefreshRecipe | None = None


DecisionKind = Literal["refresh", "admit", "dispatch", "wait", "finish"]


class Decision(BaseModel):
    """Intent only. Runtime executes; scheduler must not apply side effects."""

    kind: DecisionKind
    task_id: str
    attempt_id: str | None = None
    reason: str
    refresh_recipe: RefreshRecipe | None = None
    lease: AdmissionLease | None = None
