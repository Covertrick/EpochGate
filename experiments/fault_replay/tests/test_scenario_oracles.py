"""Executable oracle checks for S13–S18. Expectations are literals, not the runner's labels."""

from __future__ import annotations

from pathlib import Path

from frozen_proposals.hashing import sha256_obj
from frozen_proposals.oracles import Observation, physical_effect_state, semantics_oracle
from frozen_proposals.predicate import PREDICATE_VERSION, legal_payload
from fault_replay.contract import SCENARIO_ROLE, WEEK3
from fault_replay.execute import run_cell
from fault_replay.proposals import load_records
from fault_replay.scoring import classify_claim_digest
from fault_replay.yaml_io import load_path

MANIFESTS = WEEK3 / "scenario_manifest"
REQUIRED = (
    "initial_world",
    "observed_world",
    "mutation",
    "notice_schedule",
    "effect_key",
    "semantic_oracle",
    "successor_requirement",
    "expected_evidence_class",
    "oracle_versions",
    "predicate_version",
    "world_state_id",
)


def _records():
    return {row["proposal_id"]: row for row in load_records()}


def test_manifests_record_world_and_oracle_versions():
    for scenario_id in ("S13", "S14", "S15", "S16", "S17", "S18"):
        body = load_path(MANIFESTS / f"{scenario_id}.yaml")
        for key in REQUIRED:
            assert key in body
        assert body["predicate_version"] == PREDICATE_VERSION
        assert body["semantic_oracle"]["version"] == "1.0.0"
        assert sha256_obj(body["initial_world"]) == body["world_state_id"]["initial"]
        assert sha256_obj(body["observed_world"]) == body["world_state_id"]["observed"]
        assert set(body["expected_matrix"]) == {"Direct", "Admission", "Claim", "Outbox"}


def test_physical_confirmation_does_not_collapse_into_semantic_invalidity():
    body = load_path(MANIFESTS / "S14.yaml")
    claim = legal_payload(body["initial_world"])
    observed = body["observed_world"]
    observation = Observation(
        run_id="golden-s14",
        proposal_id=body["fixture_proposal_id"],
        proposal_sha256="unused",
        predicate_version=PREDICATE_VERSION,
        scoring_world=observed,
        physical_bytes=claim,
    )
    assert semantics_oracle(observation) == "INVALID"
    assert physical_effect_state(observation) == "NOT_LEGAL"
    physical, recovery = classify_claim_digest(
        claim,
        __import__("frozen_proposals.hashing", fromlist=["sha256_bytes"]).sha256_bytes(claim),
        recovery_attempted=False,
        restored_bytes=False,
    )
    assert physical == "CONFIRMED_EFFECT"
    assert recovery == "NOT_APPLICABLE"
    assert physical != physical_effect_state(observation)


def test_s06_and_s09_stay_out_of_main_comparison():
    records = _records()
    assert records["p08"]["mechanism_role"] == "NON_DISCRIMINATIVE"
    assert records["p08"]["scenario_id"] == "S06"
    assert SCENARIO_ROLE["S06"] == "NON_DISCRIMINATIVE"
    assert records["p12"]["mechanism_role"] == "MECHANISM_SPECIFIC_DIAGNOSTIC"
    assert records["p12"]["scenario_id"] == "S09"


def _run(tmp_path: Path, proposal_id: str, path: str):
    return run_cell(_records()[proposal_id], path, run_root=tmp_path)


def test_s13_direct_confirmed_and_claim_unknown(tmp_path: Path):
    direct = _run(tmp_path, "p19", "Direct")
    claim = _run(tmp_path, "p19", "Claim")
    assert direct["physical_effect_state"] == "CONFIRMED_EFFECT"
    assert direct["semantic_validity"] == "INVALID"
    assert direct["recovery_state"] == "NOT_APPLICABLE"
    assert claim["recovery_evidence_state"] == "UNKNOWN"
    assert claim["physical_effect_state"] == "ABSENT"
    assert claim["semantic_validity"] == "UNKNOWN"
    assert claim["semantic_validity"] != "INVALID"


def test_s14_restored_bytes_are_not_confirmed_effect(tmp_path: Path):
    row = _run(tmp_path, "p20", "Claim")
    assert row["recovery_evidence_state"] == "PUBLISH_SEALED"
    assert row["recovery_state"] == "RESTORED_BYTES"
    assert row["physical_effect_state"] == "PRESENT_UNVERIFIED"
    assert row["semantic_validity"] == "INVALID"
    assert row["published_old_bytes"] is True


def test_s15_confirm_target_is_confirmed_effect(tmp_path: Path):
    row = _run(tmp_path, "p21", "Outbox")
    assert row["recovery_evidence_state"] == "CONFIRM_TARGET"
    assert row["physical_effect_state"] == "CONFIRMED_EFFECT"
    assert row["recovery_state"] == "NOT_APPLICABLE"
    assert row["semantic_validity"] == "INVALID"


def test_s16_lost_receipt_restores_sealed_bytes(tmp_path: Path):
    row = _run(tmp_path, "p22", "Outbox")
    assert row["checkpoint"] == "lost_receipt"
    assert row["recovery_evidence_state"] == "PUBLISH_SEALED"
    assert row["recovery_state"] == "RESTORED_BYTES"
    assert row["semantic_validity"] == "INVALID"
    assert row["recovery_state"] != "CONFIRMED_EFFECT"


def test_s17_freeze_accepts_successor_and_direct_publishes_old(tmp_path: Path):
    claim = _run(tmp_path, "p24", "Claim")
    outbox = _run(tmp_path, "p24", "Outbox")
    direct = _run(tmp_path, "p24", "Direct")
    admission = _run(tmp_path, "p24", "Admission")
    for row in (claim, outbox):
        assert row["published_old_bytes"] is False
        assert row["successor_required"] is True
        assert row["successor_status"] == "COMPLETED_VALID"
        assert row["semantic_validity"] == "VALID"
        assert row["scenario_behavior"] == "notified_freeze"
    for row in (direct, admission):
        assert row["published_old_bytes"] is True
        assert row["successor_status"] == "FAILED"
        assert row["semantic_validity"] == "INVALID"


def test_s18_correct_stop_is_not_successor_failure(tmp_path: Path):
    claim = _run(tmp_path, "p26", "Claim")
    direct = _run(tmp_path, "p26", "Direct")
    assert claim["successor_required"] is True
    assert claim["successor_status"] == "CORRECT_STOP"
    assert claim["successor_status"] != "FAILED"
    assert claim["week1_successor_status"] == "NOT_APPLICABLE"
    assert claim["published_old_bytes"] is False
    assert claim["physical_effect_state"] == "ABSENT"
    assert direct["published_old_bytes"] is True
    assert direct["successor_status"] == "FAILED"
    assert direct["adjudication"] != "UNRESOLVED" or direct["authority_decision"] == "STALE_ACCEPTED"


def test_s09_not_applicable_is_not_failure(tmp_path: Path):
    row = _run(tmp_path, "p12", "Direct")
    assert row["runner_status"] == "NOT_APPLICABLE"
    assert row["adjudication"] == "UNRESOLVED"
    assert row["adjudication"] != "CONFIRMED_FAILURE"
    assert row["in_main_comparison"] is False
