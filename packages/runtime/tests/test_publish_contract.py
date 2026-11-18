import hashlib
import multiprocessing
import os
from pathlib import Path

from epochgate_runtime.files import publish_file, reconcile_file, recover_claimed_publish
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


def test_commit_only_snapshot_does_not_fence_the_write(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "decision.txt"
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    sink = EffectSink(tmp_path / "sink.sqlite")
    ledger.create_run("run")
    ledger.advance("run", "running")
    admission = {"allowed": ledger.get_run("run").eligible}
    assert admission["allowed"] is True
    ledger.revoke_with_sink("run", sink)
    assert ledger.get_run("run").eligible is False
    target.write_text("permit ALLOW", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "permit ALLOW"
    ledger.close()
    sink.close()


def _claimed_workspace(tmp_path: Path, text: bytes = b"gold") -> tuple[Path, Path, RunLedger, str]:
    workspace = tmp_path / "workspace"
    staging_dir = workspace / ".eg-staging"
    staging_dir.mkdir(parents=True)
    staging = staging_dir / "k"
    staging.write_bytes(text)
    digest = hashlib.sha256(text).hexdigest()
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    ledger.create_run("run")
    ledger.advance("run", "running")
    token = ledger.get_run("run").fencing_token
    assert ledger.claim_publish("run", "k", token, digest)["status"] == "claimed"
    return workspace, staging, ledger, digest


def test_claimed_before_replace_recovers_the_authorized_publish(tmp_path: Path):
    workspace, staging, ledger, digest = _claimed_workspace(tmp_path)
    first = recover_claimed_publish(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        relative_path="order.txt",
        staging_path=staging,
    )
    second = recover_claimed_publish(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        relative_path="order.txt",
        staging_path=staging,
    )
    assert first["status"] == "completed"
    assert first["published"] is True
    assert first["protocol"] == "cooperative_publisher"
    assert (workspace / "order.txt").read_bytes() == b"gold"
    assert second["status"] == "replayed"
    assert ledger.effect_row("k")["result"] == digest
    ledger.close()


def test_receipt_lost_after_replace_restores_the_receipt_once(tmp_path: Path):
    workspace, staging, ledger, _digest = _claimed_workspace(tmp_path)
    os.replace(staging, workspace / "order.txt")
    first = recover_claimed_publish(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        relative_path="order.txt",
        staging_path=staging,
    )
    second = recover_claimed_publish(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        relative_path="order.txt",
        staging_path=staging,
    )
    assert first["status"] == "completed"
    assert first["replaced"] is False
    assert second["status"] == "replayed"
    assert (workspace / "order.txt").read_bytes() == b"gold"
    ledger.close()


def test_staging_unavailable_stays_unknown(tmp_path: Path):
    workspace, staging, ledger, _digest = _claimed_workspace(tmp_path)
    staging.unlink()
    decision = recover_claimed_publish(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        relative_path="order.txt",
        staging_path=staging,
    )
    assert decision["status"] == "unknown"
    assert decision["published"] is False
    assert not (workspace / "order.txt").exists()
    ledger.close()


def test_target_conflict_does_not_overwrite(tmp_path: Path):
    workspace, staging, ledger, _digest = _claimed_workspace(tmp_path)
    (workspace / "order.txt").write_bytes(b"other")
    decision = recover_claimed_publish(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        relative_path="order.txt",
        staging_path=staging,
    )
    assert decision["status"] == "conflict"
    assert (workspace / "order.txt").read_bytes() == b"other"
    ledger.close()


def test_revoke_after_claim_still_allows_recovery(tmp_path: Path):
    workspace, staging, ledger, _digest = _claimed_workspace(tmp_path)
    sink = EffectSink(tmp_path / "sink.sqlite")
    ledger.revoke_with_sink("run", sink)
    ledger.close()
    sink.close()
    fresh = RunLedger(tmp_path / "ledger.sqlite")
    decision = recover_claimed_publish(
        fresh,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        relative_path="order.txt",
        staging_path=staging,
    )
    assert decision["published"] is True
    assert (workspace / "order.txt").read_bytes() == b"gold"
    fresh.close()


def test_stranger_bytes_do_not_complete_a_claim(tmp_path: Path):
    workspace, staging, ledger, digest = _claimed_workspace(tmp_path)
    staging.write_bytes(b"other")
    wrong_run = recover_claimed_publish(
        ledger,
        workspace=workspace,
        run_id="other",
        idempotency_key="k",
        relative_path="order.txt",
        staging_path=staging,
    )
    mismatched = recover_claimed_publish(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        relative_path="order.txt",
        staging_path=staging,
    )
    assert wrong_run["status"] == "missing"
    assert mismatched["status"] == "unknown"
    assert not (workspace / "order.txt").exists()
    assert ledger.effect_row("k")["result"] == digest
    ledger.close()


def test_completed_claim_rejects_a_different_digest(tmp_path: Path):
    workspace, staging, ledger, digest = _claimed_workspace(tmp_path)
    token = ledger.get_run("run").fencing_token
    recovered = recover_claimed_publish(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        relative_path="order.txt",
        staging_path=staging,
    )
    assert recovered["published"] is True
    other = hashlib.sha256(b"other").hexdigest()
    claim = ledger.claim_publish("run", "k", token, other)
    complete = ledger.complete_claimed_publish("run", "k", other, f"file:order.txt:{other}", path="order.txt")
    assert claim["status"] == "conflict"
    assert complete["status"] == "conflict"
    assert ledger.effect_row("k")["result"] == digest
    assert ledger.effect_row("k")["receipt"] == recovered["receipt"]
    assert (workspace / "order.txt").read_bytes() == b"gold"
    ledger.close()


def test_open_claim_keeps_its_digest(tmp_path: Path):
    _workspace, _staging, ledger, digest = _claimed_workspace(tmp_path)
    token = ledger.get_run("run").fencing_token
    again = ledger.claim_publish("run", "k", token, digest)
    other = ledger.claim_publish("run", "k", token, hashlib.sha256(b"other").hexdigest())
    assert again["status"] == "claimed"
    assert other["status"] == "conflict"
    assert ledger.effect_row("k")["result"] == digest
    ledger.close()


def test_external_writer_is_outside_the_publisher_lock(tmp_path: Path):
    workspace, staging, ledger, _digest = _claimed_workspace(tmp_path)
    target = workspace / "order.txt"

    def intrude() -> None:
        target.write_bytes(b"intruder")

    decision = recover_claimed_publish(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        relative_path="order.txt",
        staging_path=staging,
        before_replace=intrude,
    )
    assert decision["external_file_overwritten"] is True
    assert decision["replace_is_unconditional"] is True
    assert target.read_bytes() == b"gold"
    ledger.close()


def test_two_cooperative_recoverers_publish_once(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    workspace, staging, ledger, _digest = _claimed_workspace(tmp_path)
    ledger.close()
    queue = context.Queue()
    first = context.Process(
        target=_recover_worker,
        args=(str(tmp_path / "ledger.sqlite"), str(workspace), str(staging), queue),
    )
    second = context.Process(
        target=_recover_worker,
        args=(str(tmp_path / "ledger.sqlite"), str(workspace), str(staging), queue),
    )
    first.start()
    second.start()
    first.join(20)
    second.join(20)
    assert first.exitcode == 0
    assert second.exitcode == 0
    results = [queue.get(timeout=5), queue.get(timeout=5)]
    assert sorted(item["status"] for item in results) == ["completed", "replayed"]
    assert (workspace / "order.txt").read_bytes() == b"gold"


def test_unaligned_sink_rejects_until_the_mirror_catches_up(tmp_path: Path):
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    sink = EffectSink(tmp_path / "sink.sqlite")
    ledger.create_run("run")
    ledger.advance("run", "running")
    original = ledger.get_run("run")
    ledger.supersede("run")
    fresh = ledger.get_run("run")
    stale = ledger.commit_authorized(
        "run",
        idempotency_key="old",
        fencing_token=original.fencing_token,
        state_version=original.state_version,
        sink=sink,
        payload="old",
    )
    lagged = ledger.commit_authorized(
        "run",
        idempotency_key="new",
        fencing_token=fresh.fencing_token,
        state_version=fresh.state_version,
        sink=sink,
        payload="new",
    )
    assert stale["status"] == "rejected"
    assert lagged["status"] == "rejected"
    assert lagged.get("reason") == "version"
    sink.advance_to(fresh.state_version)
    successor = ledger.commit_authorized(
        "run",
        idempotency_key="new",
        fencing_token=fresh.fencing_token,
        state_version=fresh.state_version,
        sink=sink,
        payload="new",
    )
    assert successor["status"] == "completed"
    assert sink.accepted_count("new") == 1
    ledger.close()
    sink.close()


def _recover_worker(database: str, workspace: str, staging: str, queue) -> None:
    ledger = RunLedger(database)
    try:
        result = recover_claimed_publish(
            ledger,
            workspace=Path(workspace),
            run_id="run",
            idempotency_key="k",
            relative_path="order.txt",
            staging_path=Path(staging),
        )
        queue.put({"status": result["status"], "published": result["published"]})
    except Exception as exc:  # noqa: BLE001
        queue.put({"status": type(exc).__name__, "published": False})
    finally:
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
