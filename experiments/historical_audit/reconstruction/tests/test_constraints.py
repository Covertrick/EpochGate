"""Constraint tests for the week-1 reconstruction checker."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import reconstruct as rec  # noqa: E402

WORKSPACE = Path(__file__).resolve().parents[4]


def _completed(**overrides: object) -> dict:
    raw = {
        "task_id": "task-a",
        "arm": "B0",
        "fault": "S1",
        "run_id": "task-a-B0-S1",
        "blocked": False,
        "task_terminal_match": True,
        "stale_credential_accepted": False,
        "wrong_physical": False,
        "duplicate_effect": False,
        "verifiable_effect": True,
        "physical_digest": "digest-legal",
        "legal_digest": "digest-legal",
        "interrupted_recovered": None,
        "successor_applicable": True,
        "successor_completed": False,
        "correct_stop": False,
        "receiver": {
            "accept_log": [{"accepted": True, "digest": "digest-legal"}],
            "physical_digest": "digest-legal",
            "accepted_count": 1,
        },
    }
    raw.update(overrides)
    return rec.normalize_raw(
        raw,
        cohort="m4-confirm",
        source_summary_ref="synthetic:summary",
        source_evidence_ref="synthetic:evidence",
        source_artifact_ref="synthetic:artifact",
        artifact_matches=True,
    )


def test_schema_rejects_unknown_failure_label() -> None:
    record = _completed()
    record["adjudication"] = "FAILURE"
    errors = rec.validate_outcome(record)
    assert any(error.startswith("enum:adjudication") for error in errors)


def test_missing_identifiers_are_not_invented(tmp_path: Path) -> None:
    root = tmp_path / "results" / "runtime" / "correctness" / "m4-confirm"
    root.mkdir(parents=True)
    (root / "summary.json").write_text(
        json.dumps({"n": 1, "blocked": 0, "rows": [{"arm": "B0", "blocked": False}]}),
        encoding="utf-8",
    )
    records, anomalies, _meta = rec.build_confirm(tmp_path)
    assert any(item["anomaly_type"] == "MISSING_IDENTIFIER" for item in anomalies)
    assert records[0]["proposal_id"] is None
    assert records[0]["proposal_id_origin"] == "ABSENT"
    assert records[0]["run_id"] is None
    assert records[0]["joint_observed"] is False
    assert records[0]["adjudication"] == "UNRESOLVED"


def test_unknown_blocked_terminal_is_not_failure() -> None:
    record = rec.normalize_raw(
        {
            "task_id": "task-a",
            "arm": "B0",
            "fault": "S2",
            "blocked": True,
            "error_type": "model_no_effect",
            "task_terminal_match": False,
        },
        cohort="m4-confirm",
        source_summary_ref="synthetic:blocked",
        source_evidence_ref=None,
        source_artifact_ref=None,
        artifact_matches=None,
    )
    assert record["terminal_outcome"] == "UNKNOWN"
    assert record["adjudication"] == "UNRESOLVED"
    assert record["adjudication"] != "CONFIRMED_FAILURE"
    assert record["joint_observed"] is False
    assert rec.validate_outcome(record) == []


def test_conflict_is_unresolved_and_not_merged() -> None:
    raw = {
        "task_id": "task-a",
        "arm": "B0",
        "fault": "S1",
        "run_id": "run-conflict",
        "blocked": False,
        "task_terminal_match": True,
        "stale_credential_accepted": False,
        "wrong_physical": True,
        "duplicate_effect": False,
        "verifiable_effect": True,
        "physical_digest": "digest-legal",
        "legal_digest": "digest-legal",
        "successor_applicable": True,
        "successor_completed": False,
        "correct_stop": False,
        "receiver": {"accept_log": [], "physical_digest": "digest-legal", "accepted_count": 1},
        "oracle": {"task_terminal_match": False},
    }
    record = rec.normalize_raw(
        raw,
        cohort="m4-confirm",
        source_summary_ref="synthetic:conflict",
        source_evidence_ref="synthetic:conflict",
        source_artifact_ref=None,
        artifact_matches=True,
    )
    assert record["adjudication"] == "UNRESOLVED"
    assert record["joint_observed"] is False
    assert record["unobserved_reason"].startswith("CONFLICT:")
    assert record["terminal_outcome"] == "UNKNOWN"


def test_restored_nonlegal_bytes_are_not_confirmed_effect() -> None:
    record = _completed(
        arm="B3",
        fault="S3",
        run_id="task-a-B3-S3",
        task_terminal_match=False,
        wrong_physical=True,
        physical_digest="old-bytes",
        legal_digest="legal-bytes",
        interrupted_recovered=True,
        receiver={
            "accept_log": [{"accepted": True}],
            "physical_digest": "old-bytes",
            "accepted_count": 1,
        },
    )
    assert record["recovery_state"] == "RESTORED_BYTES"
    assert record["physical_effect_state"] == "NOT_LEGAL"
    assert record["physical_effect_state"] != "CONFIRMED_EFFECT"
    assert "RESTORED" not in record["adjudication_basis"]
    assert rec.validate_outcome(record) == []


def test_correct_stop_is_not_successor_failure() -> None:
    record = rec.normalize_raw(
        {
            "authority": "revoke",
            "arm": "B2",
            "successor_applicable": False,
            "successor_completed": False,
            "correct_stop": True,
            "stale_credential_accepted": False,
        },
        cohort="m4-dedicated",
        source_summary_ref="synthetic:revoke",
        source_evidence_ref=None,
        source_artifact_ref=None,
        artifact_matches=None,
    )
    assert record["stop_correctness"] == "CORRECT_STOP"
    assert record["successor_status"] == "NOT_APPLICABLE"
    assert record["adjudication"] != "CONFIRMED_FAILURE"
    assert "FAILURE" not in record["successor_status"]
    assert rec.validate_outcome(record) == []


def test_outdated_label_is_not_stale_acceptance() -> None:
    record = rec.normalize_raw(
        {"task_id": "task-a", "arm": "B1", "fault": "S2", "blocked": True, "task_terminal_match": False},
        cohort="m4-confirm",
        source_summary_ref="synthetic:outdated",
        source_evidence_ref=None,
        source_artifact_ref=None,
        artifact_matches=None,
    )
    assert record["outdated_scenario_label"] == "NOTICE_MISSING_OR_DELAYED"
    assert record["authority_decision"] == "UNKNOWN"
    assert record["authority_decision"] != "STALE_ACCEPTED"
    fresh = _completed(fault="S2", stale_credential_accepted=False)
    assert fresh["outdated_scenario_label"] == "NOTICE_MISSING_OR_DELAYED"
    assert fresh["authority_decision"] == "NO_STALE_ACCEPTANCE_RECORDED"


def test_duplicate_records_collapse_without_a_second_sample() -> None:
    one = _completed(run_id="same-run")
    two = _completed(run_id="same-run")
    kept, anomalies = rec.collapse_duplicate_runs([one, two])
    assert len(kept) == 1
    assert anomalies[0]["anomaly_type"] == "DUPLICATE_RUN_ID"
    conflicted = _completed(run_id="same-run", task_terminal_match=False, wrong_physical=True, physical_digest="other", legal_digest="digest-legal")
    merged, conflict_anomalies = rec.collapse_duplicate_runs([one, conflicted])
    assert len(merged) == 1
    assert merged[0]["adjudication"] == "UNRESOLVED"
    assert merged[0]["joint_observed"] is False
    assert conflict_anomalies[0]["anomaly_type"] == "CONFLICT"


def test_marginal_only_input_does_not_manufacture_joints() -> None:
    payload = {"n": 72, "blocked": 20, "terminal_match": 16, "stale_accepted": 10, "rows": [{"arm": "B0", "n": 18}]}
    assert rec.is_marginal_only(payload) is True
    result = rec.reconstruct_marginal_only(payload)
    assert result["decision"] == "NO-GO FOR ARCHIVED EFE"
    assert result["joint_recoverable"] is False
    assert result["rows"] == []
    assert "16" not in json.dumps(result["rows"])


def test_archived_confirm_joint_is_partial_and_schema_valid() -> None:
    if not (WORKSPACE / "results/runtime/correctness/m4-confirm/summary.json").is_file():
        return
    records, anomalies, meta = rec.build_confirm(WORKSPACE)
    schema = rec.load_schema()
    for record in records:
        assert rec.validate_outcome(record, schema) == [], record
    decision = rec.decide(records, meta)
    assert meta["header_n"] == 72
    assert meta["header_blocked"] == 20
    assert decision["confirm_rows"] == 72
    assert 0 < decision["joint_observed"] < 72
    assert decision["decision"] == "CONDITIONAL GO"
    blocked = [row for row in records if row["unobserved_reason"] == "BLOCKED_NO_EFFECT_HISTORY"]
    assert len(blocked) == 20
    assert all(row["adjudication"] == "UNRESOLVED" for row in blocked)
    assert all(row["terminal_outcome"] == "UNKNOWN" for row in blocked)
    assert not any(row["physical_effect_state"] == "CONFIRMED_EFFECT" and row["adjudication_basis"].startswith("RESTORED") for row in records)
    assert any(item["anomaly_type"] == "MISSING_RUN_ID" for item in anomalies)
