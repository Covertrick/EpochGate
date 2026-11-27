import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from epochgate_runtime.api import Runtime
from epochgate_runtime.httpapp import create_app


def test_product_archive_and_launch_contract(tmp_path, monkeypatch):
    runtime = Runtime.open(tmp_path / "epochgate.db")
    repo = Path(__file__).resolve().parents[3]
    try:
        app = create_app(runtime, tmp_path, "token", repo=repo)
        client = TestClient(app, headers={"Authorization": "Bearer token"})
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
    finally:
        runtime.close()
