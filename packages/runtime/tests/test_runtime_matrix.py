import subprocess
import sys
import threading
from pathlib import Path

from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.f2p import score_local_discriminator
from epochgate_runtime.files import publish_file
from epochgate_runtime.processes import cancel_process, pid_alive, start_parent_and_child, start_sleeper, stop_pid, wait_for_pid_file
from epochgate_runtime.recovery import reconcile
from epochgate_runtime.scheduler import ProcessScheduler
from epochgate_runtime.service import ControlServer
from epochgate_runtime.sink import EffectSink


def _running(ledger: RunLedger, run_id: str = "run") -> tuple[int, int]:
    ledger.create_run(run_id)
    ledger.advance(run_id, "running")
    state = ledger.get_run(run_id)
    return state.fencing_token, state.state_version


def test_sink_rejects_stale_token_without_accept():
    ledger = RunLedger()
    sink = EffectSink()
    token, version = _running(ledger)
    ledger.revoke_with_sink("run", sink)
    result = ledger.commit_authorized("run", idempotency_key="k", fencing_token=token, state_version=version, sink=sink, payload="x")
    assert result["status"] == "rejected"
    assert sink.accepted_count("k") == 0
    ledger.close()
    sink.close()


def test_duplicate_commit_hits_sink_once():
    ledger = RunLedger()
    sink = EffectSink()
    token, version = _running(ledger)
    first = ledger.commit_authorized("run", idempotency_key="k", fencing_token=token, state_version=version, sink=sink, payload="x")
    second = ledger.commit_authorized("run", idempotency_key="k", fencing_token=token, state_version=version, sink=sink, payload="x")
    assert first["sink_accepted"] is True
    assert second["status"] == "replayed"
    assert sink.accepted_count("k") == 1
    ledger.close()
    sink.close()


def test_revoke_and_commit_have_one_order():
    ledger = RunLedger()
    sink = EffectSink()
    token, version = _running(ledger)
    outcome: dict[str, str] = {}

    def commit() -> None:
        result = ledger.commit_authorized("run", idempotency_key="k", fencing_token=token, state_version=version, sink=sink, payload="x")
        outcome["status"] = result["status"]

    left = threading.Thread(target=commit)
    right = threading.Thread(target=lambda: ledger.revoke_with_sink("run", sink))
    left.start()
    right.start()
    left.join()
    right.join()
    accepted = sink.accepted_count("k")
    assert accepted in {0, 1}
    assert (outcome["status"] == "completed") is (accepted == 1)
    ledger.close()
    sink.close()


def test_process_crash_points_do_not_repeat_sink(tmp_path: Path):
    for fault, decision in (
        ("before_dispatch", "not_applied"),
        ("before_sink", "not_applied"),
        ("after_sink", "confirmed_from_sink"),
        ("after_receipt", "replay"),
    ):
        db = tmp_path / f"{fault}.sqlite"
        sink_path = tmp_path / f"{fault}-sink.sqlite"
        ledger = RunLedger(db)
        token, version = _running(ledger, "run")
        ledger.close()
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "epochgate_runtime.worker",
                "--db",
                str(db),
                "--sink",
                str(sink_path),
                "--fault",
                fault,
                "--run",
                "run",
                "--key",
                "k",
                "--token",
                str(token),
                "--version",
                str(version),
            ],
            check=False,
        )
        assert completed.returncode == 86
        recovered_ledger = RunLedger(db)
        recovered_sink = EffectSink(sink_path)
        result = reconcile(recovered_ledger, recovered_sink, run_id="run", idempotency_key="k", fencing_token=token)
        assert result["decision"] == decision
        assert result["retried"] is False
        assert recovered_sink.accepted_count("k") in {0, 1}
        if decision in {"confirmed_from_sink", "replay"}:
            assert recovered_sink.accepted_count("k") == 1
        recovered_ledger.close()
        recovered_sink.close()


