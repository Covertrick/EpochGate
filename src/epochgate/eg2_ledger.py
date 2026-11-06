"""候选、授权和任务正确性分开计数。Oracle 不进入可部署策略。"""

from __future__ import annotations

from typing import Any

DEPLOYABLE = ("global_reset", "reread_retest", "static_dependency", "llm_dependency", "ignore_update")


def score_candidate(row: dict[str, Any]) -> dict[str, bool]:
    gateway_rejection = row.get("gateway_decision") == "reject"
    candidate_invalid = row.get("candidate_matches_current_requirements") is False
    final_failure = row.get("post_state_tests_passed") is False
    return {
        "candidate_invalid_attempt": candidate_invalid,
        "gateway_rejection": gateway_rejection,
        "final_task_failure": final_failure,
    }


def deployment_allowed(policy: str) -> bool:
    return policy in DEPLOYABLE


def oracle_record(hidden_dependency_used: bool) -> dict[str, Any]:
    return {
        "policy": "oracle_dependency",
        "deployable": False,
        "hidden_information": hidden_dependency_used,
        "comparable_as_fair_baseline": False,
    }
