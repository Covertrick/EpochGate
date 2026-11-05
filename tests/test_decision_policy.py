from __future__ import annotations

import pytest

from epochgate.decision_policy import DecisionInput, RuntimeAction, decide


def test_invalid_predicate_has_priority_over_cost() -> None:
    result = decide(
        DecisionInput(
            predicate_valid=False,
            candidate_formed=False,
            remaining_service_estimate=1,
            restart_service_estimate=100,
        )
    )
    assert result.action is RuntimeAction.ABORT_AND_REPLAN
    assert result.reason == "predicate-invalid"


def test_formed_candidate_is_deferred_to_gateway() -> None:
    result = decide(
        DecisionInput(
            predicate_valid=True,
            candidate_formed=True,
            remaining_service_estimate=100,
            restart_service_estimate=1,
        )
    )
    assert result.action is RuntimeAction.ACCEPT_IF_VALID


@pytest.mark.parametrize(
    ("restart", "remaining", "expected"),
    [(1, 10, RuntimeAction.ABORT_AND_REPLAN), (10, 1, RuntimeAction.CONTINUE), (5, 5, RuntimeAction.DEFER)],
)
def test_cost_rule(restart: float, remaining: float, expected: RuntimeAction) -> None:
    result = decide(
        DecisionInput(
            predicate_valid=True,
            candidate_formed=False,
            remaining_service_estimate=remaining,
            restart_service_estimate=restart,
        )
    )
    assert result.action is expected


def test_error_margin_prevents_forced_switch() -> None:
    result = decide(
        DecisionInput(
            predicate_valid=True,
            candidate_formed=False,
            remaining_service_estimate=10,
            restart_service_estimate=8,
            remaining_service_error=1,
            restart_service_error=1,
        )
    )
    assert result.action is RuntimeAction.DEFER
    assert result.margin == 2


def test_negative_uncertainty_is_rejected() -> None:
    with pytest.raises(ValueError):
        DecisionInput(
            predicate_valid=True,
            candidate_formed=False,
            remaining_service_estimate=1,
            restart_service_estimate=1,
            safety_buffer=-0.1,
        )
