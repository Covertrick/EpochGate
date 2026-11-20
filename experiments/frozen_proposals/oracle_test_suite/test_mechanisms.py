from frozen_proposals.fairness import check_fairness
from frozen_proposals.freeze_artifacts import freeze
from frozen_proposals.frozen_proposal_runner import load_jsonl, run_selected


def _by_path(root, cohort, proposal_id):
    rows = [
        row
        for row in load_jsonl(root / "run_manifest.jsonl")
        if row.get("cohort") == cohort and row.get("proposal_id") == proposal_id and row.get("runner_status") != "ABANDONED"
    ]
    return {row["path"]: row for row in rows}


def test_same_proposal_bytes_and_s01_s03_s04_s08_s09(tmp_path):
    freeze(tmp_path)
    ids = ["p01", "p04", "p06", "p08", "p10", "p12", "p13", "p15"]
    result = run_selected(tmp_path, ids, "CHECK")
    assert result["pass"] is True
    rows = [row for row in load_jsonl(tmp_path / "run_manifest.jsonl") if row.get("cohort") == "CHECK"]
    assert check_fairness(rows)["pass"] is True

    s01 = _by_path(tmp_path, "CHECK", "p01")
    assert s01["Direct"]["authority_decision"] == "STALE_ACCEPTED"
    assert s01["Direct"]["physical_effect_state"] == "NOT_LEGAL"
    assert s01["Admission"]["authority_decision"] == "NO_STALE_ACCEPTANCE_RECORDED"
    assert s01["Admission"]["physical_effect_state"] == "ABSENT"
    assert s01["Admission"]["stop_correctness"] == "CORRECT_STOP"
    assert s01["Admission"]["week1_successor_status"] == "NOT_APPLICABLE"
    assert s01["Claim"]["physical_effect_state"] == "ABSENT"
    assert s01["Outbox"]["physical_effect_state"] == "ABSENT"
    assert len({row["payload_sha256"] for row in s01.values()}) == 1

    s03 = _by_path(tmp_path, "CHECK", "p04")
    for path in s03.values():
        assert path["authority_decision"] == "NO_STALE_ACCEPTANCE_RECORDED"
        assert path["semantic_validity"] == "INVALID"
        assert path["physical_effect_state"] == "NOT_LEGAL"
        assert path["outdated_scenario_label"] == "NOTICE_MISSING_OR_DELAYED"

    s04 = _by_path(tmp_path, "CHECK", "p06")
    assert s04["Direct"]["terminal_outcome"] == "MATCH"
    assert s04["Direct"]["authority_decision"] == "STALE_ACCEPTED"
    assert s04["Direct"]["physical_effect_state"] == "CONFIRMED_EFFECT"
    assert s04["Direct"]["semantic_validity"] == "VALID"
    assert s04["Direct"]["adjudication"] == "CONFIRMED_FAILURE"
    assert s04["Admission"]["authority_decision"] == "NO_STALE_ACCEPTANCE_RECORDED"
    assert s04["Admission"]["terminal_outcome"] == "MISMATCH"

    s06 = _by_path(tmp_path, "CHECK", "p08")
    for path in s06.values():
        assert path["authority_decision"] == "STALE_ACCEPTED"
        assert path["physical_effect_state"] == "NOT_LEGAL"

    s08 = _by_path(tmp_path, "CHECK", "p10")
    assert s08["Direct"]["runner_status"] == "CRASHED"
    assert s08["Admission"]["runner_status"] == "CRASHED"
    assert s08["Claim"]["runner_status"] == "COMPLETED"
    assert s08["Outbox"]["runner_status"] == "COMPLETED"
    for path in s08.values():
        assert path["physical_effect_state"] == "CONFIRMED_EFFECT"
        assert path["joint_observed"] is True
    assert s08["Direct"]["recovery_state"] == "NOT_APPLICABLE"
    assert s08["Claim"]["recovery_state"] == "RESTORED_BYTES"
    assert s08["Outbox"]["recovery_state"] == "RESTORED_BYTES"

    s09 = _by_path(tmp_path, "CHECK", "p12")
    assert s09["Direct"]["runner_status"] == "NOT_APPLICABLE"
    assert s09["Admission"]["runner_status"] == "NOT_APPLICABLE"
    assert s09["Direct"]["adjudication"] == "UNRESOLVED"
    assert s09["Claim"]["physical_effect_state"] == "CONFIRMED_EFFECT"
    assert s09["Claim"]["recovery_state"] == "RESTORED_BYTES"
    assert s09["Outbox"]["physical_effect_state"] == "CONFIRMED_EFFECT"
    assert s09["Direct"]["payload_sha256"] == s09["Outbox"]["payload_sha256"]

    s10 = _by_path(tmp_path, "CHECK", "p13")
    for path in s10.values():
        assert path["evidence_state"] == "CONFLICT"
        assert path["semantic_validity"] == "VALID"
        assert path["duplicate_effect"] is False
        assert path["physical_effect_state"] == "CONFIRMED_EFFECT"

    s12 = _by_path(tmp_path, "CHECK", "p15")
    assert s12["Admission"]["successor_status"] == "FAILED"
    assert s12["Admission"]["successor_status"] != "CORRECT_STOP"
    assert s12["Admission"]["adjudication"] == "NO_CONFIRMED_FAILURE"
    assert s12["Direct"]["authority_decision"] == "STALE_ACCEPTED"
