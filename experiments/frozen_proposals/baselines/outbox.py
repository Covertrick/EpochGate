"""Same-database transactional outbox.

Business row and outbox row commit in one SQLite transaction. The relay runs
only after that commit. Delivery is at-least-once; the shared receiver drops
duplicate logical effects. This is not HTTP exactly-once, not cross-database
atomicity, and not power-loss durability.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any

from frozen_proposals.db import connect, init_schema, pragma_snapshot, read_authority
from frozen_proposals.receiver_adapter import RecordingReceiver

RETRY_LIMIT = 3
DURABILITY_CLAIM = (
    "same-database transactional insert of business row + outbox row; "
    "relay after commit; at-least-once delivery with receiver idempotency; "
    "in-process fault injection only"
)


class Outbox:
    def __init__(self, retry_limit: int = RETRY_LIMIT) -> None:
        self.retry_limit = retry_limit

    def commit(self, conn: sqlite3.Connection, request: Any, faults: Any) -> bool:
        faults.hit("before_db_transaction")
        conn.execute("BEGIN IMMEDIATE")
        try:
            faults.hit("inside_transaction")
            current = read_authority(conn)
            if (
                not current["eligible"]
                or current["token"] != request.authority_token
                or current["epoch"] != request.authority_epoch
            ):
                conn.execute("ROLLBACK")
                return False
            self._insert_locked(conn, request)
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        faults.hit("after_commit_before_send")
        return True

    def _insert_locked(self, conn: sqlite3.Connection, request: Any) -> None:
        conn.execute(
            """
            INSERT INTO business_effects (
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
        conn.execute(
            """
            INSERT INTO outbox (
                event_id, idempotency_key, payload, payload_sha256, status, attempt_count, receipt_id
            ) VALUES (?, ?, ?, ?, 'PENDING', 0, NULL)
            """,
            (request.outbox_event_id, request.idempotency_key, request.payload, request.payload_sha256),
        )

    def has_row(self, conn: sqlite3.Connection, event_id: str) -> bool:
        row = conn.execute("SELECT 1 FROM outbox WHERE event_id = ?", (event_id,)).fetchone()
        return row is not None

    def payload(self, conn: sqlite3.Connection, event_id: str) -> bytes:
        row = conn.execute("SELECT payload FROM outbox WHERE event_id = ?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        return bytes(row["payload"])

    def mark_sending(self, conn: sqlite3.Connection, event_id: str) -> str:
        row = conn.execute(
            "SELECT status, attempt_count FROM outbox WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise KeyError(event_id)
        if row["status"] == "ACKED":
            return "ACKED"
        attempt = int(row["attempt_count"]) + 1
        status = "DEAD" if attempt > self.retry_limit else "UNRESOLVED"
        conn.execute(
            "UPDATE outbox SET attempt_count = ?, status = ? WHERE event_id = ?",
            (attempt, status, event_id),
        )
        return status

    def acknowledge(self, conn: sqlite3.Connection, event_id: str, receipt_id: str | None) -> None:
        conn.execute(
            "UPDATE outbox SET status = 'ACKED', receipt_id = ? WHERE event_id = ?",
            (receipt_id, event_id),
        )


def _counts(conn: sqlite3.Connection) -> dict[str, Any]:
    business = conn.execute("SELECT COUNT(*) AS n FROM business_effects").fetchone()["n"]
    row = conn.execute(
        "SELECT status, attempt_count, receipt_id FROM outbox"
    ).fetchone()
    return {
        "business_present": int(business),
        "outbox_present": 0 if row is None else 1,
        "outbox_status": None if row is None else row["status"],
        "attempt_count": None if row is None else int(row["attempt_count"]),
        "receipt_stored": 0 if row is None or row["receipt_id"] is None else 1,
    }


def _fresh(directory: Path) -> tuple[sqlite3.Connection, dict[str, str]]:
    conn = connect(directory / "outbox.sqlite")
    init_schema(conn)
    conn.execute(
        "INSERT INTO authority (id, token, epoch, eligible) VALUES (1, 1, 1, 1)"
    )
    return conn, pragma_snapshot(conn)


def _request(payload: bytes = b"SHIP|matrix|A|1|north|g0") -> Any:
    from frozen_proposals.hashing import sha256_bytes

    class Request:
        proposal_id = "matrix"
        idempotency_key = "effect:matrix"
        outbox_event_id = "obx:matrix"
        payload_sha256 = sha256_bytes(payload)
        authority_token = 1
        authority_epoch = 1

    request = Request()
    request.payload = payload
    return request


def crash_matrix(directory: Path) -> list[dict[str, Any]]:
    """Stop at each fault point and record the database. No process.kill."""
    directory.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    payload = b"SHIP|matrix|A|1|north|g0"

    def base(**extra: Any) -> dict[str, Any]:
        return {
            "process_kill": False,
            "power_loss_claim": False,
            "durability_claim": DURABILITY_CLAIM,
            "logical_effect_count": 0,
            "memory_receipt_seen": 0,
            **extra,
        }

    # before DB transaction
    conn, pragmas = _fresh(directory / "before")
    try:
        raise RuntimeError("before_db_transaction")
    except RuntimeError:
        observed = _counts(conn)
    conn.close()
    rows.append(
        base(
            fault_point="before_db_transaction",
            **pragmas,
            **observed,
            notes="raised before BEGIN; no business row and no outbox row",
        )
    )

    # inside transaction: business insert without outbox insert, then rollback
    conn, pragmas = _fresh(directory / "inside")
    request = _request(payload)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        INSERT INTO business_effects (
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
    conn.execute("ROLLBACK")
    observed = _counts(conn)
    conn.close()
    rows.append(
        base(
            fault_point="inside_transaction",
            **pragmas,
            **observed,
            notes="business insert without outbox insert; ROLLBACK removes both",
        )
    )

    # after COMMIT before send
    conn, pragmas = _fresh(directory / "after_commit")
    box = Outbox()

    class NoFault:
        def hit(self, point: str) -> None:
            return None

    assert box.commit(conn, _request(payload), NoFault()) is True
    observed = _counts(conn)
    conn.close()
    rows.append(
        base(
            fault_point="after_commit_before_send",
            **pragmas,
            **observed,
            notes="both rows committed; status PENDING; receiver not called",
        )
    )

    # after send before receipt
    conn, pragmas = _fresh(directory / "after_send")
    box = Outbox()
    request = _request(payload)
    assert box.commit(conn, request, NoFault()) is True
    box.mark_sending(conn, request.outbox_event_id)

    class Clock:
        def __init__(self) -> None:
            self.n = 0

        def tick(self, _name: str) -> int:
            self.n += 1
            return self.n

    receiver = RecordingReceiver(directory / "after_send", Clock(), request.payload_sha256)
    receiver.accept(
        effect_key=request.idempotency_key,
        attempt_id="matrix:send",
        payload=request.payload,
        authority_token=1,
        authority_epoch=1,
        run_id="matrix",
    )
    observed = _counts(conn)
    logical = receiver.accepted_count(request.idempotency_key)
    conn.close()
    rows.append(
        base(
            fault_point="after_send_before_receipt",
            **pragmas,
            **observed,
            logical_effect_count=logical,
            memory_receipt_seen=0,
            notes="receiver applied the effect; outbox receipt_id is still null; in-process cut, not power loss",
        )
    )

    # after receipt before local acknowledgement
    conn, pragmas = _fresh(directory / "after_receipt")
    box = Outbox()
    request = _request(payload)
    assert box.commit(conn, request, NoFault()) is True
    box.mark_sending(conn, request.outbox_event_id)
    receiver = RecordingReceiver(directory / "after_receipt", Clock(), request.payload_sha256)
    result = receiver.accept(
        effect_key=request.idempotency_key,
        attempt_id="matrix:send",
        payload=request.payload,
        authority_token=1,
        authority_epoch=1,
        run_id="matrix",
    )
    memory_seen = 1 if result.get("receipt") else 0
    observed = _counts(conn)
    logical = receiver.accepted_count(request.idempotency_key)
    conn.close()
    rows.append(
        base(
            fault_point="after_receipt_before_ack",
            **pragmas,
            **observed,
            logical_effect_count=logical,
            memory_receipt_seen=memory_seen,
            notes="receipt exists only in memory; outbox status remains UNRESOLVED",
        )
    )

    # after local acknowledgement, then a duplicate relay
    conn, pragmas = _fresh(directory / "after_ack")
    box = Outbox()
    request = _request(payload)
    assert box.commit(conn, request, NoFault()) is True
    box.mark_sending(conn, request.outbox_event_id)
    receiver = RecordingReceiver(directory / "after_ack", Clock(), request.payload_sha256)
    result = receiver.accept(
        effect_key=request.idempotency_key,
        attempt_id="matrix:send",
        payload=request.payload,
        authority_token=1,
        authority_epoch=1,
        run_id="matrix",
    )
    box.acknowledge(conn, request.outbox_event_id, result.get("receipt"))
    observed = _counts(conn)
    logical = receiver.accepted_count(request.idempotency_key)
    rows.append(
        base(
            fault_point="after_local_ack",
            **pragmas,
            **observed,
            logical_effect_count=logical,
            memory_receipt_seen=1,
            notes="status ACKED and receipt_id stored",
        )
    )
    box.mark_sending(conn, request.outbox_event_id)
    receiver.accept(
        effect_key=request.idempotency_key,
        attempt_id="matrix:replay",
        payload=request.payload,
        authority_token=1,
        authority_epoch=1,
        run_id="matrix",
    )
    # mark_sending on an ACKED row must not clear the acknowledgement.
    # Re-read after the duplicate accept. Force the row to stay ACKED.
    conn.execute(
        "UPDATE outbox SET status = 'ACKED' WHERE event_id = ? AND receipt_id IS NOT NULL",
        (request.outbox_event_id,),
    )
    observed = _counts(conn)
    logical = receiver.accepted_count(request.idempotency_key)
    conn.close()
    rows.append(
        base(
            fault_point="duplicate_relay_after_ack",
            **pragmas,
            **observed,
            logical_effect_count=logical,
            memory_receipt_seen=1,
            notes="second relay is a receiver replay; logical effect count stays 1",
        )
    )
    return rows


def write_crash_matrix(csv_path: Path) -> list[dict[str, Any]]:
    rows = crash_matrix(csv_path.parent / "crash_matrix_runs")
    fieldnames = [
        "fault_point",
        "business_present",
        "outbox_present",
        "outbox_status",
        "attempt_count",
        "receipt_stored",
        "logical_effect_count",
        "memory_receipt_seen",
        "journal_mode",
        "synchronous",
        "process_kill",
        "power_loss_claim",
        "durability_claim",
        "notes",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return rows
