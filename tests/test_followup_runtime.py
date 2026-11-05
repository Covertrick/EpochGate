import asyncio

from epochgate.followup_runtime import ScriptedGeneration, run_matrix, summarize


def test_closed_loop_triggers_each_action_and_rejects_stale_commits() -> None:
    rows = asyncio.run(run_matrix(ScriptedGeneration(), batches=1, tasks=2))
    summary = summarize(rows, engine="scripted")
    assert summary["attempts"] == 8
    assert set(summary["actions"]) == {"continue", "abort-and-replan", "accept-if-valid", "continue/defer"}
    assert summary["stale_accepted_commit"] == 0
    assert summary["old_attempt_accepted"] == 0
    assert summary["cancel_receipts_closed"] == summary["abort_attempts"]
    formed = next(row for row in rows if row["scenario"] == "candidate-formed")
    assert formed["gateway"]["accepted"] is True
    version = next(row for row in rows if row["scenario"] == "version-race")
    assert version["gateway"]["accepted"] is False
    assert any("stale plan_epoch" in reason for reason in version["gateway"]["reasons"])
    invalid = next(row for row in rows if row["scenario"] == "predicate-invalid")
    assert invalid["predicate_valid"] is False
    assert invalid["decision"] == "abort-and-replan"
