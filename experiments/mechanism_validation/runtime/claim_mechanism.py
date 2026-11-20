"""Claim persistence. Own tables, own transitions, own recovery, own acknowledge."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from mechanism_validation.runtime.faults import FaultCut, FaultState
from mechanism_validation.runtime.receiver_client import PathReceiverClient
from mechanism_validation.runtime.sqlite_util import open_sqlite
from mechanism_validation.runtime.types import EffectRequest

CLAIM_SCHEMA = """
CREATE TABLE IF NOT EXISTS claim_authority (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token INTEGER NOT NULL,
    epoch INTEGER NOT NULL,
    eligible INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_rows (
    idempotency_key TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL,
    authority_token INTEGER NOT NULL,
    authority_epoch INTEGER NOT NULL,
    phase TEXT NOT NULL,
    receipt_id TEXT,
    world_version_at_commit INTEGER NOT NULL
);
"""


class ClaimMechanism:
    def __init__(self, db_path: Path, fault_point: str | None = None) -> None:
        self.db_path = Path(db_path)
        self.fault_point = fault_point
        self.trace: list[str] = []
        self.sql_trace: list[str] = []
        self.conn = open_sqlite(self.db_path)
        self.conn.executescript(CLAIM_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def _cut(self, point: str) -> None:
        self.trace.append(point)
        if self.fault_point == point:
            raise FaultCut(point)

    def _exec(self, conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        self.sql_trace.append(" ".join(sql.split()))
        return conn.execute(sql, params)

    def record_authority(self, auth: dict[str, Any]) -> None:
        self._cut("claim.record_authority")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._exec(self.conn, "DELETE FROM claim_authority")
            self._exec(
                self.conn,
                "INSERT INTO claim_authority (id, token, epoch, eligible) VALUES (1, ?, ?, ?)",
                (int(auth["token"]), int(auth["epoch"]), 1 if auth["eligible"] else 0),
            )
            self.conn.execute("COMMIT")
            self.sql_trace.append("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def commit_claim(self, request: EffectRequest) -> bool:
        self._cut("claim.commit_claim")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.sql_trace.append("BEGIN IMMEDIATE")
            current = self._exec(
                self.conn,
                "SELECT token, epoch, eligible FROM claim_authority WHERE id = 1",
            ).fetchone()
            if current is None:
                raise RuntimeError("claim authority missing")
            allowed = (
                bool(current["eligible"])
                and int(current["token"]) == int(request.authority_token)
                and int(current["epoch"]) == int(request.authority_epoch)
            )
            if not allowed:
                self.conn.execute("ROLLBACK")
                self.sql_trace.append("ROLLBACK")
                return False
            self._exec(
                self.conn,
                """
                INSERT INTO claim_rows (
                    idempotency_key, proposal_id, payload, payload_sha256,
                    authority_token, authority_epoch, phase, receipt_id, world_version_at_commit
                ) VALUES (?, ?, ?, ?, ?, ?, 'CLAIMED', NULL, ?)
                """,
                (
                    request.idempotency_key,
                    request.proposal_id,
                    request.payload,
                    request.payload_sha256,
                    request.authority_token,
                    request.authority_epoch,
                    request.world_version,
                ),
            )
            self.conn.execute("COMMIT")
            self.sql_trace.append("COMMIT")
            return True
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def transition_to_sending(self, key: str) -> None:
        self._cut("claim.transition_to_sending")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._exec(
                self.conn,
                "UPDATE claim_rows SET phase = 'SENDING' WHERE idempotency_key = ? AND phase != 'ACKED'",
                (key,),
            )
            self.conn.execute("COMMIT")
            self.sql_trace.append("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def acknowledge_claim(self, key: str, receipt_id: str | None) -> None:
        self._cut("claim.acknowledge_claim")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._exec(
                self.conn,
                "UPDATE claim_rows SET phase = 'ACKED', receipt_id = ? WHERE idempotency_key = ?",
                (receipt_id, key),
            )
            self.conn.execute("COMMIT")
            self.sql_trace.append("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def load_persisted(self, key: str) -> dict[str, Any] | None:
        self._cut("claim.load_persisted")
        row = self.conn.execute(
            """
            SELECT idempotency_key, proposal_id, payload, payload_sha256, phase, receipt_id,
                   authority_token, authority_epoch, world_version_at_commit
            FROM claim_rows WHERE idempotency_key = ?
            """,
            (key,),
        ).fetchone()
        if row is None:
            return None
        return {
            "idempotency_key": row["idempotency_key"],
            "proposal_id": row["proposal_id"],
            "payload": bytes(row["payload"]),
            "payload_sha256": row["payload_sha256"],
            "phase": row["phase"],
            "receipt_id": row["receipt_id"],
            "authority_token": int(row["authority_token"]),
            "authority_epoch": int(row["authority_epoch"]),
            "world_version_at_commit": int(row["world_version_at_commit"]),
        }

    def recover(self, client: PathReceiverClient, key: str) -> dict[str, Any]:
        """Rebuild from claim_rows only. UNKNOWN does not publish. A matching effect is confirmed, not republished."""
        self._cut("claim.recover")
        loaded = self.load_persisted(key)
        if loaded is None:
            return {"outcome": "NO_DURABLE_STATE", "published_new": False, "recovery": "NOT_RECOVERED"}
        queried = client.query(key)
        if queried.get("status") == "UNKNOWN" or queried.get("outcome") == "UNKNOWN":
            return {
                "outcome": "UNKNOWN",
                "published_new": False,
                "recovery": "RESTORED_BYTES",
                "payload_sha256": loaded["payload_sha256"],
            }
        if queried.get("status") == "PRESENT" and queried.get("payload_hash") == loaded["payload_sha256"]:
            self.acknowledge_claim(key, queried.get("receipt"))
            return {
                "outcome": "CONFIRMED_EXISTING",
                "published_new": False,
                "recovery": "RESTORED_BYTES",
                "effect_count": queried.get("effect_count"),
                "payload_sha256": loaded["payload_sha256"],
            }
        if queried.get("status") == "PRESENT":
            return {"outcome": "CONFLICT", "published_new": False, "recovery": "RESTORED_BYTES"}
        return {
            "outcome": "RESTORED_BYTES",
            "published_new": False,
            "recovery": "RESTORED_BYTES",
            "payload": loaded["payload"],
            "payload_sha256": loaded["payload_sha256"],
        }


def _blank(outcome: str, **extra: Any) -> dict[str, Any]:
    body = {
        "outcome": outcome,
        "published_new": False,
        "withheld": False,
        "recovery": "NOT_APPLICABLE",
        "local_ack": False,
        "receiver_log": [],
        "presented_token": None,
        "presented_epoch": None,
    }
    body.update(extra)
    return body


def _deliver_claim(
    client: PathReceiverClient,
    *,
    effect_key: str,
    payload: bytes,
    attempt_id: str,
    authority_token: int,
    authority_epoch: int,
    duplicate: bool,
    conflict_payload: bytes | None,
) -> list[dict[str, Any]]:
    first = client.submit(
        effect_key=effect_key,
        payload=payload,
        attempt_id=attempt_id,
        fence=False,
        authority_token=authority_token,
        authority_epoch=authority_epoch,
    )
    log = [first]
    if duplicate:
        log.append(
            client.submit(
                effect_key=effect_key,
                payload=payload,
                attempt_id=attempt_id + ":dup",
                fence=False,
                authority_token=authority_token,
                authority_epoch=authority_epoch,
            )
        )
    elif conflict_payload is not None:
        log.append(
            client.submit(
                effect_key=effect_key,
                payload=conflict_payload,
                attempt_id=attempt_id + ":conflict",
                fence=False,
                authority_token=authority_token,
                authority_epoch=authority_epoch,
            )
        )
    return log


def run_claim_path(
    db_path: Path,
    client: PathReceiverClient,
    request: EffectRequest,
    auth: dict[str, Any],
    faults: FaultState,
    world: dict[str, Any],
    *,
    attempt_id: str,
    conflict_payload: bytes,
) -> dict[str, Any]:
    mechanism = ClaimMechanism(db_path)
    try:
        faults.hit("before_effect", world, auth)
        faults.hit("authority_gate", world, auth)
        mechanism.record_authority(auth)
        committed = mechanism.commit_claim(request)
        faults.hit("after_commit_before_send", world, auth)
        faults.hit("after_admit_before_send", world, auth)
        if faults.restart:
            mechanism.close()
            restarted = ClaimMechanism(db_path)
            try:
                recovered = restarted.recover(client, request.idempotency_key)
                if recovered["outcome"] != "RESTORED_BYTES":
                    return _blank(
                        recovered["outcome"],
                        withheld=True,
                        recovery=recovered.get("recovery", "RESTORED_BYTES"),
                        presented_token=int(auth["token"]),
                        presented_epoch=int(auth["epoch"]),
                    )
                restarted.transition_to_sending(request.idempotency_key)
                faults.hit("before_send", world, auth)
                if faults.receiver_fail:
                    return _blank(
                        "RECEIVER_FAIL",
                        recovery="RESTORED_BYTES",
                        presented_token=int(auth["token"]),
                        presented_epoch=int(auth["epoch"]),
                    )
                log = _deliver_claim(
                    client,
                    effect_key=request.idempotency_key,
                    payload=recovered["payload"],
                    attempt_id=attempt_id,
                    authority_token=int(request.authority_token),
                    authority_epoch=int(request.authority_epoch),
                    duplicate=faults.duplicate,
                    conflict_payload=conflict_payload if faults.conflict else None,
                )
                first = log[0]
                if first.get("outcome") in {"ACCEPTED", "REPLAY"}:
                    restarted.acknowledge_claim(request.idempotency_key, first.get("receipt"))
                return _blank(
                    str(first.get("outcome")),
                    published_new=bool(first.get("applied")),
                    recovery="RESTORED_BYTES",
                    local_ack=first.get("outcome") in {"ACCEPTED", "REPLAY"},
                    receiver_log=log,
                    presented_token=int(request.authority_token),
                    presented_epoch=int(request.authority_epoch),
                )
            finally:
                restarted.close()
        if not committed:
            return _blank(
                "WITHHELD",
                withheld=True,
                presented_token=int(auth["token"]),
                presented_epoch=int(auth["epoch"]),
            )
        mechanism.transition_to_sending(request.idempotency_key)
        faults.hit("before_send", world, auth)
        if faults.receiver_fail:
            return _blank(
                "RECEIVER_FAIL",
                presented_token=int(request.authority_token),
                presented_epoch=int(request.authority_epoch),
            )
        log = _deliver_claim(
            client,
            effect_key=request.idempotency_key,
            payload=request.payload,
            attempt_id=attempt_id,
            authority_token=int(request.authority_token),
            authority_epoch=int(request.authority_epoch),
            duplicate=faults.duplicate,
            conflict_payload=conflict_payload if faults.conflict else None,
        )
        first = log[0]
        if first.get("status") == "UNKNOWN":
            return _blank(
                "UNKNOWN",
                receiver_log=log,
                presented_token=int(request.authority_token),
                presented_epoch=int(request.authority_epoch),
            )
        if first.get("outcome") in {"ACCEPTED", "REPLAY"}:
            mechanism.acknowledge_claim(request.idempotency_key, first.get("receipt"))
        return _blank(
            str(first.get("outcome")),
            published_new=bool(first.get("applied")),
            local_ack=first.get("outcome") in {"ACCEPTED", "REPLAY"},
            receiver_log=log,
            presented_token=int(request.authority_token),
            presented_epoch=int(request.authority_epoch),
        )
    finally:
        mechanism.close()
