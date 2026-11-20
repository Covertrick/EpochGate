"""Claim and Outbox are separate mechanisms. A shared receiver is not that separation."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from mechanism_validation.runtime.claim_mechanism import ClaimMechanism, run_claim_path
from mechanism_validation.runtime.faults import FaultCut
from mechanism_validation.runtime.independence import assess
from mechanism_validation.runtime.outbox_mechanism import RETRY_LIMIT, OutboxMechanism, run_outbox_path
from mechanism_validation.runtime.types import EffectRequest


def _request(payload: bytes = b"SHIP|t|A|1|north|g0") -> EffectRequest:
    from mechanism_validation.runtime.hashing import sha256_bytes

    return EffectRequest(
        proposal_id="t",
        idempotency_key="effect:t",
        relay_id="relay:t",
        payload=payload,
        payload_sha256=sha256_bytes(payload),
        authority_token=1,
        authority_epoch=1,
        world_version=1,
    )


def _auth() -> dict:
    return {"token": 1, "epoch": 1, "eligible": True}


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def test_core_methods_are_not_the_same_function() -> None:
    assert ClaimMechanism.commit_claim is not OutboxMechanism.commit_business_and_relay
    assert ClaimMechanism.recover is not OutboxMechanism.recover
    assert ClaimMechanism.acknowledge_claim is not OutboxMechanism.acknowledge_relay
    assert ClaimMechanism.transition_to_sending is not OutboxMechanism.transition_attempt
    assert run_claim_path is not run_outbox_path
    assert ClaimMechanism.commit_claim.__code__ is not OutboxMechanism.commit_business_and_relay.__code__


def test_commit_does_not_call_the_other_mechanism_or_shared_private(tmp_path: Path) -> None:
    seen: list[str] = []

    def tracer(frame, event, _arg):  # type: ignore[no-untyped-def]
        if event == "call":
            seen.append(frame.f_code.co_filename)
        return tracer

    claim = ClaimMechanism(tmp_path / "claim.sqlite")
    claim.record_authority(_auth())
    sys.settrace(tracer)
    try:
        assert claim.commit_claim(_request()) is True
    finally:
        sys.settrace(None)
        claim.close()
    assert not any(path.endswith("outbox_mechanism.py") for path in seen)
    assert not any(path.endswith("not_shared.py") for path in seen)

    seen.clear()
    outbox = OutboxMechanism(tmp_path / "outbox.sqlite")
    outbox.record_authority(_auth())
    sys.settrace(tracer)
    try:
        assert outbox.commit_business_and_relay(_request()) is True
    finally:
        sys.settrace(None)
        outbox.close()
    assert not any(path.endswith("claim_mechanism.py") for path in seen)
    assert not any(path.endswith("not_shared.py") for path in seen)


def test_tables_and_transaction_paths_differ(tmp_path: Path) -> None:
    claim = ClaimMechanism(tmp_path / "claim.sqlite")
    claim.record_authority(_auth())
    assert claim.commit_claim(_request()) is True
    claim_sql = "\n".join(claim.sql_trace)
    claim.close()
    outbox = OutboxMechanism(tmp_path / "outbox.sqlite")
    outbox.record_authority(_auth())
    assert outbox.commit_business_and_relay(_request()) is True
    outbox_sql = list(outbox.sql_trace)
    outbox.close()

    claim_tables = _tables(tmp_path / "claim.sqlite")
    outbox_tables = _tables(tmp_path / "outbox.sqlite")
    assert "claim_rows" in claim_tables
    assert "outbox_business" not in claim_tables
    assert "outbox_relay" not in claim_tables
    assert {"outbox_business", "outbox_relay", "outbox_authority"} <= outbox_tables
    assert "claim_rows" not in outbox_tables
    assert "INSERT INTO claim_rows" in claim_sql
    assert "outbox_business" not in claim_sql
    business_at = next(i for i, sql in enumerate(outbox_sql) if "INSERT INTO outbox_business" in sql)
    relay_at = next(i for i, sql in enumerate(outbox_sql) if "INSERT INTO outbox_relay" in sql)
    commit_at = max(i for i, sql in enumerate(outbox_sql[: relay_at + 3]) if sql == "COMMIT")
    assert business_at < relay_at < commit_at
    assert "RECEIVER_SUBMIT_AFTER_COMMIT" not in outbox_sql


def test_fault_injection_hits_the_named_mechanism(tmp_path: Path) -> None:
    claim = ClaimMechanism(tmp_path / "claim.sqlite", fault_point="claim.commit_claim")
    claim.record_authority(_auth())
    with pytest.raises(FaultCut) as caught:
        claim.commit_claim(_request())
    assert caught.value.point == "claim.commit_claim"
    assert "claim.commit_claim" in claim.trace
    claim.close()
    assert "claim_rows" in _tables(tmp_path / "claim.sqlite")
    conn = sqlite3.connect(tmp_path / "claim.sqlite")
    try:
        count = conn.execute("SELECT COUNT(*) FROM claim_rows").fetchone()[0]
    finally:
        conn.close()
    assert count == 0

    outbox = OutboxMechanism(tmp_path / "outbox.sqlite", fault_point="outbox.commit_business_and_relay")
    outbox.record_authority(_auth())
    with pytest.raises(FaultCut) as caught_out:
        outbox.commit_business_and_relay(_request())
    assert caught_out.value.point == "outbox.commit_business_and_relay"
    outbox.close()


def test_recovery_reloads_each_mechanisms_own_rows(tmp_path: Path) -> None:
    payload = b"SHIP|recover|B|2|west|g0"
    claim = ClaimMechanism(tmp_path / "claim.sqlite")
    claim.record_authority(_auth())
    assert claim.commit_claim(_request(payload)) is True
    claim.close()
    restored = ClaimMechanism(tmp_path / "claim.sqlite")
    loaded = restored.load_persisted("effect:t")
    restored.close()
    assert loaded is not None
    assert loaded["payload"] == payload
    assert loaded["phase"] == "CLAIMED"

    outbox = OutboxMechanism(tmp_path / "outbox.sqlite")
    outbox.record_authority(_auth())
    assert outbox.commit_business_and_relay(_request(payload)) is True
    outbox.close()
    restored_out = OutboxMechanism(tmp_path / "outbox.sqlite")
    loaded_out = restored_out.load_persisted("relay:t")
    restored_out.close()
    assert loaded_out is not None
    assert loaded_out["payload"] == payload
    assert loaded_out["status"] == "PENDING"
    assert loaded_out["attempt_count"] == 0
    assert loaded_out["business_present"] is True


def test_outbox_retry_limit_reaches_dead_without_another_send(tmp_path: Path) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.submits = 0

        def submit(self, **_kwargs):  # type: ignore[no-untyped-def]
            self.submits += 1
            return {"outcome": "ACCEPTED", "applied": True, "receipt": "r", "status": "ACCEPTED"}

        def query(self, _key: str) -> dict:
            return {"status": "ABSENT", "effect_count": 0}

    outbox = OutboxMechanism(tmp_path / "outbox.sqlite")
    outbox.record_authority(_auth())
    assert outbox.commit_business_and_relay(_request()) is True
    client = FakeClient()
    for index in range(RETRY_LIMIT):
        result = outbox.relay_after_commit(client, "relay:t", attempt_id=f"a{index}")  # type: ignore[arg-type]
        assert result["outcome"] == "ACCEPTED"
    dead = outbox.relay_after_commit(client, "relay:t", attempt_id="dead")  # type: ignore[arg-type]
    outbox.close()
    assert dead["outcome"] == "DEAD"
    assert client.submits == RETRY_LIMIT
    assert "RECEIVER_SUBMIT_AFTER_COMMIT" in " ".join(outbox.sql_trace)


def test_unknown_query_does_not_publish(tmp_path: Path) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.submits = 0

        def submit(self, **_kwargs):  # type: ignore[no-untyped-def]
            self.submits += 1
            return {"outcome": "ACCEPTED", "applied": True}

        def query(self, _key: str) -> dict:
            return {"status": "UNKNOWN", "outcome": "UNKNOWN", "applied": False}

    claim = ClaimMechanism(tmp_path / "claim.sqlite")
    claim.record_authority(_auth())
    assert claim.commit_claim(_request()) is True
    client = FakeClient()
    recovered = claim.recover(client, "effect:t")  # type: ignore[arg-type]
    claim.close()
    assert recovered["outcome"] == "UNKNOWN"
    assert recovered["published_new"] is False
    assert client.submits == 0


def test_shared_receiver_does_not_make_mechanisms_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    report = assess()
    assert report["shared_receiver_does_not_establish_independence"] is True
    assert "mechanism_validation.runtime.receiver_client.PathReceiverClient" in report["shared_but_not_sufficient"]
    assert report["historical_week2_to_week4"] == "PARTIALLY_SHARED"
    stored = json_decision()
    assert stored["decision"] == report["decision"]
    assert report["decision"] in {"INDEPENDENT", "PARTIALLY_SHARED", "SAME_CORE"}

    import mechanism_validation.runtime.outbox_mechanism as outbox_module

    monkeypatch.setattr(outbox_module.OutboxMechanism, "commit_business_and_relay", ClaimMechanism.commit_claim)
    aliased = assess()
    assert aliased["decision"] == "SAME_CORE"
    assert aliased["checks"]["commit_functions_distinct"] is False


def json_decision() -> dict:
    import json

    path = Path(__file__).resolve().parents[1] / "architecture" / "mechanism_independence.json"
    return json.loads(path.read_text(encoding="utf-8"))
