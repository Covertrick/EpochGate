"""Child process and the separate reconciliation process.

The child pauses at one checkpoint. It does not catch the kill. Recovery is a
new process: it reopens SQLite and the effect file, and it never receives the
in-memory payload.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_SCRIPTS = _REPO.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from reliability_contract import SharedReceiver  # noqa: E402

from frozen_proposals.db import connect  # noqa: E402
from frozen_proposals.hashing import sha256_bytes  # noqa: E402
from frozen_proposals.oracles import Observation, semantics_oracle  # noqa: E402
from frozen_proposals.predicate import PREDICATE_VERSION  # noqa: E402
from fault_replay.contract import (  # noqa: E402
    LOST_RECEIPT_CHECKPOINT,
    MEMORY_SENTINEL,
    STUDY_CHECKPOINTS,
)

SENTINEL_TEXT = MEMORY_SENTINEL.decode("ascii")


def _init_ledger(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kill_ledger (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            path TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            claim_status TEXT NOT NULL,
            sealed_payload BLOB,
            sealed_digest TEXT,
            receipt_id TEXT,
            receipt_required INTEGER NOT NULL
        )
        """
    )


def _fsync_file(path: Path) -> None:
    if not path.exists():
        return
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _pause_until_killed() -> None:
    while True:
        time.sleep(0.25)


def _load_contract(run_dir: Path) -> dict:
    return json.loads((run_dir / "contract.json").read_text(encoding="utf-8"))