def test_cancel_waits_for_process_exit():
    ledger = RunLedger()
    ledger.create_run("run")
    ledger.advance("run", "running")
    process = start_sleeper()
    report = cancel_process(ledger, "run", process)
    assert report["exited"] is True
    assert report["slot_released"] is True
    assert report["gpu_reclaimed"] is False
    assert report["resource_observation"] == "not_observable"
    names = [item["event_type"] for item in ledger.events("run")]
    assert "cancel_requested" in names
    assert "stop_signal_sent" in names
    assert "process_exited" in names
    ledger.close()


def test_grandchild_keeps_slot_until_it_exits(tmp_path: Path):
    ledger = RunLedger()
    ledger.create_run("run")
    ledger.advance("run", "running")
    pid_file = tmp_path / "child.pid"
    parent = start_parent_and_child(pid_file)
    child_pid = wait_for_pid_file(pid_file)
    parent.kill()
    parent.wait(timeout=5)
    assert pid_alive(child_pid) is True
    ledger.revoke("run")
    ledger.append_event("run", "process_exited", {"pid": parent.pid, "role": "parent"})
    ledger.observe_slot("run", free=False)
    assert ledger.get_run("run").cancel.slot_released is False
    stop_pid(child_pid)
    assert pid_alive(child_pid) is False
    ledger.append_event("run", "process_exited", {"pid": child_pid, "role": "child"})
    ledger.close()


def test_scheduler_limits_real_processes():
    ledger = RunLedger()
    for index in range(4):
        ledger.create_run(f"r{index}")
    scheduler = ProcessScheduler(ledger, capacity=2, queue_limit=1)
    statuses = [scheduler.submit(f"r{index}", sleep_s=0.2) for index in range(4)]
    assert statuses.count("rejected") == 1
    scheduler.wait()
    summary = scheduler.summary()
    assert summary["max_active_processes"] <= 2
    assert summary["max_active_processes"] >= 1
    assert summary["workload"] == "real_process"
    assert ledger.held_leases("tool_executor") == []
    ledger.close()


def test_publish_file_respects_revocation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    staging = workspace / "staging.txt"
    staging.write_text("hello", encoding="utf-8")
    ledger = RunLedger()
    token, _version = _running(ledger)
    ledger.record_intent("run", "k", token, _version, effect_id="e", arguments_hash="a")
    ledger.revoke("run")
    result = publish_file(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        fencing_token=token,
        relative_path="out.txt",
        staging_path=staging,
    )
    assert result["published"] is False
    assert not (workspace / "out.txt").exists()
    ledger.close()


def test_control_service_is_the_admission_path(tmp_path: Path):
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    sink = EffectSink(tmp_path / "sink.sqlite")
    server = ControlServer(ledger, sink, tmp_path)
    server.start()
    try:
        import json
        from urllib.request import Request, urlopen

        def post(path: str, body: dict) -> dict:
            request = Request(
                server.base_url + path,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request) as response:
                return json.loads(response.read().decode("utf-8"))

        created = post("/runs", {"run_id": "live", "session_id": "session-1"})
        reserved = post("/effects/reserve", {
            "run_id": "live",
            "idempotency_key": "tool-1",
            "fencing_token": created["fencing_token"],
            "state_version": created["state_version"],
            "effect_id": "effect-1",
            "arguments_hash": "abc",
        })
        assert reserved["allowed"] is True
        committed = post("/effects/commit", {
            "run_id": "live",
            "idempotency_key": "tool-1b",
            "fencing_token": created["fencing_token"],
            "state_version": created["state_version"],
            "payload": "write",
        })
        assert committed["sink_accepted"] is True
        post("/runs/live/revoke", {})
        late = post("/effects/commit", {
            "run_id": "live",
            "idempotency_key": "tool-2",
            "fencing_token": created["fencing_token"],
            "state_version": created["state_version"],
            "payload": "late",
        })
        assert late["status"] == "rejected"
        assert sink.accepted_count("tool-2") == 0
    finally:
        server.stop()
        ledger.close()
        sink.close()


def test_local_discriminator_separates_gold_and_probe(tmp_path: Path):
    report = score_local_discriminator(tmp_path)
    assert report["discriminating"] is True
    assert report["probe_marked_repaired"] is False
