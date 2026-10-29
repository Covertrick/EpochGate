"""冒烟清单的本地部分。不访问模型。"""

from __future__ import annotations

import pytest

from epochgate.faults import FaultPlan, FaultSpec
from epochgate.smoke import evaluate_cell, run_mechanism_checks


def test_positive_probability_without_faults_is_invalid() -> None:
    plan = FaultPlan(faults=[FaultSpec(kind="crash", probability=1, modes=["http_500"])], seed=1)
    plan.request_count = 4
    verdict = evaluate_cell(plan, 0)
    assert verdict["cell_valid"] is False
    assert "zero fault_injected" in verdict["reason"]


@pytest.mark.asyncio
async def test_mechanism_checklist_passes(tmp_path) -> None:
    report = await run_mechanism_checks(tmp_path, service_prior_s=0.0)
    failed = [item for item in report["checks"] if not item["passed"]]
    assert failed == []
    names = {item["name"] for item in report["checks"]}
    assert {
        "sqlite_wal_200",
        "close_rejects_write",
        "no_oversell",
        "idempotent_action",
        "default_commit_has_no_window",
        "window_opens_conflict",
        "lease_ttl",
        "prior_zero_admits",
        "unreachable_rejected",
        "asgi_env",
    } <= names
