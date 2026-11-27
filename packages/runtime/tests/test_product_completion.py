import io
import json
import sqlite3
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from epochgate_runtime.api import Runtime
from epochgate_runtime.httpapp import create_app
from epochgate_runtime import sandbox


def test_docker_override_is_used_for_health_and_execution(monkeypatch):
    probes = []
    monkeypatch.setattr(sandbox, "_DOCKER_PREFIX", ["previous-docker"])
    monkeypatch.setenv("EPOCHGATE_DOCKER_BIN", "configured-docker")

    def linux_engine(prefix):
        probes.append(prefix)
        return True

    monkeypatch.setattr(sandbox, "_linux_engine", linux_engine)
    assert sandbox.docker_available()
    assert probes == [["configured-docker"]]
    assert sandbox.docker_command(["ps"]) == ["configured-docker", "ps"]


def test_product_archive_and_launch_contract(tmp_path, monkeypatch):
    runtime = Runtime.open(tmp_path / "epochgate.db")
    repo = Path(__file__).resolve().parents[3]
    try:
        app = create_app(runtime, tmp_path, "token", repo=repo)
        client = TestClient(app, headers={"Authorization": "Bearer token"})
        assert client.get("/health").json()["mode"] == "local"
        for path in ("/", "/app.js", "/app.css"):
            assert client.get(path).headers["Cache-Control"] == "no-cache"
        calls = []

        def create(*args, **kwargs):
            calls.append(args)
            return {"run_id": "test", "session_id": "test", "job_id": "job", "events": [], "effects": []}

        monkeypatch.setattr(app.state.sessions, "create", create)
        monkeypatch.setattr("epochgate_runtime.httpapp._with_figures", lambda _, view: view)
        headers = {"Idempotency-Key": "test-launch"}
        body = {"workspace": str(tmp_path), "task": "read"}
        first = client.post("/sessions", json=body, headers=headers)
        second = client.post("/sessions", json=body, headers=headers)
        assert first.status_code == second.status_code == 200
        assert len(calls) == 1
        assert client.post("/sessions", json={**body, "task": "changed"}, headers=headers).status_code == 409
        assert client.get("/observatory/launch/test-launch").json()["run_id"] == "test"
        demo = TestClient(create_app(runtime, tmp_path, "token", demo_mode=True), headers={"Authorization": "Bearer token"})
        assert demo.post("/sessions", json=body).status_code == 409

        base = "/observatory/research"
        cell_id = "sqlite-inventory-check_commit_gap"
        cell = client.get(f"{base}/cells/{cell_id}").json()
        violation, = [item for item in cell["timeline"] if item["violation"]]
        assert violation["target_record"]["effect_key"] == "old"
        assert violation["receipt"]["receipt"] == violation["target_record"]["receipt_id"]
        bundle = client.get(f"{base}/export/zip?cell_id={cell_id}")
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            assert json.loads(archive.read("metadata.json"))["protocol_sha256"]
            assert f"{cell_id}/target.sqlite" in archive.namelist()
        assert client.get(f"{base}/export/csv").content.count(b"sqlite-inventory-check_commit_gap") == 1
        assert client.post(f"{base}/audit").json()["status"] == "VERIFIED"
        assert client.get(f"{base}/audits").json()["audits"][0]["report_sha256"]

        job_id = client.post("/actions/unknown-outcome").json()["job_id"]
        for _ in range(50):
            job = client.get(f"/jobs/{job_id}").json()
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(.02)
        assert job["status"] == "succeeded"
        run_id = job["run_id"]
        for _ in range(2):
            detail = client.get(f"/runs/{run_id}?figures=false").json()
            assert detail["summary"]["unknown_effects"] == 1
            assert detail["receipts"] == []
        assert any(match["id"] == run_id for match in client.get(f"/observatory/search?q={run_id}").json()["matches"])
    finally:
        runtime.close()


def test_unavailable_sink_does_not_verify_a_mismatched_receipt(tmp_path, monkeypatch):
    runtime = Runtime.open(tmp_path / "epochgate.db")
    try:
        ledger = runtime.ledger
        ledger.create_run("receipt-run")
        state = ledger.advance("receipt-run", "running")
        runtime.sink.accept("key", expected_version=1, payload="target")
        ledger.store_receipt("receipt-run", "key", state.fencing_token, "incorrect-receipt")
        client = TestClient(create_app(runtime, tmp_path, "token"), headers={"Authorization": "Bearer token"})

        path = "/runs/receipt-run?figures=false"
        assert client.get(path).json()["receipts"][0]["status"] == "MISMATCH"

        def unavailable(_key):
            raise sqlite3.OperationalError("closed")

        monkeypatch.setattr(runtime.sink, "get", unavailable)
        result = client.get(path).json()
        assert result["receipts"][0]["status"] == "NOT_AVAILABLE"
        assert result["summary"]["confirmed"] == 0
    finally:
        runtime.close()
