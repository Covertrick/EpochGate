import hashlib
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epochgate_runtime.agent_loop import run_agent_turn
from epochgate_runtime.api import Runtime
from epochgate_runtime.demo import prepare_dirs
from epochgate_runtime.httpapp import _stream, create_app
from epochgate_runtime.observations import observe_run
from epochgate_runtime.plugin_patch import find_repo, render_plugin_patch
from epochgate_runtime.sandbox import docker_available


def _app(tmp_path: Path):
    prepare_dirs(tmp_path)
    runtime = Runtime.open(tmp_path / "epochgate.db", data_dir=tmp_path)
    app = create_app(runtime, tmp_path, "test-token", repo=find_repo(), control_url="http://127.0.0.1:9")
    return runtime, app


def _client(app) -> TestClient:
    return TestClient(app)


def _auth(token: str = "test-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _wait(client: TestClient, job_id: str, rounds: int = 80) -> dict:
    for _ in range(rounds):
        response = client.get(f"/jobs/{job_id}", headers=_auth())
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_health_is_open_and_writes_need_a_token(tmp_path: Path):
    runtime, app = _app(tmp_path)
    client = _client(app)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["authority"] == "run_ledger"
    assert health.headers["X-Request-Id"]
    denied = client.post("/sessions", json={"workspace": str(tmp_path), "task": "read"})
    assert denied.status_code == 401
    assert denied.json()["code"] == "unauthorized"
    runtime.close()


def test_demo_names_count_in_order(tmp_path: Path):
    runtime, app = _app(tmp_path)
    client = _client(app)
    first = _wait(client, client.post("/actions/stale-write", headers=_auth()).json()["job_id"])
    second = _wait(client, client.post("/actions/idempotency-conflict", headers=_auth()).json()["job_id"])
    assert first["run_id"] == "token-01"
    assert second["run_id"] == "key-01"
    listed = client.get("/runs", headers=_auth()).json()["runs"]
    assert listed[0]["run_id"] == "key-01"
    assert listed[0]["title"] == "同一把钥匙 1"
    assert listed[1]["title"] == "旧令牌 1"
    runtime.close()


def test_clear_removes_saved_runs(tmp_path: Path):
    runtime, app = _app(tmp_path)
    client = _client(app)
    finished = _wait(client, client.post("/actions/stale-write", headers=_auth()).json()["job_id"])
    assert finished["run_id"] == "token-01"
    cleared = client.post("/runs/clear", headers=_auth())
    assert cleared.status_code == 200
    assert cleared.json()["cleared"] == 1
    assert client.get("/runs", headers=_auth()).json()["runs"] == []
    runtime.close()


def test_examples_offer_a_real_workspace(tmp_path: Path):
    runtime, app = _app(tmp_path)
    client = _client(app)
    response = client.get("/examples", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["tasks"]
    sample = body["tasks"][0]
    assert Path(sample["workspace"], "note.py").is_file()
    assert "greet" in sample["task"]
    assert sample["test_command"]
    assert "def greet" in "".join(item["text"] for item in sample["files"])
    assert "missing" in body
    runtime.close()


def test_running_docker_is_not_started_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime, app = _app(tmp_path)
    monkeypatch.setattr("epochgate_runtime.sessions.docker_available", lambda: False)
    monkeypatch.setattr("epochgate_runtime.httpapp.docker_reason", lambda: "denied")
    client = _client(app)
    body = client.get("/examples", headers=_auth()).json()
    assert body["docker"] == "denied"
    assert "已经在运行" in body["message"]
    assert "没有再次启动" in body["message"]
    runtime.close()


def test_missing_requirements_do_not_create_a_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime, app = _app(tmp_path)
    monkeypatch.setattr("epochgate_runtime.sessions.missing_requirements", lambda repo: ["api_key"])
    client = _client(app)
    workspace = tmp_path / "tree"
    workspace.mkdir()
    response = client.post("/sessions", headers=_auth(), json={"workspace": str(workspace), "task": "read the tree"})
    assert response.status_code == 409
    assert response.json()["code"] == "requirements"
    assert runtime.ledger.list_runs() == []
    runtime.close()


def test_stale_write_keeps_the_old_token_out(tmp_path: Path):
    runtime, app = _app(tmp_path)
    client = _client(app)
    started = client.post("/actions/stale-write", headers=_auth())
    assert started.status_code == 200
    finished = _wait(client, started.json()["job_id"])
    assert finished["status"] == "succeeded"
    run_id = finished["run_id"]
    detail = client.get(f"/runs/{run_id}", headers=_auth())
    names = [item["event_type"] for item in detail.json()["events"]]
    assert "result_withheld" in names or "commit_rejected" in names
    assert detail.json()["observations"]["container_publish"]["status"] == "absent"
    target = tmp_path / "workspace" / run_id / "main.py"
    assert target.read_bytes() == b"new-bytes"
    runtime.close()


def test_idempotency_conflict_keeps_the_first_bytes(tmp_path: Path):
    runtime, app = _app(tmp_path)
    client = _client(app)
    started = client.post("/actions/idempotency-conflict", headers=_auth())
    finished = _wait(client, started.json()["job_id"])
    assert finished["status"] == "succeeded"
    run_id = finished["run_id"]
    detail = client.get(f"/runs/{run_id}", headers=_auth())
    assert detail.json()["observations"]["idempotency"]["status"] == "seen"
    assert detail.json()["observations"]["idempotency"]["unchanged"] is True
    assert (tmp_path / "workspace" / run_id / "main.py").read_bytes() == b"kept-bytes"
    runtime.close()


def test_corrected_effect_keeps_the_early_receipt(tmp_path: Path):
    runtime, app = _app(tmp_path)
    client = _client(app)
    started = client.post("/actions/corrected-effect", headers=_auth())
    finished = _wait(client, started.json()["job_id"])
    run_id = finished["run_id"]
    detail = client.get(f"/runs/{run_id}", headers=_auth()).json()
    assert (tmp_path / "workspace" / run_id / "main.py").read_bytes() == b"return 1\n"
    receipts = [item["receipt"] for item in detail["effects"] if item["receipt"]]
    assert len(receipts) >= 2
    runtime.close()


def test_plugin_patch_uses_this_checkout():
    repo = find_repo(Path(__file__))
    assert repo is not None
    text = render_plugin_patch(repo)
    assert "D:/Workspace/EpochGate" not in text
    for name in ("eg-workspace", "eg-policy", "eg-observability"):
        assert (repo / "packages" / name / "lib" / "index.js").resolve().as_uri() in text


def test_agent_turn_sends_the_task_and_control_env(tmp_path: Path):
    fake = tmp_path / "fake_dsh.py"
    prompt_path = tmp_path / "prompt.txt"
    env_path = tmp_path / "env.txt"
    fake.write_text(
        "import json, os, sys\n"
        f"sys.stdout.reconfigure(line_buffering=True)\n"
        f"open(r'{env_path}', 'w', encoding='utf-8').write(os.environ['EG_CONTROL_URL'] + ' ' + os.environ['EG_REQUIRE_CONTROL'])\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if message.get('method') == 'session/prompt':\n"
        f"        open(r'{prompt_path}', 'w', encoding='utf-8').write(message['params']['contentBlocks'][0]['text'])\n"
        "    sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{}}) + '\\n')\n"
        "    sys.stdout.flush()\n",
        encoding="utf-8",
    )
    repo = find_repo(Path(__file__))
    assert repo is not None
    result = run_agent_turn(
        repo=repo,
        patch=tmp_path / "patch.yml",
        control_url="http://127.0.0.1:8765",
        run_id="sess-test",
        container="eg-test",
        task="只读仓库里的 README",
        token="secret-token",
        command=[sys.executable, str(fake)],
        timeout=5,
    )
    assert result["prompt"].startswith("只读仓库里的 README")
    assert env_path.read_text(encoding="utf-8") == "http://127.0.0.1:8765 1"


def test_ledger_survives_reopen(tmp_path: Path):
    runtime, app = _app(tmp_path)
    client = _client(app)
    started = client.post("/actions/stale-write", headers=_auth())
    finished = _wait(client, started.json()["job_id"])
    run_id = finished["run_id"]
    runtime.close()
    reopened = Runtime.open(tmp_path / "epochgate.db", data_dir=tmp_path)
    again = create_app(reopened, tmp_path, "next-token", repo=find_repo(), control_url="http://127.0.0.1:9")
    client = _client(again)
    listed = client.get("/runs", headers=_auth("next-token"))
    assert run_id in [item["run_id"] for item in listed.json()["runs"]]
    reopened.close()


@pytest.mark.skipif(not docker_available(), reason="docker is not available")
def test_walkthrough_shows_five_observations(tmp_path: Path):
    runtime, app = _app(tmp_path)
    client = _client(app)
    started = client.post("/actions/walkthrough", headers=_auth())
    assert started.status_code == 200
    finished = _wait_long(client, started.json()["job_id"])
    assert finished["status"] == "succeeded", finished
    detail = client.get(f"/runs/{finished['run_id']}", headers=_auth()).json()
    observations = detail["observations"]
    assert observations["fencing"]["status"] == "seen"
    assert observations["idempotency"]["unchanged"] is True
    assert observations["reconcile"] == {"status": "seen", "first": "confirmed_from_sink", "second": "replay"}
    assert observations["slot"]["released_after_exit"] is True
    assert observations["container_publish"]["receipt_verified"] is True
    runtime.close()


def test_action_without_a_token_is_rejected(tmp_path: Path):
    runtime, app = _app(tmp_path)
    client = _client(app)
    denied = client.post("/actions/stale-write")
    assert denied.status_code == 401
    assert denied.json()["code"] == "unauthorized"
    runtime.close()


def test_crash_window_reconciles_then_replays(tmp_path: Path):
    runtime, app = _app(tmp_path)
    client = _client(app)
    started = client.post("/actions/crash-window", headers=_auth())
    assert started.status_code == 200
    finished = _wait(client, started.json()["job_id"], rounds=200)
    assert finished["status"] == "succeeded", finished
    detail = client.get(f"/runs/{finished['run_id']}", headers=_auth()).json()
    assert detail["observations"]["reconcile"] == {"status": "seen", "first": "confirmed_from_sink", "second": "replay"}
    assert "旧令牌" in detail["effect_svg"] or "这一次还没有" in detail["effect_svg"]
    assert "<ol>" not in detail["table_html"]
    assert (tmp_path / "runs" / finished["run_id"] / "effect.svg").is_file()
    assert "序号" in (tmp_path / "runs" / finished["run_id"] / "table.html").read_text(encoding="utf-8")
    runtime.close()


def test_stale_write_figure_uses_the_ledger_sequence(tmp_path: Path):
    runtime, app = _app(tmp_path)
    client = _client(app)
    started = client.post("/actions/stale-write", headers=_auth())
    finished = _wait(client, started.json()["job_id"])
    run_id = finished["run_id"]
    detail = client.get(f"/runs/{run_id}", headers=_auth()).json()
    assert "资格" in detail["effect_svg"]
    assert "<ol>" not in detail["table_html"]
    report = (tmp_path / "runs" / run_id / "report.html").read_text(encoding="utf-8")
    assert "<ol>" not in report
    assert "资格" in (tmp_path / "runs" / run_id / "effect.svg").read_text(encoding="utf-8")
    runtime.close()


def test_event_stream_does_not_repeat_seen_sequences(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime, app = _app(tmp_path)
    client = _client(app)
    started = client.post("/actions/stale-write", headers=_auth())
    finished = _wait(client, started.json()["job_id"])
    run_id = finished["run_id"]

    class Slept(Exception):
        pass

    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: (_ for _ in ()).throw(Slept()))

    def collect(after: int) -> list[int]:
        found: list[int] = []
        try:
            for chunk in _stream(runtime, run_id, after):
                for line in chunk.splitlines():
                    if line.startswith("id:"):
                        found.append(int(line.split(":", 1)[1].strip()))
        except Slept:
            pass
        return found

    first = collect(0)
    assert first
    assert first == sorted(set(first))
    assert collect(first[-1]) == []
    runtime.close()


def test_same_path_different_bytes_conflicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime, app = _app(tmp_path)
    stored: dict[str, bytes] = {}

    def stage(_container: str, _path: str, payload: bytes) -> None:
        stored["staged"] = payload

    def read(_name: str, _path: str) -> bytes:
        return stored["staged"]

    def write(_name: str, _relative: str, payload: bytes) -> bool:
        stored["written"] = payload
        return True

    def digest(_name: str, _path: str) -> str:
        return hashlib.sha256(stored["written"]).hexdigest()

    monkeypatch.setattr("epochgate_runtime.sessions._stage", stage)
    monkeypatch.setattr("epochgate_runtime.sandbox.read_staging_bytes", read)
    monkeypatch.setattr("epochgate_runtime.sandbox.write_published_bytes", write)
    monkeypatch.setattr("epochgate_runtime.sandbox.sha256_file", digest)
    session_id = "sess-pub"
    runtime.ledger.create_run(session_id)
    runtime.ledger.advance(session_id, "running")
    app.state.sessions._write({
        "session_id": session_id,
        "run_id": session_id,
        "container": "eg-pub",
        "task": "publish",
        "workspace": str(tmp_path),
        "baseline": {},
    })
    first = app.state.sessions.publish(session_id, "main.py", "kept")
    second = app.state.sessions.publish(session_id, "main.py", "other")
    assert first.get("published") is True
    assert second["status"] == "conflict"
    assert stored["written"] == b"kept"
    assert any(item["event_type"] == "publish_outcome" for item in runtime.ledger.events(session_id))
    runtime.close()


def test_cancel_releases_the_slot_only_after_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime, app = _app(tmp_path)
    monkeypatch.setattr("epochgate_runtime.sessions.stop_container", lambda _name: None)
    session_id = "sess-cancel"
    runtime.ledger.create_run(session_id)
    runtime.ledger.advance(session_id, "running")
    app.state.sessions._write({
        "session_id": session_id,
        "run_id": session_id,
        "container": "eg-cancel",
        "task": "sleep",
        "workspace": str(tmp_path),
        "baseline": {},
    })
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    app.state.sessions.jobs.attach_process(session_id, process)
    try:
        result = app.state.sessions.cancel(session_id)
    finally:
        if process.poll() is None:
            process.kill()
    assert result["slot_released"] is True
    events = runtime.ledger.events(session_id)
    cancel = next(item for item in events if item["event_type"] == "cancel_requested")
    exited = next(item for item in events if item["event_type"] == "process_exited")
    assert exited["seq"] > cancel["seq"]
    assert observe_run(runtime.ledger, session_id)["slot"]["released_after_exit"] is True
    runtime.close()


def test_session_returns_the_agent_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime, app = _app(tmp_path)
    monkeypatch.setattr("epochgate_runtime.sessions.missing_requirements", lambda _repo: [])
    monkeypatch.setattr("epochgate_runtime.sessions.start_container", lambda _name: None)
    monkeypatch.setattr("epochgate_runtime.sessions.write_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("epochgate_runtime.sessions.lock_workspace", lambda _name: None)
    monkeypatch.setattr("epochgate_runtime.sessions.stop_container", lambda _name: None)

    def failed_turn(**kwargs):
        return {
            "run_id": kwargs["run_id"],
            "initialize_error": {"message": "no model"},
            "prompt_error": None,
            "wait_error": None,
        }

    monkeypatch.setattr("epochgate_runtime.sessions.run_agent_turn", failed_turn)
    workspace = tmp_path / "tree"
    workspace.mkdir()
    client = _client(app)
    response = client.post("/sessions", headers=_auth(), json={"workspace": str(workspace), "task": "read"})
    assert response.status_code == 200
    assert response.json()["job_id"]
    finished = _wait(client, response.json()["job_id"])
    assert finished["status"] == "failed"
    assert "初始化" in finished["error"]
    runtime.close()


def test_session_setup_failure_stops_the_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime, app = _app(tmp_path)
    monkeypatch.setattr("epochgate_runtime.sessions.missing_requirements", lambda _repo: [])
    monkeypatch.setattr("epochgate_runtime.sessions.start_container", lambda _name: None)
    monkeypatch.setattr("epochgate_runtime.sessions.write_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("epochgate_runtime.sessions.lock_workspace", lambda _name: None)
    stopped: list[str] = []
    monkeypatch.setattr("epochgate_runtime.sessions.stop_container", lambda name: stopped.append(name))

    def boom(*_args, **_kwargs):
        raise RuntimeError("patch failed")

    monkeypatch.setattr("epochgate_runtime.sessions.write_plugin_patch", boom)
    workspace = tmp_path / "tree"
    workspace.mkdir()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/sessions", headers=_auth(), json={"workspace": str(workspace), "task": "read"})
    assert response.status_code == 500
    assert stopped
    runtime.close()


def _wait_long(client: TestClient, job_id: str) -> dict:
    for _ in range(180):
        response = client.get(f"/jobs/{job_id}", headers=_auth())
        body = response.json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(1)
    raise AssertionError("walkthrough did not finish")
