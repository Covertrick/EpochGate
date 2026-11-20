"""Same-database outbox. Business row and relay row commit together. Relay runs after that commit."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from mechanism_validation.runtime.faults import FaultCut, FaultState
from mechanism_validation.runtime.receiver_client import PathReceiverClient
from mechanism_validation.runtime.sqlite_util import open_sqlite
from mechanism_validation.runtime.types import EffectRequest

RETRY_LIMIT = 3

OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox_authority (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token INTEGER NOT NULL,
    epoch INTEGER NOT NULL,
    eligible INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox_business (
    idempotency_key TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL,
    authority_token INTEGER NOT NULL,
    authority_epoch INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox_relay (
    relay_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    retry_limit INTEGER NOT NULL,
    receipt_id TEXT
);
"""


class OutboxMechanism:
    def __init__(self, db_path: Path, fault_point: str | None = None, retry_limit: int = RETRY_LIMIT) -> None:
        self.db_path = Path(db_path)
        self.fault_point = fault_point
        self.retry_limit = retry_limit
        self.trace: list[str] = []
        self.sql_trace: list[str] = []
        self.conn = open_sqlite(self.db_path)
        self.conn.executescript(OUTBOX_SCHEMA)

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
        self._cut("outbox.record_authority")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._exec(self.conn, "DELETE FROM outbox_authority")
            self._exec(
                self.conn,
                "INSERT INTO outbox_authority (id, token, epoch, eligible) VALUES (1, ?, ?, ?)",
                (int(auth["token"]), int(auth["epoch"]), 1 if auth["eligible"] else 0),
            )
            self.conn.execute("COMMIT")
            self.sql_trace.append("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def commit_business_and_relay(self, request: EffectRequest) -> bool:
        self._cut("outbox.commit_business_and_relay")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.sql_trace.append("BEGIN IMMEDIATE")
            current = self._exec(
                self.conn,
                "SELECT token, epoch, eligible FROM outbox_authority WHERE id = 1",
            ).fetchone()
            if current is None:
                raise RuntimeError("outbox authority missing")
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
                INSERT INTO outbox_business (
                    idempotency_key, proposal_id, payload, payload_sha256, authority_token, authority_epoch
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    request.idempotency_key,
                    request.proposal_id,
                    request.payload,
                    request.payload_sha256,
                    request.authority_token,
                    request.authority_epoch,
                ),
            )
            self._exec(
                self.conn,
                """
                INSERT INTO outbox_relay (
                    relay_id, idempotency_key, payload, payload_sha256, status, attempt_count, retry_limit, receipt_id
                ) VALUES (?, ?, ?, ?, 'PENDING', 0, ?, NULL)
                """,
                (
                    request.relay_id,
                    request.idempotency_key,
                    request.payload,
                    request.payload_sha256,
                    self.retry_limit,
                ),
            )
            self.conn.execute("COMMIT")
            self.sql_trace.append("COMMIT")
            return True
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def transition_attempt(self, relay_id: str) -> str:
        """Increment attempt_count. Past the retry limit the row becomes DEAD and is not sent."""
        self._cut("outbox.transition_attempt")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._exec(
                self.conn,
                "SELECT status, attempt_count, retry_limit FROM outbox_relay WHERE relay_id = ?",
                (relay_id,),
            ).fetchone()
            if row is None:
                raise KeyError(relay_id)
            if row["status"] in {"ACKED", "DEAD"}:
                self.conn.execute("COMMIT")
                self.sql_trace.append("COMMIT")
                return str(row["status"])
            attempt = int(row["attempt_count"]) + 1
            status = "DEAD" if attempt > int(row["retry_limit"]) else "UNRESOLVED"
            self._exec(
                self.conn,
                "UPDATE outbox_relay SET attempt_count = ?, status = ? WHERE relay_id = ?",
                (attempt, status, relay_id),
            )
            self.conn.execute("COMMIT")
            self.sql_trace.append("COMMIT")
            return status
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def acknowledge_relay(self, relay_id: str, receipt_id: str | None) -> None:
        self._cut("outbox.acknowledge_relay")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._exec(
                self.conn,
                "UPDATE outbox_relay SET status = 'ACKED', receipt_id = ? WHERE relay_id = ?",
                (receipt_id, relay_id),
            )
            self.conn.execute("COMMIT")
            self.sql_trace.append("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def load_persisted(self, relay_id: str) -> dict[str, Any] | None:
        self._cut("outbox.load_persisted")
        relay = self.conn.execute(
            """
            SELECT relay_id, idempotency_key, payload, payload_sha256, status, attempt_count, retry_limit, receipt_id
            FROM outbox_relay WHERE relay_id = ?
            """,
            (relay_id,),
        ).fetchone()
        if relay is None:
            return None
        business = self.conn.execute(
            "SELECT proposal_id, payload_sha256 FROM outbox_business WHERE idempotency_key = ?",
            (relay["idempotency_key"],),
        ).fetchone()
        return {
            "relay_id": relay["relay_id"],
            "idempotency_key": relay["idempotency_key"],
            "payload": bytes(relay["payload"]),
            "payload_sha256": relay["payload_sha256"],
            "status": relay["status"],
            "attempt_count": int(relay["attempt_count"]),
            "retry_limit": int(relay["retry_limit"]),
            "receipt_id": relay["receipt_id"],
            "business_present": business is not None,
            "proposal_id": None if business is None else business["proposal_id"],
        }

    def relay_after_commit(
        self,
        client: PathReceiverClient,
        relay_id: str,
        *,
        attempt_id: str,
        duplicate: bool = False,
        conflict_payload: bytes | None = None,
    ) -> dict[str, Any]:
        self._cut("outbox.relay_after_commit")
        status = self.transition_attempt(relay_id)
        loaded = self.load_persisted(relay_id)
        if loaded is None:
            return {"outcome": "NO_DURABLE_STATE", "published_new": False, "receiver_log": []}
        if status in {"DEAD", "ACKED"}:
            return {"outcome": status, "published_new": False, "receiver_log": [], "attempt_count": loaded["attempt_count"]}
        self.sql_trace.append("RECEIVER_SUBMIT_AFTER_COMMIT")
        log = _deliver_outbox(
            client,
            effect_key=loaded["idempotency_key"],
            payload=loaded["payload"],
            attempt_id=attempt_id,
            duplicate=duplicate,
            conflict_payload=conflict_payload,
        )
        return {
            "outcome": log[0].get("outcome"),
            "published_new": bool(log[0].get("applied")),
            "receiver_log": log,
            "attempt_count": loaded["attempt_count"],
            "status": status,
        }

    def recover(self, client: PathReceiverClient, relay_id: str) -> dict[str, Any]:
        self._cut("outbox.recover")
        loaded = self.load_persisted(relay_id)
        if loaded is None:
            return {"outcome": "NO_DURABLE_STATE", "published_new": False, "recovery": "NOT_RECOVERED"}
        if loaded["status"] == "DEAD":
            return {"outcome": "DEAD", "published_new": False, "recovery": "RESTORED_BYTES", "attempt_count": loaded["attempt_count"]}
        queried = client.query(loaded["idempotency_key"])
        if queried.get("status") == "UNKNOWN" or queried.get("outcome") == "UNKNOWN":
            return {"outcome": "UNKNOWN", "published_new": False, "recovery": "RESTORED_BYTES"}
        if queried.get("status") == "PRESENT" and queried.get("payload_hash") == loaded["payload_sha256"]:
            self.acknowledge_relay(relay_id, queried.get("receipt"))
            return {
                "outcome": "CONFIRMED_EXISTING",
                "published_new": False,
                "recovery": "RESTORED_BYTES",
                "effect_count": queried.get("effect_count"),
            }
        if queried.get("status") == "PRESENT":
            return {"outcome": "CONFLICT", "published_new": False, "recovery": "RESTORED_BYTES"}
        return {
            "outcome": "RESTORED_BYTES",
            "published_new": False,
            "recovery": "RESTORED_BYTES",
            "payload": loaded["payload"],
            "payload_sha256": loaded["payload_sha256"],
            "attempt_count": loaded["attempt_count"],
        }


def _deliver_outbox(
    client: PathReceiverClient,
    *,
    effect_key: str,
    payload: bytes,
    attempt_id: str,
    duplicate: bool,
    conflict_payload: bytes | None,
) -> list[dict[str, Any]]:
    first = client.submit(
        effect_key=effect_key,
        payload=payload,
        attempt_id=attempt_id,
        fence=False,
    )
    log = [first]
    if duplicate:
        log.append(
            client.submit(
                effect_key=effect_key,
                payload=payload,
                attempt_id=attempt_id + ":dup",
                fence=False,
            )
        )
    elif conflict_payload is not None:
        log.append(
            client.submit(
                effect_key=effect_key,
                payload=conflict_payload,
                attempt_id=attempt_id + ":conflict",
                fence=False,
            )
        )
    return log


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
        "attempt_count": None,
    }
    body.update(extra)
    return body


