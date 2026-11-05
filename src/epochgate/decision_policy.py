"""在途撤销的三态决策协议。

该模块只做纯决策，不读取环境、不提交动作，也不修改 Runtime 状态。
Gateway 仍然负责候选动作的最终谓词、版本和租约检查。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RuntimeAction(StrEnum):
    CONTINUE = "continue"
    ABORT_AND_REPLAN = "abort-and-replan"
    ACCEPT_IF_VALID = "accept-if-valid"
    DEFER = "continue/defer"


@dataclass(frozen=True)
class DecisionInput:
    predicate_valid: bool
    candidate_formed: bool
    remaining_service_estimate: float
    restart_service_estimate: float
    remaining_service_error: float = 0.0
    restart_service_error: float = 0.0
    safety_buffer: float = 0.0

    def __post_init__(self) -> None:
        values = (
            self.remaining_service_estimate,
            self.restart_service_estimate,
            self.remaining_service_error,
            self.restart_service_error,
            self.safety_buffer,
        )
        if any(value < 0 for value in values):
            raise ValueError("service estimates, errors, and safety_buffer must be non-negative")


@dataclass(frozen=True)
class DecisionOutput:
    action: RuntimeAction
    delta: float
    margin: float
    reason: str


def decide(value: DecisionInput) -> DecisionOutput:
    """Return the action required by the frozen protocol.

    Priority is deliberate: an invalid predicate always triggers replan;
    a formed candidate is handed to Gateway; only an unformed candidate is
    compared by the cost difference and its uncertainty margin.
    """

    delta = value.restart_service_estimate - value.remaining_service_estimate
    margin = value.restart_service_error + value.remaining_service_error + value.safety_buffer
    if not value.predicate_valid:
        return DecisionOutput(RuntimeAction.ABORT_AND_REPLAN, delta, margin, "predicate-invalid")
    if value.candidate_formed:
        return DecisionOutput(RuntimeAction.ACCEPT_IF_VALID, delta, margin, "candidate-formed")
    if delta < -margin:
        return DecisionOutput(RuntimeAction.ABORT_AND_REPLAN, delta, margin, "restart-cheaper-outside-margin")
    if delta > margin:
        return DecisionOutput(RuntimeAction.CONTINUE, delta, margin, "continue-cheaper-outside-margin")
    return DecisionOutput(RuntimeAction.DEFER, delta, margin, "inside-uncertainty-margin")


__all__ = ["DecisionInput", "DecisionOutput", "RuntimeAction", "decide"]
