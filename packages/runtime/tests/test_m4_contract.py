import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from m4_matrix import run_cell  # noqa: E402
from m4_tasks import PILOT, legal_payload, observed_payload  # noqa: E402


def _payloads(task):
    world = json.loads(json.dumps(task["initial"]))
    for key, value in task["event"]["set"].items():
        cursor = world
        parts = key.split(".")
        for part in parts[:-1]:
            cursor = cursor[part]
        cursor[parts[-1]] = value
    legal = legal_payload(task, world).encode("utf-8")
    return observed_payload(task), legal


def test_s1_b1_accepts_stale_credential_and_b2_rejects(tmp_path: Path):
    task = PILOT[0]
    old, nxt = _payloads(task)
    b1 = run_cell(task, "B1", "S1", tmp_path / "b1", old_payload=old, next_payload=nxt)
    b2 = run_cell(task, "B2", "S1", tmp_path / "b2", old_payload=old, next_payload=nxt)
    assert b1["stale_credential_accepted"] is True
    assert b2["stale_credential_accepted"] is False
    assert b2["successor_completed"] is True


def test_s3_uses_the_same_receiver_and_only_b3_recovers(tmp_path: Path):
    task = PILOT[0]
    old, nxt = _payloads(task)
    b2 = run_cell(task, "B2", "S3", tmp_path / "b2", old_payload=old, next_payload=nxt)
    b3 = run_cell(task, "B3", "S3", tmp_path / "b3", old_payload=old, next_payload=nxt)
    assert b2["oracle"]["interrupted_recovered"] is False
    assert b3["oracle"]["interrupted_recovered"] is True
    assert b3["receiver"]["accepted_count"] == 1
    assert b2["receiver"]["physical_digest"] == b3["receiver"]["physical_digest"]


def test_revoke_has_no_successor_failure(tmp_path: Path):
    task = dict(PILOT[0])
    task["successor_applicable"] = False
    old, nxt = _payloads(task)
    revoked = run_cell(
        task, "B1", "S1", tmp_path / "revoke", old_payload=old, next_payload=nxt, authority_kind="revoke",
    )
    assert revoked["successor_completed"] is False
    assert revoked["correct_stop"] is True
    assert revoked["successor_applicable"] is False
