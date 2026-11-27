import hashlib
import time
from pathlib import Path

from fastapi.testclient import TestClient

from epochgate_runtime.api import Runtime
from epochgate_runtime.httpapp import create_app


def test_observatory_reads_causal_evidence_without_writing_figures(tmp_path):
    runtime = Runtime.open(tmp_path / "epochgate.db")
    try:
        first = runtime.reserve("test-run", "note.txt")
        second = runtime.reserve("test-run", "note.txt")
        assert runtime.publish(first, b"old")["status"] == "withheld"
        assert runtime.publish(second, b"new")["published"]

        client = TestClient(create_app(runtime, tmp_path, "token"), headers={"Authorization": "Bearer token"})
        detail = client.get("/runs/test-run?figures=false").json()
        assert len(detail["attempts"]) == 2
        assert {row["attempt_id"] for row in detail["events"] if row["event_type"] == "attempt_opened"} == {first.attempt_id, second.attempt_id}
        assert detail["summary"]["rejected_submissions"] == 1
        assert detail["summary"]["confirmed"] == 1
        assert detail["summary"]["duplicate_publications"] == 0
        receipt, = detail["receipts"]
        assert receipt["source"] == "publish_readback"
        assert receipt["verification_event"] in receipt["event_seqs"]
        effect = next(row for row in detail["effects"] if row["receipt"] == receipt["receipt_id"])
        assert effect["attempt_id"] == second.attempt_id
        assert "effect_svg" not in detail
        assert not (tmp_path / "runs").exists()
    finally:
        runtime.close()


def test_cancellation_demo_observes_exit_before_slot_release(tmp_path):
    runtime = Runtime.open(tmp_path / "epochgate.db")
    try:
        client = TestClient(create_app(runtime, tmp_path, "token"), headers={"Authorization": "Bearer token"})
        started = client.post("/actions/cancellation").json()
        for _ in range(100):
            job = client.get(f"/jobs/{started['job_id']}").json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(.05)
        assert job["status"] == "succeeded", job
        detail = client.get(f"/runs/{job['run_id']}?figures=false").json()
        events = {event["event_type"]: event for event in detail["events"]}
        assert events["cancel_requested"]["seq"] < events["process_exited"]["seq"] < events["slot_release_observed"]["seq"]
        assert events["slot_release_observed"]["causation_id"] == events["process_exited"]["seq"]
        assert detail["slot_released"] is True
    finally:
        runtime.close()


def test_research_archive_has_trace_and_read_only_ledger(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    runtime = Runtime.open(tmp_path / "epochgate.db")
    try:
        client = TestClient(create_app(runtime, tmp_path, "token", repo=repo))
        base = "/observatory/research"
        auth = {"Authorization": "Bearer token"}
        assert client.get(base).status_code == 401
        data = client.get(base, headers=auth).json()
        assert data["report"]["cells"] == 27
        assert data["protocol_matches"]
        protocol = client.get(f"{base}/artifacts/protocol", headers=auth).content
        assert hashlib.sha256(protocol).hexdigest() == data["protocol_sha256"]
        assert data["integrity"]["missing"] == data["integrity"]["mismatched"] == []
        cell = "sqlite-inventory-check_commit_gap"
        detail = client.get(f"{base}/cells/{cell}", headers=auth).json()
        assert detail["row"]["historical_flags"]
        assert detail["trace"]["trace"]
        assert detail["ledger"]["target_effects"]
        assert client.get(f"{base}/artifacts/{cell}/target.sqlite", headers=auth).content.startswith(b"SQLite format 3")
        assert client.get(f"{base}/artifacts/unknown", headers=auth).status_code == 404
    finally:
        runtime.close()