def _commit_claim(run_dir: Path, contract: dict, payload_sha: str) -> None:
    conn = connect(run_dir / "state.sqlite")
    try:
        _init_ledger(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO kill_ledger (
                id, path, proposal_id, payload_sha256, claim_status,
                sealed_payload, sealed_digest, receipt_id, receipt_required
            ) VALUES (1, ?, ?, ?, 'CLAIMED', NULL, NULL, NULL, ?)
            """,
            (
                contract["path"],
                contract["proposal_id"],
                payload_sha,
                1 if contract["checkpoint"] == LOST_RECEIPT_CHECKPOINT else 0,
            ),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


def _commit_seal(run_dir: Path, payload: bytes, payload_sha: str) -> None:
    conn = connect(run_dir / "state.sqlite")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE kill_ledger
            SET claim_status = 'SEALED', sealed_payload = ?, sealed_digest = ?
            WHERE id = 1
            """,
            (payload, payload_sha),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


def _mark_replaced(run_dir: Path) -> None:
    conn = connect(run_dir / "state.sqlite")
    try:
        conn.execute("UPDATE kill_ledger SET claim_status = 'REPLACED' WHERE id = 1")
    finally:
        conn.close()


def _commit_receipt(run_dir: Path, receipt_id: str) -> None:
    conn = connect(run_dir / "state.sqlite")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE kill_ledger SET claim_status = 'RECEIPT', receipt_id = ? WHERE id = 1",
            (receipt_id,),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


def _write_marker(run_dir: Path, contract: dict) -> None:
    _atomic_json(
        run_dir / "marker.json",
        {
            "pid": os.getpid(),
            "checkpoint": contract["checkpoint"],
            "path": contract["path"],
            "run_id": contract["run_id"],
            "proposal_id": contract["proposal_id"],
        },
    )


def run_child(run_dir: Path, proposal_bin: Path) -> None:
    contract = _load_contract(run_dir)
    payload = proposal_bin.read_bytes()
    payload_sha = sha256_bytes(payload)
    if payload_sha != contract["payload_sha256"]:
        raise SystemExit("proposal bytes do not match the frozen contract")
    # Held only in this process. Never written. Die with the process.
    _memory_only = SENTINEL_TEXT + ":" + payload_sha
    del payload_sha
    _ = _memory_only

    checkpoint = contract["checkpoint"]
    known = set(STUDY_CHECKPOINTS) | {LOST_RECEIPT_CHECKPOINT}
    if checkpoint not in known:
        raise SystemExit(f"unknown checkpoint {checkpoint}")

    _atomic_json(
        run_dir / "memory_probe.json",
        {"durable_copy_of_sentinel": None, "note": "sentinel is process memory only"},
    )
    _commit_claim(run_dir, contract, contract["payload_sha256"])
    if checkpoint == "after_claim_before_sealed":
        _write_marker(run_dir, contract)
        _pause_until_killed()

    payload = proposal_bin.read_bytes()
    digest = sha256_bytes(payload)
    _commit_seal(run_dir, payload, digest)
    if checkpoint in {"after_sealed_before_replace", LOST_RECEIPT_CHECKPOINT}:
        _write_marker(run_dir, contract)
        _pause_until_killed()

    receiver = SharedReceiver(run_dir, "effect.bin")
    result = receiver.accept(
        effect_key=contract["effect_key"],
        attempt_id=contract["run_id"] + ":replace",
        payload=payload,
    )
    _fsync_file(run_dir / "effect.bin")
    # Receipt stays in this local name until the receipt checkpoint.
    receipt_in_memory = result.get("receipt")
    _atomic_json(
        run_dir / "receiver_log.json",
        [
            {
                "effect_key": contract["effect_key"],
                "attempt_id": contract["run_id"] + ":replace",
                "accepted": True,
                "detail": "accepted",
                "digest": result.get("digest"),
                "token": contract["authority_token"],
                "epoch": contract["authority_epoch"],
                "seq": 1,
                "receipt": None,
                "run_id": contract["run_id"],
            }
        ],
    )
    _mark_replaced(run_dir)
    if checkpoint == "after_replace_before_receipt":
        _write_marker(run_dir, contract)
        _pause_until_killed()

    if not receipt_in_memory:
        raise SystemExit("receiver did not return a receipt")
    _atomic_json(
        run_dir / "receipt.json",
        {
            "receipt": receipt_in_memory,
            "digest": digest,
            "effect_key": contract["effect_key"],
        },
    )
    _commit_receipt(run_dir, receipt_in_memory)
    log = json.loads((run_dir / "receiver_log.json").read_text(encoding="utf-8"))
    log[0]["receipt"] = receipt_in_memory
    _atomic_json(run_dir / "receiver_log.json", log)
    _write_marker(run_dir, contract)
    _pause_until_killed()


def _read_ledger(run_dir: Path):
    conn = connect(run_dir / "state.sqlite")
    try:
        row = conn.execute("SELECT * FROM kill_ledger WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("kill_ledger row missing")
        return {key: row[key] for key in row.keys()}
    finally:
        conn.close()


def _post_kill_state(row: dict, target_exists: bool, receipt_present: bool) -> str:
    if row["sealed_payload"] is None:
        return "CLAIMED_UNSEALED"
    if int(row["receipt_required"]) and not receipt_present and not target_exists:
        return "SEALED_RECEIPT_LOST"
    if not target_exists:
        return "SEALED_NOT_REPLACED"
    if not receipt_present:
        return "REPLACED_NO_RECEIPT"
    return "RECEIPT_DURABLE"


def _scan_sentinel(run_dir: Path) -> bool:
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        if SENTINEL_TEXT.encode("ascii") in path.read_bytes():
            return True
    return False


def run_reconcile(run_dir: Path) -> dict:
    contract = _load_contract(run_dir)
    saw_sentinel = _scan_sentinel(run_dir)
    row = _read_ledger(run_dir)
    target_path = run_dir / "effect.bin"
    receipt_path = run_dir / "receipt.json"
    target_before = target_path.read_bytes() if target_path.is_file() else None
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else None
    receipt_present = bool(receipt and receipt.get("receipt"))
    sealed = None if row["sealed_payload"] is None else bytes(row["sealed_payload"])
    sealed_digest = row["sealed_digest"]
    target_digest = None if target_before is None else sha256_bytes(target_before)
    post_kill = _post_kill_state(row, target_before is not None, receipt_present)

    restored = False
    if sealed_digest and target_digest and sealed_digest != target_digest:
        evidence = "CONFLICT"
        action = "HOLD_CONFLICT"
    elif (
        receipt_present
        and sealed_digest
        and receipt.get("digest") == sealed_digest
        and target_digest == sealed_digest
    ):
        evidence = "REPLAY_RECEIPT"
        action = "REPLAY_STORED_RECEIPT"
    elif target_digest and sealed_digest and target_digest == sealed_digest and not receipt_present:
        evidence = "CONFIRM_TARGET"
        action = "CONFIRM_EXISTING_TARGET"
    elif sealed is not None and target_before is None and not receipt_present:
        receiver = SharedReceiver(run_dir, "effect.bin")
        published = receiver.accept(
            effect_key=contract["effect_key"],
            attempt_id=contract["run_id"] + ":recover",
            payload=sealed,
        )
        _fsync_file(target_path)
        _atomic_json(
            run_dir / "receiver_log.json",
            [
                {
                    "effect_key": contract["effect_key"],
                    "attempt_id": contract["run_id"] + ":recover",
                    "accepted": bool(published.get("applied")),
                    "detail": published.get("status"),
                    "digest": published.get("digest"),
                    "token": contract["authority_token"],
                    "epoch": contract["authority_epoch"],
                    "seq": 1,
                    "receipt": published.get("receipt"),
                    "run_id": contract["run_id"],
                }
            ],
        )
        evidence = "PUBLISH_SEALED"
        action = "PUBLISH_SEALED_BYTES"
        restored = True
        target_digest = published.get("digest")
    else:
        evidence = "UNKNOWN"
        action = "WITHHOLD_UNSEALED"

    final_target = target_path.read_bytes() if target_path.is_file() else None
    obs = Observation(
        run_id=contract["run_id"],
        proposal_id=contract["proposal_id"],
        proposal_sha256=contract["payload_sha256"],
        predicate_version=PREDICATE_VERSION,
        scoring_world=contract["world_after"],
        physical_bytes=final_target,
    )
    result = {
        "run_id": contract["run_id"],
        "checkpoint": contract["checkpoint"],
        "path": contract["path"],
        "proposal_id": contract["proposal_id"],
        "post_kill_state": post_kill,
        "recovery_action": action,
        "evidence_state": evidence,
        "target_digest": target_digest or "",
        "sealed_digest": sealed_digest or "",
        "receipt_present": receipt_present,
        "semantic_oracle_result": semantics_oracle(obs),
        "payload_sha256": contract["payload_sha256"],
        "world_before_hash": contract["world_before_hash"],
        "world_after_hash": contract["world_after_hash"],
        "authority_initial_hash": contract["authority_initial_hash"],
        "restored_bytes": restored,
        "memory_sentinel_seen": saw_sentinel,
        "reconciler_pid": os.getpid(),
        "ledger_readable": True,
        "ledger_claim_status": row["claim_status"],
        "physical_present": final_target is not None,
    }
    _atomic_json(run_dir / "recovery_result.json", result)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("child", "reconcile"), required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--proposal-bin", default="")
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    if args.role == "child":
        if not args.proposal_bin:
            raise SystemExit("child requires --proposal-bin")
        run_child(run_dir, Path(args.proposal_bin))
        return
    run_reconcile(run_dir)


if __name__ == "__main__":
    main()
