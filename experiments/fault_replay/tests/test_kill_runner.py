"""Process-kill checks. The 4 x 2 x 10 matrix is the study file, not this module's loop."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from frozen_proposals.db import connect
from fault_replay.contract import ARCHIVED_JOINT, ARCHIVED_SHA256, MEMORY_SENTINEL, WEEK2, WEEK3
from fault_replay.kill_runner import frozen_proposal, hard_kill, kill_once, launch_and_kill, pid_alive
from frozen_proposals.hashing import sha256_file

STUDY = WEEK3 / "crash_matrix.csv"


def test_archived_joint_untouched():
    import hashlib

    digest = hashlib.sha256(ARCHIVED_JOINT.read_bytes()).hexdigest()
    assert digest == ARCHIVED_SHA256


def test_frozen_proposal_bytes_match_registry():
    for proposal_id in ("p10", "p11"):
        proposal = frozen_proposal(proposal_id)
        assert sha256_file(WEEK2 / proposal["payload_path"]) == proposal["payload_sha256"]


def test_child_is_killed_and_memory_is_gone(tmp_path: Path):
    proposal = frozen_proposal("p10")
    row = kill_once(
        tmp_path / "killed",
        proposal=proposal,
        path="Claim",
        checkpoint="after_sealed_before_replace",
        run_id="test-kill",
    )
    assert row["exit_code"] == 1
    assert row["killed_pid"] != row["reconciler_pid"]
    assert pid_alive(row["killed_pid"]) is False
    assert row["memory_sentinel_seen"] is False
    blob = b"".join(path.read_bytes() for path in (tmp_path / "killed").rglob("*") if path.is_file())
    assert MEMORY_SENTINEL not in blob
    conn = connect(tmp_path / "killed" / "state.sqlite")
    try:
        ledger = conn.execute("SELECT claim_status, sealed_digest FROM kill_ledger WHERE id = 1").fetchone()
    finally:
        conn.close()
    assert ledger["claim_status"] == "SEALED"
    assert ledger["sealed_digest"] == proposal["payload_sha256"]


def test_recovery_state_is_reproducible(tmp_path: Path):
    proposal = frozen_proposal("p11")
    first = kill_once(
        tmp_path / "a",
        proposal=proposal,
        path="Outbox",
        checkpoint="after_replace_before_receipt",
        run_id="test-repro-a",
    )
    second = kill_once(
        tmp_path / "b",
        proposal=proposal,
        path="Outbox",
        checkpoint="after_replace_before_receipt",
        run_id="test-repro-b",
    )
    assert first["evidence_state"] == second["evidence_state"] == "CONFIRM_TARGET"
    assert first["post_kill_state"] == second["post_kill_state"] == "REPLACED_NO_RECEIPT"
    assert pid_alive(first["killed_pid"]) is False
    assert pid_alive(second["killed_pid"]) is False


def test_conflict_is_a_real_reconcile_state(tmp_path: Path):
    proposal = frozen_proposal("p10")
    run_dir = tmp_path / "conflict"
    launch_and_kill(
        run_dir,
        proposal=proposal,
        path="Claim",
        checkpoint="after_replace_before_receipt",
        run_id="test-conflict",
    )
    (run_dir / "effect.bin").write_bytes(b"CONFLICT-BYTES")
    completed = subprocess.run(
        [sys.executable, "-m", "fault_replay.kill_child", "--role", "reconcile", "--run-dir", str(run_dir)],
        cwd=str(WEEK3.parent),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads((run_dir / "recovery_result.json").read_text(encoding="utf-8"))
    assert result["evidence_state"] == "CONFLICT"
    assert result["memory_sentinel_seen"] is False


def test_study_matrix_has_full_grid():
    with STUDY.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 80
    checkpoints = {row["checkpoint"] for row in rows}
    assert checkpoints == {
        "after_claim_before_sealed",
        "after_sealed_before_replace",
        "after_replace_before_receipt",
        "after_receipt",
    }
    counts: dict[tuple[str, str], int] = {}
    states: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        key = (row["checkpoint"], row["path"])
        counts[key] = counts.get(key, 0) + 1
        states.setdefault(key, set()).add(row["evidence_state"])
        assert row["process_kill"] == "true"
        assert row["power_loss_claim"] == "false"
        assert row["proposal_contract_match"] == "true"
        assert row["memory_sentinel_seen"] == "false"
        assert row["exit_code"] == "1"
    assert len(counts) == 8
    assert set(counts.values()) == {10}
    assert all(len(value) == 1 for value in states.values())
    assert len({next(iter(value)) for value in states.values()}) >= 3
    assert callable(hard_kill)