def run_outbox_path(
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
    mechanism = OutboxMechanism(db_path)
    try:
        faults.hit("before_effect", world, auth)
        faults.hit("authority_gate", world, auth)
        mechanism.record_authority(auth)
        committed = mechanism.commit_business_and_relay(request)
        faults.hit("after_commit_before_send", world, auth)
        faults.hit("after_admit_before_send", world, auth)
        if not committed and not faults.restart:
            return _blank(
                "WITHHELD",
                withheld=True,
                presented_token=int(auth["token"]),
                presented_epoch=int(auth["epoch"]),
            )
        if faults.restart:
            mechanism.close()
            restarted = OutboxMechanism(db_path)
            try:
                recovered = restarted.recover(client, request.relay_id)
                if recovered["outcome"] != "RESTORED_BYTES":
                    return _blank(
                        recovered["outcome"],
                        withheld=True,
                        recovery=recovered.get("recovery", "RESTORED_BYTES"),
                        presented_token=int(auth["token"]),
                        presented_epoch=int(auth["epoch"]),
                    )
                faults.hit("before_send", world, auth)
                if faults.receiver_fail:
                    return _blank("RECEIVER_FAIL", recovery="RESTORED_BYTES")
                relayed = restarted.relay_after_commit(
                    client,
                    request.relay_id,
                    attempt_id=attempt_id,
                    duplicate=faults.duplicate,
                    conflict_payload=conflict_payload if faults.conflict else None,
                )
                first = (relayed.get("receiver_log") or [{}])[0]
                if first.get("outcome") in {"ACCEPTED", "REPLAY"}:
                    restarted.acknowledge_relay(request.relay_id, first.get("receipt"))
                return _blank(
                    str(relayed.get("outcome")),
                    published_new=bool(relayed.get("published_new")),
                    recovery="RESTORED_BYTES",
                    local_ack=first.get("outcome") in {"ACCEPTED", "REPLAY"},
                    receiver_log=relayed.get("receiver_log") or [],
                    presented_token=int(request.authority_token),
                    presented_epoch=int(request.authority_epoch),
                    attempt_count=relayed.get("attempt_count"),
                )
            finally:
                restarted.close()
        faults.hit("before_send", world, auth)
        if faults.receiver_fail:
            return _blank(
                "RECEIVER_FAIL",
                presented_token=int(request.authority_token),
                presented_epoch=int(request.authority_epoch),
            )
        relayed = mechanism.relay_after_commit(
            client,
            request.relay_id,
            attempt_id=attempt_id,
            duplicate=faults.duplicate,
            conflict_payload=conflict_payload if faults.conflict else None,
        )
        first = (relayed.get("receiver_log") or [{}])[0]
        if first.get("status") == "UNKNOWN" or first.get("outcome") == "UNKNOWN":
            return _blank(
                "UNKNOWN",
                receiver_log=relayed.get("receiver_log") or [],
                attempt_count=relayed.get("attempt_count"),
                presented_token=int(request.authority_token),
                presented_epoch=int(request.authority_epoch),
            )
        if first.get("outcome") in {"ACCEPTED", "REPLAY"}:
            mechanism.acknowledge_relay(request.relay_id, first.get("receipt"))
        return _blank(
            str(relayed.get("outcome")),
            published_new=bool(relayed.get("published_new")),
            local_ack=first.get("outcome") in {"ACCEPTED", "REPLAY"},
            receiver_log=relayed.get("receiver_log") or [],
            presented_token=int(request.authority_token),
            presented_epoch=int(request.authority_epoch),
            attempt_count=relayed.get("attempt_count"),
        )
    finally:
        mechanism.close()
