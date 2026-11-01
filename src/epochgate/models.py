"""领域实体，以及调度器可见的裁剪视图。

与墙钟无关的时长和顺序都用 ``time.monotonic()`` 的 ``float``。
展示用的日历时间不在当前范围内。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """任务业务状态。成功只计一次。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"


class AttemptState(str, Enum):
    """一次请求尝试的资源生命周期，与结果资格相互独立。"""

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
    """一项业务任务。重算不新建任务分母。"""

    task_id: str
    arrival_mono: float
    deadline_mono: float
    goal: str
    task_class: str = "default"
    status: TaskStatus = TaskStatus.PENDING
    plan_epoch: int = 0
    obs_revision: int = 0


class RequestAttempt(BaseModel):
    """一次模型请求尝试。计划修订与观测更新分开编号。"""

    attempt_id: str
    task_id: str
    plan_epoch: int
    obs_revision: int
    state: AttemptState = AttemptState.QUEUED
    eligible: bool = True
    created_mono: float
    reason: str | None = None


class Observation(BaseModel):
    """一次只读采样。``source_version`` 允许为空。"""

    observation_id: str
    source_id: str
    source_version: str | None = None
    sample_time_mono: float
    received_time_mono: float
    payload: Mapping[str, Any] = Field(default_factory=dict)
    payload_hash: str | None = None
    visibility: Literal["public", "tenant"] = "public"


class Dependency(BaseModel):
    """动作对观测的依赖。模型不能自行声明条件已满足。"""

    observation_id: str
    predicate_id: str
    scope: str = "exact"
    completeness: Literal["required", "unknown", "coarse"] = "required"


