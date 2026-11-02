"""离线确定性 A/B/C：与 acceptance_report 共用 acceptance_predicates。"""

from __future__ import annotations

import pytest

from epochgate.validity_run import (
    acceptance_predicates,
    predicates_pass,
    run_deterministic_scenario,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("task_kind", ["static", "invalidate", "recoverable"])
async def test_deterministic_scenario_predicates(task_kind: str) -> None:
    row = await run_deterministic_scenario(
        task_kind=task_kind,
        seed={"static": 801, "invalidate": 811, "recoverable": 821}[task_kind],
        code_version="pytest",
    )
    preds = acceptance_predicates(task_kind, row)
    assert preds == row["predicates"]
    assert all(preds.values()), preds
    assert predicates_pass(task_kind, row)
    assert row["success"] is True
    assert row["evidence_class"] == "deterministic"


@pytest.mark.asyncio
async def test_static_stock_and_single_commit() -> None:
    row = await run_deterministic_scenario(task_kind="static", seed=803, code_version="pytest")
    assert row["action_commits"] == 1
    assert row["final_stock"] == 2
    assert row["side_effect_once"] is True


@pytest.mark.asyncio
async def test_invalidate_late_tool_does_not_commit() -> None:
    row = await run_deterministic_scenario(task_kind="invalidate", seed=813, code_version="pytest")
    assert row["old_attempt_committed"] is False
    assert row["action_commits"] == 0
    assert row["final_stock"] == 0
    assert row["stock_change_fired"] is True


@pytest.mark.asyncio
async def test_recoverable_old_rejected_new_commits_once() -> None:
    row = await run_deterministic_scenario(task_kind="recoverable", seed=823, code_version="pytest")
    assert row["old_attempt_committed"] is False
    assert row["action_commits"] == 1
    assert row["side_effect_once"] is True
    assert row["final_stock"] == 0
