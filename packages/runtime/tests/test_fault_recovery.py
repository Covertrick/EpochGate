import subprocess
import sys
import time
from pathlib import Path

from epochgate_runtime.files import reconcile_file
from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.processes import pid_alive, reconcile_dead_workers, start_sleeper, stop_pid
from epochgate_runtime.recovery import reconcile
from epochgate_runtime.sink import EffectSink


def test_killed_transaction_does_not_keep_a_new_token(tmp_path: Path):
    database = tmp_path / "ledger.sqlite"
    ledger = RunLedger(database)
    ledger.create_run("crash")
    ledger.close()
    code = (
        "import sqlite3,sys,time; conn=sqlite3.connect(sys.argv[1]); conn.execute('BEGIN IMMEDIATE'); "
        "conn.execute(\"UPDATE runs SET fencing_token=99 WHERE run_id='crash'\"); "
        "print('ready', flush=True); time.sleep(30)"
    )
    process = subprocess.Popen([sys.executable, "-c", code, str(database)], stdout=subprocess.PIPE, text=True)
    assert process.stdout is not None
    deadline = time.time() + 5
    while time.time() < deadline and process.stdout.readline().strip() != "ready":
        continue
    process.kill()
    process.wait(timeout=5)
    fresh = RunLedger(database)
    assert fresh.get_run("crash").fencing_token == 1
    fresh.close()


def test_control_process_restart_keeps_the_run(tmp_path: Path):
    database = tmp_path / "ledger.sqlite"
    sink = tmp_path / "sink.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code = (
        "import sys; from pathlib import Path; "
        "from epochgate_runtime.ledger import RunLedger; "
        "from epochgate_runtime.service import ControlServer; "
        "from epochgate_runtime.sink import EffectSink; "
        "ledger=RunLedger(sys.argv[1]); server=ControlServer(ledger, EffectSink(sys.argv[2]), Path(sys.argv[3])); "
        "server.start(); print(server.base_url, flush=True); import time; time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(database), str(sink), str(workspace)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    base = process.stdout.readline().strip()
    import json
    from urllib.request import Request, urlopen
    request = Request(base + "/runs", data=json.dumps({"run_id": "kept", "session_id": "s"}).encode(), headers={"Content-Type": "application/json"})
    with urlopen(request) as response:
        created = json.loads(response.read().decode())
    process.kill()
    process.wait(timeout=5)
    fresh = RunLedger(database)
    state = fresh.get_run("kept")
    assert state.fencing_token == created["fencing_token"]
    assert state.phase == "running"
    fresh.close()


def test_dead_worker_releases_only_after_the_pid_is_gone():
    ledger = RunLedger()
    ledger.create_run("worker")
    process = start_sleeper(30)
    ledger.grant_lease("lease-live", "worker", "w", "tool_executor", process.pid)
    assert reconcile_dead_workers(ledger) == []
    assert ledger.held_leases()
    stop_pid(process.pid)
    process.wait(timeout=5)
    deadline = time.time() + 5
    while pid_alive(process.pid) and time.time() < deadline:
        time.sleep(0.05)
    released = reconcile_dead_workers(ledger)
    assert released == ["lease-live"]
    assert ledger.held_leases() == []
    again = reconcile_dead_workers(ledger)
    assert again == []
    ledger.close()


def test_repeated_file_and_sink_recovery_do_not_apply_twice(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "bug.py").write_text("gold", encoding="utf-8")
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    sink = EffectSink(tmp_path / "sink.sqlite")
    ledger.create_run("run")
    ledger.advance("run", "running")
    token = ledger.get_run("run").fencing_token
    digest = __import__("hashlib").sha256(b"gold").hexdigest()
    ledger.claim_publish("run", "file", token, digest)
    first = reconcile_file(ledger, workspace=workspace, run_id="run", idempotency_key="file", relative_path="bug.py")
    second = reconcile_file(ledger, workspace=workspace, run_id="run", idempotency_key="file", relative_path="bug.py")
    assert first["published"] is True
    assert second["status"] == "replayed"
    sink.accept("sink-key", expected_version=1, payload="x")
    ledger.mark_started_only("run", "sink-key", token)
    recovered = reconcile(ledger, sink, run_id="run", idempotency_key="sink-key", fencing_token=token)
    repeated = reconcile(ledger, sink, run_id="run", idempotency_key="sink-key", fencing_token=token)
    assert recovered["decision"] == "confirmed_from_sink"
    assert repeated["decision"] == "replay"
    assert sink.accepted_count("sink-key") == 1
    ledger.close()
    sink.close()
