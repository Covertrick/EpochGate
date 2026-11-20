"""Process-kill proofs for the Week 4 recovery extensions."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from fault_replay.contract import ARCHIVED_JOINT, ARCHIVED_SHA256, MEMORY_SENTINEL
from fault_replay.kill_runner import frozen_proposal, pid_alive
from crash_recovery.crash_evidence import classify_durable, rsr_gate
from crash_recovery.crash_runner import archived_sha256, killer_name, run_receiver_case, run_relay_case, run_repeat_case


def test_archived_joint_untouched():
    assert archived_sha256() == ARCHIVED_SHA256
    assert ARCHIVED_JOINT.is_file()


def test_classify_receiver_states():
    unknown = classify_durable(sealed_digest="", target_digest="", receipt_present=False, receipt_digest="")
    confirm = classify_durable(
        sealed_digest="aa",
        target_digest="aa",
        receipt_present=False,
        receipt_digest="",
    )
    conflict = classify_durable(
        sealed_digest="aa",
        target_digest="bb",
        receipt_present=False,
        receipt_digest="",
    )
    assert unknown == ("UNKNOWN", "WITHHOLD_UNSEALED")
    assert confirm == ("CONFIRM_TARGET", "CONFIRM_EXISTING_TARGET")
    assert conflict == ("CONFLICT", "HOLD_CONFLICT")


def test_rsr_gate_three_results():
    assert rsr_gate("VALID") == ("PUBLISH_SEALED", "PUBLISH_SEALED_BYTES", True)
    assert rsr_gate("INVALID") == ("SEMANTICALLY_SUPERSEDED", "WITHHOLD_SUPERSEDED", False)
    assert rsr_gate("UNKNOWN") == ("UNKNOWN", "WITHHOLD_UNKNOWN_SEMANTICS", False)


def test_receiver_process_is_terminated(tmp_path: Path):
    proposal = frozen_proposal("p10")
    row = run_receiver_case(
        tmp_path / "recv",
        proposal=proposal,
        checkpoint="after_receiver_accept",
        run_id="test-recv-kill",
    )
    assert row["killer"] == killer_name() == "TerminateProcess"
    assert row["exit_code"] == 1
    assert pid_alive(row["killed_pid"]) is False
    assert row["killed_pid"] != row["reconciler_pid"]
    assert row["memory_sentinel_seen"] is False
    blob = b"".join(path.read_bytes() for path in (tmp_path / "recv").rglob("*") if path.is_file())
    assert MEMORY_SENTINEL not in blob
    assert row["evidence_state"] == "CONFIRM_TARGET"
    assert row["receipt_present"] is False
    assert row["logical_effect_count"] == 1
    assert row["process_kill"] is True
    assert row["power_loss_claim"] is False
    assert not (tmp_path / "recv" / "receipt.json").exists()
    assert (tmp_path / "recv" / "effect.bin").is_file()


def test_second_recovery_does_not_double_publish(tmp_path: Path):
    proposal = frozen_proposal("p10")
    locked: dict[str, Path] = {}

    def between(run_dir: Path) -> None:
        target = run_dir / "effect.bin"
        os.chmod(target, stat.S_IREAD)
        locked["path"] = target

    try:
        row = run_repeat_case(
            tmp_path / "repeat",
            proposal=proposal,
            checkpoint="after_sealed_before_replace",
            run_id="test-repeat-once",
            between=between,
        )
    finally:
        target = locked.get("path")
        if target is not None and target.exists():
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
    assert row["exit_code"] == 1
    assert pid_alive(row["killed_pid"]) is False
    assert row["evidence_state"] == "PUBLISH_SEALED"
    assert row["second_evidence_state"] == "CONFIRM_TARGET"
    assert row["double_published"] is False
    assert row["effect_rewritten"] is False
    assert row["ledger_count"] == 1
    assert row["logical_effect_count"] == 1
    assert "PUBLISH_SEALED_BYTES" in row["recovery_action"]
    assert row["recovery_action"].endswith("CONFIRM_EXISTING_TARGET")
    assert row["process_kill"] is True
    assert row["power_loss_claim"] is False
    if sys.platform == "win32":
        assert row["killer"] == "TerminateProcess"


def test_relay_replay_keeps_one_logical_effect(tmp_path: Path):
    proposal = frozen_proposal("p10")
    row = run_relay_case(tmp_path / "relay", proposal=proposal, run_id="test-relay-once")
    assert pid_alive(row["killed_pid"]) is False
    assert row["exit_code"] == 1
    assert row["logical_effect_count"] == 1
    assert row["evidence_state"] == "PUBLISH_SEALED"
    assert row["recovery_action"] == "RELAY_THEN_REPLAY"
    assert row["process_kill"] is True
    assert row["power_loss_claim"] is False
