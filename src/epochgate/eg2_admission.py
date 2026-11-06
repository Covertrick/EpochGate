"""真实历史事件的六项准入。不编造缺失的 SHA 或见证。"""

from __future__ import annotations

import re
from typing import Any

SHA40 = re.compile(r"^[0-9a-f]{40}$")
CHECKS = (
    "time_valid",
    "source_real",
    "applicable",
    "task_not_pre_solved",
    "solvable_after_change",
    "independently_scorable",
)


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    reasons: dict[str, str] = {}
    time_ok = (
        case.get("base_is_ancestor") is True
        and _ordered(case.get("base_time"), case.get("change_time"))
        and bool(case.get("issue_time"))
    )
    if not time_ok:
        reasons["time_valid"] = "missing ancestry or time order"
    source_ok = bool(case.get("change_url")) and SHA40.match(str(case.get("change_sha", ""))) is not None
    if not source_ok:
        reasons["source_real"] = "change url or full sha missing"
    apply_status = case.get("apply_status")
    applicable = apply_status == "applied"
    if not applicable:
        reasons["applicable"] = f"apply_status={apply_status or 'missing'}"
    if case.get("contains_target_fix") is None or case.get("leaks_target_fix") is None:
        not_solved = False
        reasons["task_not_pre_solved"] = "not established"
    elif case.get("contains_target_fix") or case.get("leaks_target_fix"):
        not_solved = False
        reasons["task_not_pre_solved"] = "change contains or leaks the target fix"
    else:
        not_solved = True
    solvable = case.get("recovery_witness_present") is True
    if not solvable:
        reasons["solvable_after_change"] = "no recovery witness"
    scorable = case.get("post_state_test_present") is True
    if not scorable:
        reasons["independently_scorable"] = "no post-state test or rule"
    flags = {
        "time_valid": time_ok,
        "source_real": source_ok,
        "applicable": applicable,
        "task_not_pre_solved": not_solved,
        "solvable_after_change": solvable,
        "independently_scorable": scorable,
    }
    return {
        "case_id": case.get("case_id"),
        "checks": flags,
        "admitted": all(flags.values()),
        "reasons": reasons,
    }


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    reports = [evaluate_case(case) for case in cases]
    admitted = [item["case_id"] for item in reports if item["admitted"]]
    return {
        "candidates": len(reports),
        "admitted": len(admitted),
        "admitted_ids": admitted,
        "data_gate": "pass" if len(admitted) >= 5 else "stop",
        "reports": reports,
    }


def _ordered(earlier: Any, later: Any) -> bool:
    return isinstance(earlier, str) and isinstance(later, str) and earlier < later
