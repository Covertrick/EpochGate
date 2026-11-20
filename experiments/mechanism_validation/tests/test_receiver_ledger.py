"""The target ledger lives in the receiver process. The path does not open it."""

from __future__ import annotations

from pathlib import Path

from mechanism_validation.runtime.auditor import effect_for, read_target_ledger
from mechanism_validation.runtime.claim_mechanism import ClaimMechanism
from mechanism_validation.runtime.outbox_mechanism import OutboxMechanism
from mechanism_validation.runtime.receiver_client import PathReceiverClient
from mechanism_validation.runtime.receiver_process import ReceiverProcess
from mechanism_validation.runtime.receiver_server import apply_submit


def test_path_modules_do_not_open_the_target_ledger() -> None:
    claim_src = Path(ClaimMechanism.__module__.replace(".", "/") + ".py")
    # Source files are the authority for this boundary.
    root = Path(__file__).resolve().parents[1] / "runtime"
    claim_text = (root / "claim_mechanism.py").read_text(encoding="utf-8")
    outbox_text = (root / "outbox_mechanism.py").read_text(encoding="utf-8")
    for text in (claim_text, outbox_text):
        assert "target_effects" not in text
        assert "read_target_ledger" not in text
        assert "set_world_version" not in text
        assert "ControlReceiverClient" not in text
    assert not hasattr(PathReceiverClient, "audit")
    assert not hasattr(PathReceiverClient, "set_world_version")
    _ = claim_src


def test_replay_conflict_and_atomic_fence(tmp_path: Path) -> None:
    receiver = ReceiverProcess(tmp_path / "receiver")
    receiver.start()
    try:
        client = receiver.path_client()
        control = receiver.control_client()
        assert control.set_world_version(1)["status"] == "OK"
        assert control.set_authority(token=1, epoch=1, eligible=True)["status"] == "OK"
        first = client.submit(
            effect_key="k",
            payload=b"same",
            attempt_id="a1",
            fence=True,
            expected_world_version=1,
            expected_payload_hash=__import__("hashlib").sha256(b"same").hexdigest(),
            expected_authority={"token": 1, "epoch": 1, "eligible": True},
            authority_token=1,
            authority_epoch=1,
        )
        assert first["outcome"] == "ACCEPTED"
        assert first["effect_count"] == 1
        replay = client.submit(
            effect_key="k",
            payload=b"same",
            attempt_id="a2",
            fence=False,
            authority_token=1,
            authority_epoch=1,
        )
        assert replay["outcome"] == "REPLAY"
        assert replay["applied"] is False
        assert replay["effect_count"] == 1
        conflict = client.submit(
            effect_key="k",
            payload=b"other",
            attempt_id="a3",
            fence=False,
        )
        assert conflict["outcome"] == "CONFLICT"
        assert conflict["effect_count"] == 1
        mismatch = client.submit(
            effect_key="fresh",
            payload=b"new",
            attempt_id="a4",
            fence=True,
            expected_world_version=9,
            expected_payload_hash=__import__("hashlib").sha256(b"new").hexdigest(),
            expected_authority={"token": 1, "epoch": 1, "eligible": True},
        )
        assert mismatch["outcome"] == "WORLD_VERSION_MISMATCH"
        assert mismatch["applied"] is False
        unknown = client.submit(
            effect_key="fresh",
            payload=b"new",
            attempt_id="a5",
            fence=True,
            expected_world_version=None,
            expected_payload_hash=__import__("hashlib").sha256(b"new").hexdigest(),
            expected_authority={"token": 1, "epoch": 1, "eligible": True},
        )
        assert unknown["outcome"] == "UNKNOWN"
        authority = client.submit(
            effect_key="fresh",
            payload=b"new",
            attempt_id="a6",
            fence=True,
            expected_world_version=1,
            expected_payload_hash=__import__("hashlib").sha256(b"new").hexdigest(),
            expected_authority={"token": 4, "epoch": 4, "eligible": True},
        )
        assert authority["outcome"] == "AUTHORITY_SUPERSEDED"
    finally:
        receiver.stop()

    ledger = read_target_ledger(receiver.db_path)
    assert [row["effect_key"] for row in ledger] == ["k"]
    assert ledger[0]["payload"] == b"same"
    assert ledger[0]["logical_effect_count"] == 1
    assert effect_for(receiver.db_path, "fresh") is None


def test_fence_check_and_insert_share_one_function() -> None:
    source = Path(apply_submit.__code__.co_filename).read_text(encoding="utf-8")
    start = source.index("def apply_submit")
    end = source.index("\ndef apply_query")
    body = source[start:end]
    assert body.count("BEGIN IMMEDIATE") == 1
    assert "COMMIT" in body
    assert "INSERT INTO target_effects" in body
    assert "def _select" not in body
    assert "def _insert" not in body
