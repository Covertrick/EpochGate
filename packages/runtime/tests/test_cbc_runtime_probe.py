"""在 epochgate-runtime 0.1.0 上记录 T1–T4 的现有行为。

不新增协议，不改账本实现。断言写的是这次测量到的行为。
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import time
import urllib.request
from pathlib import Path

import os

import epochgate_runtime.files as files_mod
from epochgate_runtime.files import publish_file, reconcile_file
from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.recovery import reconcile
from epochgate_runtime.service import ControlServer
from epochgate_runtime.sink import EffectSink
from epochgate_runtime.worker import run_fault


def _running(ledger: RunLedger, run_id: str) -> tuple[int, int]:
    ledger.create_run(run_id)
    ledger.advance(run_id, "running")
    state = ledger.get_run(run_id)
    return state.fencing_token, state.state_version


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _effect_token(ledger: RunLedger, key: str) -> int | None:
    row = ledger._conn.execute(
        "SELECT fencing_token FROM effects WHERE idempotency_key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return int(row[0])


def _post(url: str, body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_t1_revoke_between_claim_and_replace_still_publishes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    staging = workspace / "staging.txt"
    payload = b"order 3"
    staging.write_bytes(payload)
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    token, version = _running(ledger, "run")
    reserved = ledger.record_intent(
        "run",
        "file-key",
        token,
        version,
        effect_id="effect-file",
        arguments_hash="order-3",
        tool_name="eg_write",
    )
    assert reserved["allowed"] is True
    seen: list[str] = []
    real_replace = files_mod.os.replace

    def replace_and_revoke(src, dst) -> None:
        seen.extend(item["event_type"] for item in ledger.events("run"))
        revoked = ledger.revoke("run")
        seen.append(f"revoke:eligible={revoked.eligible}:token={revoked.fencing_token}")
        real_replace(src, dst)

    files_mod.os.replace = replace_and_revoke
    try:
        result = publish_file(
            ledger,
            workspace=workspace,
            run_id="run",
            idempotency_key="file-key",
            fencing_token=token,
            relative_path="order.txt",
            staging_path=staging,
        )
    finally:
        files_mod.os.replace = real_replace

    target = workspace / "order.txt"
    digest = _sha256(target)
    row = ledger.effect_row("file-key")
    state = ledger.get_run("run")
    assert result["published"] is True
    assert result["sha256"] == digest
    assert target.read_bytes() == payload
    assert digest == hashlib.sha256(payload).hexdigest()
    assert row is not None
    assert row["phase"] == "completed"
    assert row["receipt"] == f"file:order.txt:{digest}"
    # claim 写入的 token 仍是 1。complete 传入 0，但冲突更新不改 fencing_token 列，也不再读 Run。
    assert _effect_token(ledger, "file-key") == token
    assert state.eligible is False
    assert state.fencing_token == token + 1
    assert seen[0] == "intent_recorded"
    assert any(item.startswith("revoke:eligible=False") for item in seen)
    names = [item["event_type"] for item in ledger.events("run")]
    assert names == ["intent_recorded", "publish_claimed", "file_published"]
    ledger.close()


def test_t1_revoke_before_claim_withholds_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    staging = workspace / "staging.txt"
    staging.write_bytes(b"order 3")
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    token, version = _running(ledger, "run")
    ledger.record_intent("run", "file-key", token, version, effect_id="e", arguments_hash="a", tool_name="eg_write")
    ledger.revoke("run")
    result = publish_file(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="file-key",
        fencing_token=token,
        relative_path="order.txt",
        staging_path=staging,
    )
    assert result["published"] is False
    assert result["status"] == "withheld"
    assert not (workspace / "order.txt").exists()
    assert ledger.effect_row("file-key")["phase"] != "completed"
    ledger.close()


def _commit_worker(database: str, sink_database: str, token: int, version: int, key: str, payload: str, queue) -> None:
    ledger = RunLedger(database)
    sink = EffectSink(sink_database)
    result = ledger.commit_authorized(
        "run",
        idempotency_key=key,
        fencing_token=token,
        state_version=version,
        sink=sink,
        payload=payload,
    )
    queue.put({
        "status": result["status"],
        "reason": result.get("reason"),
        "applied": result.get("applied"),
        "accepts": sink.accepted_count(key),
        "sink_version": sink._version,
        "stored_payload": None if sink.get(key) is None else sink.get(key)["payload"],
    })
    ledger.close()
    sink.close()


def _revoke_worker(database: str, sink_database: str, queue) -> None:
    ledger = RunLedger(database)
    sink = EffectSink(sink_database)
    state = ledger.revoke_with_sink("run", sink)
    queue.put({"op": "revoke", "token": state.fencing_token, "eligible": state.eligible, "sink_version": sink._version})
    ledger.close()
    sink.close()


def _supersede_worker(database: str, barrier, queue) -> None:
    ledger = RunLedger(database)
    barrier.wait(10)
    try:
        state = ledger.supersede("run")
        queue.put({"ok": True, "token": state.fencing_token, "version": state.state_version})
    except Exception as exc:
        queue.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        ledger.close()


def test_t2_other_process_cannot_use_old_or_successor_credentials(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    database = tmp_path / "ledger.sqlite"
    sink_database = tmp_path / "sink.sqlite"
    ledger = RunLedger(database)
    token, version = _running(ledger, "run")
    moved = ledger.supersede("run")
    ledger.close()
    queue = context.Queue()
    old = context.Process(
        target=_commit_worker,
        args=(str(database), str(sink_database), token, version, "old-key", "old", queue),
    )
    successor = context.Process(
        target=_commit_worker,
        args=(str(database), str(sink_database), moved.fencing_token, moved.state_version, "new-key", "new", queue),
    )
    old.start()
    successor.start()
    old.join(20)
    successor.join(20)
    assert old.exitcode == 0
    assert successor.exitcode == 0
    results = [queue.get(), queue.get()]
    assert [item["status"] for item in results] == ["rejected", "rejected"]
    reasons = sorted((item.get("reason") for item in results), key=lambda item: "" if item is None else item)
    assert reasons == [None, "version"]
    fresh_sink = EffectSink(sink_database)
    assert fresh_sink.accepted_count() == 0
    assert fresh_sink._version == 1
    fresh_sink.close()
    fresh = RunLedger(database)
    assert fresh.get_run("run").fencing_token == 2
    assert fresh.get_run("run").state_version == 2
    assert fresh.effect_row("old-key") is None
    assert fresh.effect_row("new-key") is None
    fresh.close()


def test_t2_same_key_across_processes_accepts_once(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    database = tmp_path / "ledger.sqlite"
    sink_database = tmp_path / "sink.sqlite"
    ledger = RunLedger(database)
    token, version = _running(ledger, "run")
    ledger.close()
    queue = context.Queue()
    first = context.Process(target=_commit_worker, args=(str(database), str(sink_database), token, version, "k", "alpha", queue))
    second = context.Process(target=_commit_worker, args=(str(database), str(sink_database), token, version, "k", "beta", queue))
    first.start()
    second.start()
    first.join(20)
    second.join(20)
    assert first.exitcode == 0
    assert second.exitcode == 0
    results = [queue.get(), queue.get()]
    statuses = sorted(item["status"] for item in results)
    assert statuses == ["completed", "replayed"]
    sink = EffectSink(sink_database)
    assert sink.accepted_count("k") == 1
    assert sink.get("k")["payload"] == "alpha" or sink.get("k")["payload"] == "beta"
    stored = sink.get("k")["payload"]
    sink.close()
    replay = context.Queue()
    again = context.Process(
        target=_commit_worker,
        args=(str(database), str(sink_database), token, version, "k", "gamma", replay),
    )
    again.start()
    again.join(20)
    assert again.exitcode == 0
    replayed = replay.get()
    assert replayed["status"] == "replayed"
    assert replayed["accepts"] == 1
    sink = EffectSink(sink_database)
    assert sink.get("k")["payload"] == stored
    assert sink.accepted_count("k") == 1
    sink.close()


def test_t2_concurrent_revoke_does_not_double_accept(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    accepts = []
    for index in range(4):
        database = tmp_path / f"ledger-{index}.sqlite"
        sink_database = tmp_path / f"sink-{index}.sqlite"
        ledger = RunLedger(database)
        token, version = _running(ledger, "run")
        ledger.close()
        queue = context.Queue()
        commit = context.Process(target=_commit_worker, args=(str(database), str(sink_database), token, version, "k", "x", queue))
        revoke = context.Process(target=_revoke_worker, args=(str(database), str(sink_database), queue))
        commit.start()
        revoke.start()
        commit.join(20)
        revoke.join(20)
        assert commit.exitcode == 0
        assert revoke.exitcode == 0
        sink = EffectSink(sink_database)
        count = sink.accepted_count("k")
        sink.close()
        fresh = RunLedger(database)
        assert count in {0, 1}
        assert fresh.get_run("run").eligible is False
        if count == 1:
            assert fresh.effect_row("k")["phase"] == "completed"
        fresh.close()
        accepts.append(count)
    assert set(accepts) <= {0, 1}
    print("T2_REVOKE_ACCEPTS", accepts)


def test_t2_concurrent_supersede_increments_twice(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    finals = []
    for index in range(12):
        database = tmp_path / f"ledger-{index}.sqlite"
        ledger = RunLedger(database)
        _running(ledger, "run")
        ledger.close()
        barrier = context.Barrier(2)
        queue = context.Queue()
        left = context.Process(target=_supersede_worker, args=(str(database), barrier, queue))
        right = context.Process(target=_supersede_worker, args=(str(database), barrier, queue))
        left.start()
        right.start()
        left.join(20)
        right.join(20)
        assert left.exitcode == 0
        assert right.exitcode == 0
        assert queue.get()["ok"] is True
        assert queue.get()["ok"] is True
        fresh = RunLedger(database)
        finals.append(fresh.get_run("run").fencing_token)
        fresh.close()
    assert finals == [3] * 12
    print("T2_SUPERSEDE_TOKENS", finals)


def _serve(database: str, sink_database: str, workspace: str, queue) -> None:
    ledger = RunLedger(database)
    sink = EffectSink(sink_database)
    server = ControlServer(ledger, sink, Path(workspace))
    server.start()
    queue.put(server.base_url)
    time.sleep(30)


def test_t3_restart_keeps_run_version_and_resets_sink_version(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    database = tmp_path / "ledger.sqlite"
    sink_database = tmp_path / "sink.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    queue = context.Queue()
    process = context.Process(target=_serve, args=(str(database), str(sink_database), str(workspace), queue))
    process.start()
    base = queue.get(timeout=15)
    created = _post(base + "/runs", {"run_id": "run", "session_id": "s"})
    moved = _post(base + "/runs/run/supersede", {})
    process.kill()
    process.join(15)
    assert created["fencing_token"] == 1
    assert created["state_version"] == 1
    assert moved["fencing_token"] == 2
    assert moved["state_version"] == 2
    fresh = RunLedger(database)
    sink = EffectSink(sink_database)
    state = fresh.get_run("run")
    old = fresh.commit_authorized(
        "run",
        idempotency_key="old",
        fencing_token=1,
        state_version=1,
        sink=sink,
        payload="old",
    )
    successor = fresh.commit_authorized(
        "run",
        idempotency_key="successor",
        fencing_token=state.fencing_token,
        state_version=state.state_version,
        sink=sink,
        payload="new",
    )
    assert state.fencing_token == 2
    assert state.state_version == 2
    assert state.eligible is True
    assert sink._version == 2
    assert old["status"] == "rejected"
    assert successor["status"] == "completed"
    assert successor["sink_accepted"] is True
    assert sink.accepted_count() == 1
    fresh.close()
    sink.close()


def test_t4_receipt_loss_reconciles_without_a_second_accept(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    sink_database = tmp_path / "sink.sqlite"
    ledger = RunLedger(database)
    token, version = _running(ledger, "run")
    ledger.close()
    try:
        run_fault(str(database), str(sink_database), "after_sink", "run", "k", token, version)
    except SystemExit as exc:
        assert exc.code == 86
    recovered = RunLedger(database)
    sink = EffectSink(sink_database)
    first = reconcile(recovered, sink, run_id="run", idempotency_key="k", fencing_token=token)
    second = reconcile(recovered, sink, run_id="run", idempotency_key="k", fencing_token=token)
    assert sink.accepted_count("k") == 1
    assert sink.get("k")["payload"] == "payload"
    assert first["decision"] == "confirmed_from_sink"
    assert second["decision"] == "replay"
    assert recovered.effect_row("k")["phase"] == "completed"

    other = sink.accept("other", expected_version=version, payload="other-op")
    assert other["status"] == "accepted"
    assert sink.accepted_count("other") == 1
    assert sink.accepted_count() == 2

    conflict = sink.accept("k", expected_version=version, payload="different")
    assert conflict["status"] == "conflict"
    assert conflict["reason"] == "payload"
    assert sink.get("k")["payload"] == "payload"
    assert sink.accepted_count("k") == 1

    recovered.mark_effect_state("ambiguous", "OUTCOME_UNKNOWN")
    recovered._conn.execute(
        """
        INSERT INTO effects (idempotency_key, run_id, fencing_token, phase, effect_state)
        VALUES ('ambiguous', 'run', ?, 'started', 'OUTCOME_UNKNOWN')
        """,
        (token,),
    )
    recovered._conn.commit()
    unknown = reconcile(recovered, sink, run_id="run", idempotency_key="ambiguous", fencing_token=token)
    retry = recovered.commit_authorized(
        "run",
        idempotency_key="ambiguous",
        fencing_token=token,
        state_version=version,
        sink=sink,
        payload="ambiguous",
    )
    assert unknown["decision"] == "unknown"
    assert unknown["retried"] is False
    assert retry["status"] == "unknown"
    assert sink.get("ambiguous") is None
    recovered.close()
    sink.close()


def test_d2_crash_during_mirror_repairs_without_accepting(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    sink_database = tmp_path / "sink.sqlite"
    ledger = RunLedger(database)
    sink = EffectSink(sink_database)
    _running(ledger, "run")

    def boom(_target: int) -> int:
        raise RuntimeError("mirror")

    sink.advance_to = boom  # type: ignore[method-assign]
    try:
        ledger.supersede("run", sink)
    except RuntimeError:
        pass
    assert ledger.get_run("run").state_version == 2
    assert sink.accepted_count() == 0
    fresh = EffectSink(sink_database)
    assert fresh._version == 1
    ledger.repair_sink(fresh)
    assert fresh._version == 2
    assert fresh.accepted_count() == 0
    done = ledger.commit_authorized(
        "run",
        idempotency_key="successor",
        fencing_token=2,
        state_version=2,
        sink=fresh,
        payload="ok",
    )
    assert done["status"] == "completed"
    assert fresh.accepted_count("successor") == 1
    ledger.close()
    sink.close()
    fresh.close()


def _claimed_replace(database: str, workspace: str) -> None:
    ledger = RunLedger(database)
    state = ledger.get_run("run")
    staging = Path(workspace) / "staging.txt"
    digest = hashlib.sha256(staging.read_bytes()).hexdigest()
    ledger.claim_publish("run", "k", state.fencing_token, digest)
    os.replace(staging, Path(workspace) / "order.txt")
    ledger.close()
    time.sleep(30)


def test_d1_kill_after_replace_reconciles_once(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    database = tmp_path / "ledger.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    staging = workspace / "staging.txt"
    staging.write_bytes(b"order 3")
    ledger = RunLedger(database)
    _running(ledger, "run")
    ledger.close()
    process = context.Process(target=_claimed_replace, args=(str(database), str(workspace)))
    process.start()
    deadline = time.time() + 10
    target = workspace / "order.txt"
    while time.time() < deadline and not target.is_file():
        time.sleep(0.05)
    process.kill()
    process.join(10)
    fresh = RunLedger(database)
    first = reconcile_file(fresh, workspace=workspace, run_id="run", idempotency_key="k", relative_path="order.txt")
    second = reconcile_file(fresh, workspace=workspace, run_id="run", idempotency_key="k", relative_path="order.txt")
    assert first["published"] is True
    assert second["status"] == "replayed"
    assert target.read_bytes() == b"order 3"
    fresh.close()
