import hashlib
import multiprocessing
import os
from pathlib import Path

from epochgate_runtime.files import publish_file, reconcile_file
from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.sink import EffectSink


def test_crash_after_move_reconciles_the_same_digest_once(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    staging = workspace / "stage.txt"
    staging.write_text("gold", encoding="utf-8")
    digest = hashlib.sha256(b"gold").hexdigest()
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    ledger.create_run("run")
    ledger.advance("run", "running")
    token = ledger.get_run("run").fencing_token
    assert ledger.claim_publish("run", "k", token, digest)["claimed"] is True
    os.replace(staging, workspace / "bug.py")
    ledger.close()

    fresh = RunLedger(tmp_path / "ledger.sqlite")
    first = reconcile_file(fresh, workspace=workspace, run_id="run", idempotency_key="k", relative_path="bug.py")
    second = reconcile_file(fresh, workspace=workspace, run_id="run", idempotency_key="k", relative_path="bug.py")
    assert first["published"] is True
    assert first["sha256"] == digest
    assert second["status"] == "replayed"
    assert (workspace / "bug.py").read_text(encoding="utf-8") == "gold"
    published = [item for item in fresh.events("run") if item["event_type"] == "file_published"]
    assert published[0]["payload"]["receipt_verified"] is True
    fresh.close()


def test_changed_file_after_claim_stays_unknown(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "bug.py").write_text("other", encoding="utf-8")
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    ledger.create_run("run")
    ledger.advance("run", "running")
    token = ledger.get_run("run").fencing_token
    ledger.claim_publish("run", "k", token, hashlib.sha256(b"gold").hexdigest())
    decision = reconcile_file(fresh := ledger, workspace=workspace, run_id="run", idempotency_key="k", relative_path="bug.py")
    assert decision["published"] is False
    assert decision["status"] == "unknown"
    fresh.close()


def test_publisher_rejects_a_path_the_client_named(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    staging = workspace / "stage.txt"
    staging.write_text("x", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    ledger = RunLedger()
    ledger.create_run("run")
    ledger.advance("run", "running")
    token = ledger.get_run("run").fencing_token
    escaped = publish_file(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        fencing_token=token,
        relative_path="../outside.txt",
        staging_path=staging,
    )
    assert escaped["published"] is False
    assert outside.read_text(encoding="utf-8") == "secret"
    ledger.close()


def _commit_worker(database: str, sink_database: str, token: int, version: int, queue) -> None:
    ledger = RunLedger(database)
    sink = EffectSink(sink_database)
    result = ledger.commit_authorized(
        "race",
        idempotency_key="k",
        fencing_token=token,
        state_version=version,
        sink=sink,
        payload="x",
    )
    queue.put({"op": "commit", "status": result["status"]})
    ledger.close()
    sink.close()


def _revoke_worker(database: str, sink_database: str, queue) -> None:
    ledger = RunLedger(database)
    sink = EffectSink(sink_database)
    ledger.revoke_with_sink("race", sink)
    queue.put({"op": "revoke"})
    ledger.close()
    sink.close()


def test_two_processes_serialize_commit_and_revoke(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    for index in range(4):
        database = tmp_path / f"ledger-{index}.sqlite"
        sink_database = tmp_path / f"sink-{index}.sqlite"
        ledger = RunLedger(database)
        ledger.create_run("race")
        ledger.advance("race", "running")
        state = ledger.get_run("race")
        ledger.close()
        queue = context.Queue()
        commit = context.Process(target=_commit_worker, args=(str(database), str(sink_database), state.fencing_token, state.state_version, queue))
        revoke = context.Process(target=_revoke_worker, args=(str(database), str(sink_database), queue))
        commit.start()
        revoke.start()
        commit.join(20)
        revoke.join(20)
        assert commit.exitcode == 0
        assert revoke.exitcode == 0
        fresh = RunLedger(database)
        sink = EffectSink(sink_database)
        accepts = sink.accepted_count("k")
        row = fresh.effect_row("k")
        assert accepts in {0, 1}
        if accepts == 1:
            assert row is not None and row["phase"] == "completed"
        else:
            assert row is None or row["phase"] != "completed"
        assert fresh.get_run("race").eligible is False
        fresh.close()
        sink.close()
