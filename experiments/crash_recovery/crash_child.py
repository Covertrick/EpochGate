"""Child processes that are killed, and the separate recovery processes.

The parent kills the child with TerminateProcess (Windows) or SIGKILL.
Recovery is a new process: it reopens SQLite and the effect file. It does not
receive the child's in-memory payload.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_SCRIPTS = _REPO.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from reliability_contract import SharedReceiver  # noqa: E402

from frozen_proposals.baselines.byte_claim import ByteClaim  # noqa: E402
from frozen_proposals.baselines.outbox import Outbox  # noqa: E402
from frozen_proposals.db import connect, init_schema  # noqa: E402
from frozen_proposals.hashing import sha256_bytes  # noqa: E402
from frozen_proposals.oracles import Observation, semantics_oracle  # noqa: E402
from frozen_proposals.predicate import PREDICATE_VERSION  # noqa: E402
from fault_replay.contract import MEMORY_SENTINEL  # noqa: E402
from crash_recovery.crash_evidence import classify_durable, receiver_post_kill, rsr_gate  # noqa: E402

SENTINEL_TEXT = MEMORY_SENTINEL.decode("ascii")


class _NoFault:
    def hit(self, _point: str) -> None:
        return None


def _atomic_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _pause_until_killed() -> None:
    while True:
        time.sleep(0.25)


def _load_contract(run_dir: Path) -> dict:
    return json.loads((run_dir / "contract.json").read_text(encoding="utf-8"))


def _hold_sentinel(payload_sha: str) -> str:
    # Process memory only. Never written. Dies with this process.
    sentinel = SENTINEL_TEXT + ":" + payload_sha
    return sentinel


def _write_marker(run_dir: Path, contract: dict) -> None:
    _atomic_json(
        run_dir / "marker.json",
        {
            "pid": os.getpid(),
            "checkpoint": contract["checkpoint"],
            "path": contract["path"],
            "run_id": contract["run_id"],
        },
    )


def _checked_bytes(path: Path, expected_sha: str) -> bytes:
    payload = path.read_bytes()
    digest = sha256_bytes(payload)
    if digest != expected_sha:
        raise SystemExit(f"payload hash drift: {digest} != {expected_sha}")
    return payload


def _scan_sentinel(run_dir: Path) -> bool:
    needle = SENTINEL_TEXT.encode("ascii")
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        if needle in blob:
            return True
    return False


def _accept_file(run_dir: Path, contract: dict, payload: bytes, attempt_id: str) -> dict:
    receiver = SharedReceiver(run_dir, "effect.bin")
    result = receiver.accept(
        effect_key=contract["effect_key"],
        attempt_id=attempt_id,
        payload=payload,
    )
    if not result.get("applied"):
        raise SystemExit(f"SharedReceiver.accept did not apply: {result}")
    _fsync_file(run_dir / "effect.bin")
    return result


def _read_surface(run_dir: Path) -> dict:
    conn = connect(run_dir / "state.sqlite")
    try:
        row = conn.execute("SELECT * FROM durable_surface WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("durable_surface row missing")
        sealed = None if row["sealed_payload"] is None else bytes(row["sealed_payload"])
        return {
            "effect_key": row["effect_key"],
            "claim_status": row["claim_status"],
            "sealed_payload": sealed,
            "sealed_digest": row["sealed_digest"] or "",
            "receipt_id": row["receipt_id"],
        }
    finally:
        conn.close()


def _target_digest(run_dir: Path) -> tuple[bytes | None, str]:
    path = run_dir / "effect.bin"
    if not path.is_file():
        return None, ""
    blob = path.read_bytes()
    return blob, sha256_bytes(blob)


def _receipt(run_dir: Path) -> tuple[bool, str]:
    path = run_dir / "receipt.json"
    if not path.is_file():
        return False, ""
    body = json.loads(path.read_text(encoding="utf-8"))
    receipt = body.get("receipt") or ""
    return bool(receipt), body.get("digest") or ""


def _semantic(contract: dict, physical: bytes | None) -> str:
    obs = Observation(
        run_id=contract["run_id"],
        proposal_id=contract["proposal_id"],
        proposal_sha256=contract["payload_sha256"],
        predicate_version=PREDICATE_VERSION,
        scoring_world=contract["world_after"],
        physical_bytes=physical,
    )
    return semantics_oracle(obs)


def _result(contract: dict, **fields: object) -> dict:
    body = {
        "run_id": contract["run_id"],
        "checkpoint": contract["checkpoint"],
        "path": contract["path"],
        "reconciler_pid": os.getpid(),
        "memory_sentinel_seen": _scan_sentinel(Path(contract["run_dir"])),
    }
    body.update(fields)
    run_dir = Path(contract["run_dir"])
    _atomic_json(run_dir / "recovery_result.json", body)
    return body


def run_receiver(run_dir: Path, proposal_bin: Path) -> None:
    contract = _load_contract(run_dir)
    checkpoint = contract["checkpoint"]
    _ = _hold_sentinel(contract["payload_sha256"])
    if checkpoint == "before_receiver_accept":
        _write_marker(run_dir, contract)
        _pause_until_killed()
        return
    if checkpoint == "after_receiver_accept":
        payload = _checked_bytes(proposal_bin, contract["payload_sha256"])
        _accept_file(run_dir, contract, payload, contract["run_id"] + ":accept")
        del payload
        _write_marker(run_dir, contract)
        _pause_until_killed()
        return
    if checkpoint == "after_receiver_accept_conflict":
        payload = (run_dir / contract["conflict_name"]).read_bytes()
        if sha256_bytes(payload) == contract["payload_sha256"]:
            raise SystemExit("conflict bytes match the sealed payload")
        _accept_file(run_dir, contract, payload, contract["run_id"] + ":conflict")
        del payload
        _write_marker(run_dir, contract)
        _pause_until_killed()
        return
    raise SystemExit(f"unknown receiver checkpoint {checkpoint}")


def run_receiver_reconcile(run_dir: Path) -> dict:
    """Classify only. Do not publish and do not overwrite the effect file."""
    contract = _load_contract(run_dir)
    contract["run_dir"] = str(run_dir)
    surface = _read_surface(run_dir)
    if surface["sealed_payload"] is not None:
        actual = sha256_bytes(surface["sealed_payload"])
        if actual != surface["sealed_digest"]:
            raise RuntimeError("sealed blob does not match sealed_digest")
    physical, target_digest = _target_digest(run_dir)
    receipt_present, receipt_digest = _receipt(run_dir)
    evidence, action = classify_durable(
        sealed_digest=surface["sealed_digest"],
        target_digest=target_digest,
        receipt_present=receipt_present,
        receipt_digest=receipt_digest,
    )
    if evidence == "PUBLISH_SEALED":
        raise RuntimeError("receiver reconcile was asked to publish; the checkpoint setup is wrong")
    if evidence not in {"UNKNOWN", "CONFIRM_TARGET", "CONFLICT", "REPLAY_RECEIPT"}:
        raise RuntimeError(f"unexpected receiver evidence {evidence}")
    logical = 0 if physical is None else 1
    return _result(
        contract,
        post_kill_state=receiver_post_kill(evidence),
        recovery_action=action,
        evidence_state=evidence,
        target_digest=target_digest,
        sealed_digest=surface["sealed_digest"],
        receipt_present=receipt_present,
        logical_effect_count=logical,
        semantic_result=_semantic(contract, physical),
        published=False,
    )


def _install_authority(conn, token: int, epoch: int) -> None:
    init_schema(conn)
    conn.execute(
        "INSERT INTO authority (id, token, epoch, eligible) VALUES (1, ?, ?, 1)",
        (token, epoch),
    )


def _request(contract: dict, payload: bytes) -> SimpleNamespace:
    return SimpleNamespace(
        proposal_id=contract["proposal_id"],
        idempotency_key=contract["effect_key"],
        outbox_event_id=contract["outbox_event_id"],
        payload=payload,
        payload_sha256=sha256_bytes(payload),
        authority_token=int(contract["authority_token"]),
        authority_epoch=int(contract["authority_epoch"]),
    )


def run_relay(run_dir: Path, proposal_bin: Path) -> None:
    """Commit the outbox row, then stop before any relay send."""
    contract = _load_contract(run_dir)
    payload = _checked_bytes(proposal_bin, contract["payload_sha256"])
    _ = _hold_sentinel(contract["payload_sha256"])
    conn = connect(run_dir / "outbox.sqlite")
    try:
        _install_authority(conn, int(contract["authority_token"]), int(contract["authority_epoch"]))
        committed = Outbox().commit(conn, _request(contract, payload), _NoFault())
        if not committed:
            raise SystemExit("Outbox.commit returned false")
    finally:
        conn.close()
    del payload
    _write_marker(run_dir, contract)
    _pause_until_killed()


def run_relay_recover(run_dir: Path) -> dict:
    contract = _load_contract(run_dir)
    contract["run_dir"] = str(run_dir)
    conn = connect(run_dir / "outbox.sqlite")
    try:
        row = conn.execute(
            "SELECT payload, payload_sha256, status, attempt_count, receipt_id FROM outbox"
        ).fetchone()
        business = conn.execute("SELECT COUNT(*) AS n FROM business_effects").fetchone()["n"]
        if row is None or int(business) != 1:
            raise RuntimeError("committed outbox pair missing")
        if row["status"] != "PENDING" or int(row["attempt_count"]) != 0 or row["receipt_id"] is not None:
            raise RuntimeError("kill point is not after commit and before send")
        payload = bytes(row["payload"])
        sealed_digest = row["payload_sha256"]
    finally:
        conn.close()
    if sha256_bytes(payload) != sealed_digest:
        raise RuntimeError("outbox payload hash drift")
    if (run_dir / "effect.bin").is_file():
        raise RuntimeError("effect file exists before relay recovery")
    evidence, _action = classify_durable(
        sealed_digest=sealed_digest,
        target_digest="",
        receipt_present=False,
        receipt_digest="",
    )
    if evidence != "PUBLISH_SEALED":
        raise RuntimeError(f"relay crash classified as {evidence}")
    receiver = SharedReceiver(run_dir, "effect.bin")
    first = receiver.accept(
        effect_key=contract["effect_key"],
        attempt_id=contract["run_id"] + ":relay",
        payload=payload,
    )
    second = receiver.accept(
        effect_key=contract["effect_key"],
        attempt_id=contract["run_id"] + ":replay",
        payload=payload,
    )
    _fsync_file(run_dir / "effect.bin")
    logical = receiver.accepted_count(contract["effect_key"])
    if not first.get("applied") or second.get("applied") or second.get("status") != "replayed" or logical != 1:
        raise RuntimeError(f"replay did not keep one logical effect: first={first} second={second} count={logical}")
    physical, target_digest = _target_digest(run_dir)
    return _result(
        contract,
        post_kill_state="OUTBOX_COMMITTED_NOT_SENT",
        recovery_action="RELAY_THEN_REPLAY",
        evidence_state=evidence,
        target_digest=target_digest,
        sealed_digest=sealed_digest,
        receipt_present=False,
        logical_effect_count=logical,
        semantic_result=_semantic(contract, physical),
        published=True,
    )


def run_claim(run_dir: Path, proposal_bin: Path) -> None:
    """ByteClaim commit, then stop before the receiver is called."""
    contract = _load_contract(run_dir)
    payload = _checked_bytes(proposal_bin, contract["payload_sha256"])
    _ = _hold_sentinel(contract["payload_sha256"])
    conn = connect(run_dir / "state.sqlite")
    try:
        _install_authority(conn, int(contract["authority_token"]), int(contract["authority_epoch"]))
        committed = ByteClaim().commit(conn, _request(contract, payload), _NoFault())
        if not committed:
            raise SystemExit("ByteClaim.commit returned false")
    finally:
        conn.close()
    del payload
    _write_marker(run_dir, contract)
    _pause_until_killed()


def run_claim_recover(run_dir: Path) -> dict:
    contract = _load_contract(run_dir)
    contract["run_dir"] = str(run_dir)
    conn = connect(run_dir / "state.sqlite")
    try:
        claims = conn.execute("SELECT COUNT(*) AS n FROM byte_claims").fetchone()["n"]
        if int(claims) != 1:
            raise RuntimeError(f"byte_claims count is {claims}")
        payload = ByteClaim().payload(conn, contract["effect_key"])
    finally:
        conn.close()
    sealed_digest = sha256_bytes(payload)
    if sealed_digest != contract["payload_sha256"]:
        raise RuntimeError("claim payload does not match the frozen contract")
    if (run_dir / "effect.bin").is_file():
        raise RuntimeError("effect file exists before claim recovery")
    precheck_ms = 0.0
    if contract.get("rsr"):
        started = time.perf_counter()
        precheck = _semantic(contract, payload)
        precheck_ms = (time.perf_counter() - started) * 1000.0
        evidence, action, publish = rsr_gate(precheck)
        published = False
        if publish:
            _accept_file(run_dir, contract, payload, contract["run_id"] + ":rsr")
            published = True
        semantic = precheck
    else:
        _accept_file(run_dir, contract, payload, contract["run_id"] + ":claim")
        published = True
        physical, _target = _target_digest(run_dir)
        semantic = _semantic(contract, physical)
        evidence, action = "PUBLISH_SEALED", "PUBLISH_SEALED_BYTES"
        precheck = semantic
    physical, target_digest = _target_digest(run_dir)
    logical = 1 if published else 0
    if published and logical != 1:
        raise RuntimeError("published claim did not record one logical effect")
    if not published and physical is not None:
        raise RuntimeError("withheld recovery still wrote effect.bin")
    return _result(
        contract,
        post_kill_state="CLAIM_COMMITTED_NOT_SENT",
        recovery_action=action,
        evidence_state=evidence,
        target_digest=target_digest,
        sealed_digest=sealed_digest,
        receipt_present=False,
        logical_effect_count=logical,
        semantic_result=semantic,
        precheck_result=precheck,
        precheck_ms=precheck_ms,
        published=published,
        invalid_restore=bool(published and semantic == "INVALID"),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role",
        required=True,
        choices=(
            "receiver",
            "receiver_reconcile",
            "relay",
            "relay_recover",
            "claim",
            "claim_recover",
        ),
    )
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--proposal-bin", default="")
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    if args.role in {"receiver", "relay", "claim"}:
        if not args.proposal_bin:
            raise SystemExit(f"{args.role} requires --proposal-bin")
        proposal_bin = Path(args.proposal_bin)
        if args.role == "receiver":
            run_receiver(run_dir, proposal_bin)
            return
        if args.role == "relay":
            run_relay(run_dir, proposal_bin)
            return
        run_claim(run_dir, proposal_bin)
        return
    if args.role == "receiver_reconcile":
        run_receiver_reconcile(run_dir)
        return
    if args.role == "relay_recover":
        run_relay_recover(run_dir)
        return
    run_claim_recover(run_dir)


if __name__ == "__main__":
    main()