class RefreshRecipe(BaseModel):
    """只读刷新配方。相同 merge_key 可以合并，写操作不能放进来。"""

    read_tool: str
    args: Mapping[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    timeout_s: float = 5.0
    cost_estimate: float = 1.0
    merge_key: str | None = None


class AdmissionLease(BaseModel):
    """短期准入资格。不独占后端硬件，过期后应重新评估。"""

    lease_id: str
    attempt_id: str
    earliest_start_mono: float
    expires_at_mono: float
    quota_class: str = "default"


class CandidateAction(BaseModel):
    """待提交动作。运行时重新绑定观测，不信任模型自报的来源。"""

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
    """只追加的追踪事件。修正用新事件表达，不改历史。"""

    run_id: str
    seq: int
    mono_time: float
    event_type: str
    identity: Mapping[str, str] = Field(default_factory=dict)
    reason: str | None = None
    usage: Mapping[str, Any] = Field(default_factory=dict)


class ObservationBatch(BaseModel):
    """一次 observe 的返回。"""

    observations: list[Observation] = Field(default_factory=list)


class ValidationResult(BaseModel):
    """提交前检查的结果。失败不抛异常。"""

    ok: bool
    reasons: list[str] = Field(default_factory=list)


class CommitResult(BaseModel):
    """条件提交的结果。拒绝必须带原因。"""

    accepted: bool
    commit_id: str | None = None
    reasons: list[str] = Field(default_factory=list)
    effect_stale: bool = False


class BackendCapabilities(BaseModel):
    """后端自报能力。取消确认不等于计算已经停止。"""

    supports_cancel: bool = False
    supports_streaming: bool = False
    resource_reclaim_observable: bool = False
    max_inflight: int = 1


class ToolSpec(BaseModel):
    """环境公布的工具。运行时只按 role 分发，不按名字分支。"""

    name: str
    role: Literal["read", "effect"]
    description: str = ""
    parameters: Mapping[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """模型返回的一次工具调用。"""

    call_id: str
    name: str
    arguments: Mapping[str, Any] = Field(default_factory=dict)


class InferenceRequest(BaseModel):
    """发给推理后端的请求。context 里带观测正文，不只是 id。"""

    attempt_id: str
    task_id: str
    plan_epoch: int
    obs_revision: int
    goal: str
    observation_ids: list[str] = Field(default_factory=list)
    context: Mapping[str, Any] = Field(default_factory=dict)
    tools: list[ToolSpec] = Field(default_factory=list)


class BackendHandle(BaseModel):
    """一次已受理推理的句柄。"""

    handle_id: str
    attempt_id: str


class BackendEvent(BaseModel):
    """推理生命周期事件。completed 上带候选动作或工具调用。"""

    handle_id: str
    kind: Literal["accepted", "started", "completed", "failed", "token_delta", "tool_call_delta"]
    mono_time: float
    candidate: CandidateAction | None = None
    tool_call: ToolCall | None = None
    detail: str | None = None
    usage: Mapping[str, Any] = Field(default_factory=dict)


class CancelReceipt(BaseModel):
    """取消回执。evidence 照后端原文记录，不能写成 GPU 已回收。

    ``grade`` 是 ACCEPTED、ACKNOWLEDGED、NOT_SUPPORTED 或 UNKNOWN。
    ``reclaimed`` 仅在后端明确停止了自己的计算时为真。
    """

    handle_id: str
    acknowledged: bool
    evidence: str
    grade: Literal["ACCEPTED", "ACKNOWLEDGED", "NOT_SUPPORTED", "UNKNOWN"] = "UNKNOWN"
    reclaimed: bool = False


class ChangeEvent(BaseModel):
    """环境变化通知。dirty 只表示待核查，不等于决策已经失效。"""

    cursor: str
    source_id: str
    source_version: str | None = None
    mono_time: float
    kind: str = "updated"


class ChangeBatch(BaseModel):
    """``changes`` 的一页结果。``stream_open`` 为假表示通知断了，不能当成状态稳定。"""

    events: list[ChangeEvent] = Field(default_factory=list)
    next_cursor: str = "0"
    stream_open: bool = True


# --- 调度器可见视图（裁剪后；无 evaluator、无仓库活引用） ---


class TaskView(BaseModel):
    """调度器看见的任务。不含观测正文。"""

    task_id: str
    status: TaskStatus
    goal: str
    plan_epoch: int
    obs_revision: int
    arrival_mono: float
    deadline_mono: float
    task_class: str = "default"


class AttemptView(BaseModel):
    """调度器看见的尝试。sources_dirty 只是布尔，没有环境真值。"""

    attempt_id: str
    task_id: str
    plan_epoch: int
    obs_revision: int
    state: AttemptState
    eligible: bool
    has_observations: bool
    lease_id: str | None = None
    lease_expires_at_mono: float | None = None
    sources_dirty: bool = False
    refreshes_used: int = 0
    refresh_inflight: bool = False
    premise_holds: bool | None = None
    bound_versions: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    last_refresh_mono: float | None = None


class ObservationView(BaseModel):
    """调度器看见的观测元数据。不含 payload。"""

    observation_id: str
    source_id: str
    source_version: str | None
    sample_time_mono: float
    task_id: str | None = None


class BackendCapacityView(BaseModel):
    """当前可派发的槽位。inflight_task_ids 只用于解释谁占着槽。"""

    free_slots: int
    supports_cancel: bool = False
    inflight_task_ids: list[str] = Field(default_factory=list)


class SchedulerSnapshot(BaseModel):
    """Runtime 组装的裁剪快照。调度器不得持有仓库活引用。"""

    now_mono: float
    run_id: str
    tasks: list[TaskView]
    attempts: list[AttemptView]
    observations: list[ObservationView]
    capacity: BackendCapacityView
    default_refresh: RefreshRecipe | None = None
    dirty_sources: list[str] = Field(default_factory=list)
    refresh_budget: int = 8
    estimated_service_s: float = 0.0
    notified_versions: dict[str, str] = Field(default_factory=dict)
    min_refresh_interval_s: float = 0.0
    dispatches_since_fair_slot: int = 0
    fair_every_n: int = 0
    fair_weight: float = 0.0
    kappa: float = 1.0
    epsilon: float = 0.05
    observe_cost_s: float = 0.0
    risk_lambda: float = 0.0
    change_count: int = 0
    stream_open: bool = True
    lease_strict_s: float = 5.0
    lease_loose_s: float = 30.0


DecisionKind = Literal["refresh", "admit", "dispatch", "wait", "finish", "timeout", "cancel", "reject"]


class Decision(BaseModel):
    """调度意图。Runtime 执行；调度器不得产生副作用。"""

    kind: DecisionKind
    task_id: str
    attempt_id: str | None = None
    reason: str
    refresh_recipe: RefreshRecipe | None = None
    lease: AdmissionLease | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
