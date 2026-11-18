"""修复后的 E1–E3、E5–E8，以及无法启动的 E4 格子。

不写 docs/现行/论文/数据。不打印密钥。
强制终止是 Windows TerminateProcess，不是断电。
"""

from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_deepseek import chat  # noqa: E402

from epochgate_runtime.files import publish_file, reconcile_file  # noqa: E402
from epochgate_runtime.ledger import RunLedger  # noqa: E402
from epochgate_runtime.recovery import reconcile  # noqa: E402
from epochgate_runtime.sandbox import docker_available  # noqa: E402
from epochgate_runtime.service import ControlServer  # noqa: E402
from epochgate_runtime.sink import EffectSink  # noqa: E402
from epochgate_runtime.worker import run_fault  # noqa: E402

OUT = ROOT / "results" / "runtime" / "correctness" / "after"
TRIALS = 100


def _git() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return result.stdout.strip()


def _running(ledger: RunLedger, run_id: str = "run") -> tuple[int, int]:
    ledger.create_run(run_id)
    ledger.advance(run_id, "running")
    state = ledger.get_run(run_id)
    return state.fencing_token, state.state_version


def _pct(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
    return ordered[min(rank, len(ordered) - 1)]


def _summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "p50": _pct(values, 50),
        "p95": _pct(values, 95),
        "p99": _pct(values, 99),
    }


def _commit(database: str, sink_database: str, token: int, version: int, key: str, payload: str, queue) -> None:
    ledger = RunLedger(database)
    sink = EffectSink(sink_database)
    started = time.perf_counter()
    result = ledger.commit_authorized(
        "run", idempotency_key=key, fencing_token=token, state_version=version, sink=sink, payload=payload,
    )
    queue.put({
        "status": result["status"],
        "reason": result.get("reason"),
        "elapsed_s": time.perf_counter() - started,
        "accepts": sink.accepted_count(key),
    })
    ledger.close()
    sink.close()


def _revoke(database: str, sink_database: str, queue) -> None:
    ledger = RunLedger(database)
    sink = EffectSink(sink_database)
    started = time.perf_counter()
    state = ledger.revoke_with_sink("run", sink)
    queue.put({"op": "revoke", "token": state.fencing_token, "eligible": state.eligible, "elapsed_s": time.perf_counter() - started})
    ledger.close()
    sink.close()


def _supersede(database: str, sink_database: str, aligned: bool, barrier, queue) -> None:
    ledger = RunLedger(database)
    sink = EffectSink(sink_database) if aligned else None
    barrier.wait(15)
    started = time.perf_counter()
    try:
        state = ledger.supersede("run", sink)
        queue.put({"ok": True, "token": state.fencing_token, "version": state.state_version, "elapsed_s": time.perf_counter() - started})
    except Exception as exc:
        queue.put({"ok": False, "error": type(exc).__name__, "elapsed_s": time.perf_counter() - started})
    finally:
        ledger.close()
        if sink is not None:
            sink.close()


def _fresh_pair(directory: Path) -> tuple[str, str, int, int]:
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "ledger.sqlite"
    sink_database = directory / "sink.sqlite"
    ledger = RunLedger(database)
    token, version = _running(ledger)
    ledger.close()
    return str(database), str(sink_database), token, version


def e1(root: Path) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    rows: list[dict[str, Any]] = []

    def add(scenario: str, **fields: Any) -> None:
        rows.append({"scenario": scenario, **fields})

    for index in range(TRIALS):
        directory = root / "revoke-then-commit" / str(index)
        database, sink_database, token, version = _fresh_pair(directory)
        ledger = RunLedger(database)
        sink = EffectSink(sink_database)
        ledger.revoke_with_sink("run", sink)
        started = time.perf_counter()
        result = ledger.commit_authorized("run", idempotency_key="k", fencing_token=token, state_version=version, sink=sink, payload="old")
        add("revoke_then_commit", status=result["status"], accepts=sink.accepted_count("k"), elapsed_s=time.perf_counter() - started, final_token=ledger.get_run("run").fencing_token)
        ledger.close()
        sink.close()

        directory = root / "commit-then-revoke" / str(index)
        database, sink_database, token, version = _fresh_pair(directory)
        ledger = RunLedger(database)
        sink = EffectSink(sink_database)
        first = ledger.commit_authorized("run", idempotency_key="k", fencing_token=token, state_version=version, sink=sink, payload="kept")
        ledger.revoke_with_sink("run", sink)
        second = ledger.commit_authorized("run", idempotency_key="k2", fencing_token=token, state_version=version, sink=sink, payload="late")
        add("commit_then_revoke", first=first["status"], second=second["status"], accepts=sink.accepted_count("k"), receipt_kept=ledger.effect_row("k")["phase"] == "completed")
        ledger.close()
        sink.close()

    for index in range(TRIALS):
        directory = root / "dual-supersede" / str(index)
        database, _sink_database, _token, _version = _fresh_pair(directory)
        barrier = context.Barrier(2)
        queue = context.Queue()
        left = context.Process(target=_supersede, args=(database, _sink_database, False, barrier, queue))
        right = context.Process(target=_supersede, args=(database, _sink_database, False, barrier, queue))
        left.start()
        right.start()
        left.join(20)
        right.join(20)
        outcomes = [queue.get(), queue.get()]
        fresh = RunLedger(database)
        final_token = fresh.get_run("run").fencing_token
        oks = sum(1 for item in outcomes if item.get("ok"))
        fresh.close()
        add("dual_supersede", oks=oks, final_token=final_token, lost_updates=max(0, oks - (final_token - 1)), elapsed_s=max(item.get("elapsed_s", 0) for item in outcomes))

        directory = root / "same-key" / str(index)
        database, sink_database, token, version = _fresh_pair(directory)
        queue = context.Queue()
        first = context.Process(target=_commit, args=(database, sink_database, token, version, "k", "alpha", queue))
        second = context.Process(target=_commit, args=(database, sink_database, token, version, "k", "alpha", queue))
        first.start()
        second.start()
        first.join(20)
        second.join(20)
        pair = [queue.get(), queue.get()]
        sink = EffectSink(sink_database)
        add("same_key", statuses=sorted(item["status"] for item in pair), accepts=sink.accepted_count("k"))
        sink.close()

        directory = root / "different-keys" / str(index)
        database, sink_database, token, version = _fresh_pair(directory)
        queue = context.Queue()
        left = context.Process(target=_commit, args=(database, sink_database, token, version, "k1", "one", queue))
        right = context.Process(target=_commit, args=(database, sink_database, token, version, "k2", "two", queue))
        left.start()
        right.start()
        left.join(20)
        right.join(20)
        pair = [queue.get(), queue.get()]
        sink = EffectSink(sink_database)
        add("different_keys", statuses=sorted(item["status"] for item in pair), accepts=sink.accepted_count())
        sink.close()

        directory = root / "aligned-successor" / str(index)
        database, sink_database, token, version = _fresh_pair(directory)
        ledger = RunLedger(database)
        sink = EffectSink(sink_database)
        moved = ledger.supersede("run", sink)
        ledger.close()
        sink.close()
        queue = context.Queue()
        old = context.Process(target=_commit, args=(database, sink_database, token, version, "old", "old", queue))
        new = context.Process(target=_commit, args=(database, sink_database, moved.fencing_token, moved.state_version, "new", "new", queue))
        old.start()
        new.start()
        old.join(20)
        new.join(20)
        pair = [queue.get(), queue.get()]
        sink = EffectSink(sink_database)
        add("aligned_successor", statuses=sorted(item["status"] for item in pair), accepts=sink.accepted_count(), final_version=moved.state_version)
        sink.close()

        directory = root / "random-race" / str(index)
        database, sink_database, token, version = _fresh_pair(directory)
        queue = context.Queue()
        commit = context.Process(target=_commit, args=(database, sink_database, token, version, "k", "race", queue))
        revoke = context.Process(target=_revoke, args=(database, sink_database, queue))
        commit.start()
        revoke.start()
        commit.join(20)
        revoke.join(20)
        raced = [queue.get(), queue.get()]
        commit_row = next(item for item in raced if "status" in item)
        sink = EffectSink(sink_database)
        add("random_revoke_commit", status=commit_row["status"], accepts=sink.accepted_count("k"), elapsed_s=commit_row["elapsed_s"])
        sink.close()

    def count(scenario: str) -> list[dict[str, Any]]:
        return [row for row in rows if row["scenario"] == scenario]

    dual = count("dual_supersede")
    same = count("same_key")
    different = count("different_keys")
    aligned = count("aligned_successor")
    race = count("random_revoke_commit")
    summary = {
        "trials_per_scenario": TRIALS,
        "revoke_then_commit_rejected": sum(1 for row in count("revoke_then_commit") if row["status"] == "rejected" and row["accepts"] == 0),
        "commit_then_revoke_kept": sum(1 for row in count("commit_then_revoke") if row["first"] == "completed" and row["second"] == "rejected" and row["receipt_kept"]),
        "dual_supersede_lost_updates": sum(row["lost_updates"] for row in dual),
        "dual_supersede_final_tokens": sorted({row["final_token"] for row in dual}),
        "same_key_single_accept": sum(1 for row in same if row["accepts"] == 1 and row["statuses"] == ["completed", "replayed"]),
        "different_keys_both_completed": sum(1 for row in different if row["statuses"] == ["completed", "completed"] and row["accepts"] == 2),
        "aligned_successor_completed": sum(1 for row in aligned if row["statuses"] == ["completed", "rejected"] and row["accepts"] == 1),
        "random_commit_wins": sum(1 for row in race if row["status"] == "completed" and row["accepts"] == 1),
        "random_revoke_wins": sum(1 for row in race if row["accepts"] == 0),
        "supersede_latency": _summary([row["elapsed_s"] for row in dual]),
        "random_commit_latency": _summary([row["elapsed_s"] for row in race]),
    }
    path = OUT / "e1.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    (OUT / "e1_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _serve(database: str, sink_database: str, workspace: str, queue) -> None:
    ledger = RunLedger(database)
    sink = EffectSink(sink_database)
    server = ControlServer(ledger, sink, Path(workspace))
    server.start()
    queue.put(server.base_url)
    time.sleep(30)


def _post(url: str, body: dict) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def e2(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    cases: dict[str, Any] = {}

    def rates(old_rejected: bool, successor_completed: bool) -> dict[str, float]:
        return {
            "stale_rejection_rate": 1.0 if old_rejected else 0.0,
            "eligible_successor_completion_rate": 1.0 if successor_completed else 0.0,
        }

    ledger = RunLedger(root / "normal.sqlite")
    sink = EffectSink(root / "normal-sink.sqlite")
    token, version = _running(ledger)
    moved = ledger.supersede("run", sink)
    old = ledger.commit_authorized("run", idempotency_key="old", fencing_token=token, state_version=version, sink=sink, payload="old")
    new = ledger.commit_authorized("run", idempotency_key="new", fencing_token=moved.fencing_token, state_version=moved.state_version, sink=sink, payload="new")
    unaligned = RunLedger(root / "unaligned.sqlite")
    unaligned_sink = EffectSink(root / "unaligned-sink.sqlite")
    utoken, uversion = _running(unaligned)
    umoved = unaligned.supersede("run")
    u_new = unaligned.commit_authorized("run", idempotency_key="new", fencing_token=umoved.fencing_token, state_version=umoved.state_version, sink=unaligned_sink, payload="new")
    cases["normal_aligned"] = rates(old["status"] == "rejected", new["status"] == "completed")
    cases["normal_unaligned_successor_rejected"] = u_new["status"] == "rejected" and u_new.get("reason") == "version"
    ledger.close()
    sink.close()
    unaligned.close()
    unaligned_sink.close()

    context = multiprocessing.get_context("spawn")
    database = root / "restart.sqlite"
    sink_database = root / "restart-sink.sqlite"
    workspace = root / "ws"
    workspace.mkdir(exist_ok=True)
    queue = context.Queue()
    process = context.Process(target=_serve, args=(str(database), str(sink_database), str(workspace), queue))
    process.start()
    base = queue.get(timeout=15)
    created = _post(base + "/runs", {"run_id": "run", "session_id": "s"})
    moved_http = _post(base + "/runs/run/supersede", {})
    process.kill()
    process.join(15)
    fresh = RunLedger(database)
    fresh_sink = EffectSink(sink_database)
    old = fresh.commit_authorized("run", idempotency_key="old", fencing_token=created["fencing_token"], state_version=created["state_version"], sink=fresh_sink, payload="old")
    new = fresh.commit_authorized("run", idempotency_key="new", fencing_token=moved_http["fencing_token"], state_version=moved_http["state_version"], sink=fresh_sink, payload="new")
    cases["restart_control_service"] = rates(old["status"] == "rejected", new["status"] == "completed")
    cases["restart_sink_version"] = fresh_sink._version
    fresh.close()
    fresh_sink.close()

    ledger = RunLedger(root / "crash.sqlite")
    sink = EffectSink(root / "crash-sink.sqlite")
    _running(ledger)
    def boom(_target: int) -> int:
        raise RuntimeError("mirror")
    sink.advance_to = boom  # type: ignore[method-assign]
    try:
        ledger.supersede("run", sink)
    except RuntimeError:
        pass
    accepts_during = sink.accepted_count()
    repaired = EffectSink(root / "crash-sink.sqlite")
    ledger.repair_sink(repaired)
    done = ledger.commit_authorized("run", idempotency_key="new", fencing_token=2, state_version=2, sink=repaired, payload="new")
    old = ledger.commit_authorized("run", idempotency_key="old", fencing_token=1, state_version=1, sink=repaired, payload="old")
    cases["crash_during_version_advance"] = rates(old["status"] == "rejected", done["status"] == "completed")
    cases["accepts_during_failed_mirror"] = accepts_during
    ledger.close()
    sink.close()
    repaired.close()

    lost = 0
    completed = 0
    rejected = 0
    for index in range(20):
        directory = root / "concurrent" / str(index)
        database, sink_database, _token, _version = _fresh_pair(directory)
        barrier = context.Barrier(4)
        queue = context.Queue()
        processes = [context.Process(target=_supersede, args=(database, sink_database, True, barrier, queue)) for _ in range(4)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
        oks = 0
        for _ in range(4):
            item = queue.get()
            oks += int(bool(item.get("ok")))
        fresh = RunLedger(database)
        final_version = fresh.get_run("run").state_version
        lost += max(0, oks - (final_version - 1))
        sink = EffectSink(sink_database)
        result = fresh.commit_authorized("run", idempotency_key="successor", fencing_token=final_version, state_version=final_version, sink=sink, payload="go")
        old = fresh.commit_authorized("run", idempotency_key="stale", fencing_token=1, state_version=1, sink=sink, payload="no")
        completed += int(result["status"] == "completed")
        rejected += int(old["status"] == "rejected")
        fresh.close()
        sink.close()
    cases["concurrent_version_advance"] = {
        "trials": 20,
        "lost_updates": lost,
        "stale_rejection_rate": rejected / 20,
        "eligible_successor_completion_rate": completed / 20,
    }
    (OUT / "e2.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cases


def e3(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "ws"
    workspace.mkdir()
    ledger = RunLedger(root / "ledger.sqlite")
    token, version = _running(ledger)
    staging = workspace / "staging.txt"
    staging.write_bytes(b"order 3")
    ledger.revoke("run")
    before = publish_file(ledger, workspace=workspace, run_id="run", idempotency_key="before", fencing_token=token, relative_path="before.txt", staging_path=staging)
    ledger.close()

    ledger = RunLedger(root / "after-claim.sqlite")
    token, version = _running(ledger)
    staging = workspace / "staging-2.txt"
    staging.write_bytes(b"order 3")
    ledger.record_intent("run", "claimed", token, version, effect_id="e", arguments_hash="a")
    digest = hashlib.sha256(b"order 3").hexdigest()
    claim = ledger.claim_publish("run", "claimed", token, digest)
    ledger.revoke("run")
    os_replace = __import__("os").replace
    os_replace(staging, workspace / "claimed.txt")
    if hashlib.sha256((workspace / "claimed.txt").read_bytes()).hexdigest() == digest:
        finished = ledger.complete_claimed_publish("run", "claimed", digest, f"file:claimed.txt:{digest}", path="claimed.txt")
    else:
        finished = {"published": False}
    events = [item["event_type"] for item in ledger.events("run")]
    ledger.close()

    ledger = RunLedger(root / "reconcile.sqlite")
    token, _version = _running(ledger)
    (workspace / "gold.txt").write_bytes(b"order 3")
    ledger.claim_publish("run", "gold", token, hashlib.sha256(b"order 3").hexdigest())
    ledger.close()
    killed = RunLedger(root / "reconcile.sqlite")
    first = reconcile_file(killed, workspace=workspace, run_id="run", idempotency_key="gold", relative_path="gold.txt")
    second = reconcile_file(killed, workspace=workspace, run_id="run", idempotency_key="gold", relative_path="gold.txt")
    killed.close()

    report = {
        "before_claim": {"published": before.get("published"), "file_exists": (workspace / "before.txt").exists()},
        "after_claim_revoke_still_publishes": {
            "claim": claim["status"],
            "published": finished.get("published"),
            "bytes": (workspace / "claimed.txt").read_bytes().decode("utf-8"),
            "events": events,
        },
        "after_replace_before_receipt": {"first": first["status"] if "status" in first else first.get("published"), "second": second["status"], "bytes": (workspace / "gold.txt").read_bytes().decode("utf-8")},
        "note": "Claim 提交是决定点。Claim 后的撤销不撤回已经决定的发布。TerminateProcess 见 E6，不是断电。",
    }
    (OUT / "e3.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


class _HttpState:
    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self.drop_response = False
        self.unavailable = False
        self.lock = threading.Lock()


def e5(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    ledger = RunLedger(root / "ledger.sqlite")
    sink = EffectSink(root / "sink.sqlite")
    token, version = _running(ledger)
    first = sink.accept("k", expected_version=version, payload="alpha")
    replay = sink.accept("k", expected_version=version, payload="alpha")
    conflict = sink.accept("k", expected_version=version, payload="beta")
    sink.close()
    reopened = EffectSink(root / "sink.sqlite")
    conflict_again = reopened.accept("k", expected_version=version, payload="beta")
    ledger.close()
    prepared = RunLedger(root / "fault2.sqlite")
    prepared_token, prepared_version = _running(prepared, "run")
    prepared.close()
    code = None
    try:
        run_fault(str(root / "fault2.sqlite"), str(root / "fault2-sink.sqlite"), "after_sink", "run", "k", prepared_token, prepared_version)
    except SystemExit as exc:
        code = exc.code
    recovered = RunLedger(root / "fault2.sqlite")
    fault_sink = EffectSink(root / "fault2-sink.sqlite")
    decision = reconcile(recovered, fault_sink, run_id="run", idempotency_key="k", fencing_token=prepared_token)
    later = fault_sink.accept("k", expected_version=prepared_version, payload="other")
    report_fault = {
        "exit": code,
        "reconcile": decision["decision"],
        "conflict": later["status"],
        "payload": fault_sink.get("k")["payload"],
        "accepts": fault_sink.accepted_count("k"),
    }
    recovered.close()
    fault_sink.close()

    state = _HttpState()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            key = str(body["key"])
            payload = str(body["payload"])
            with state.lock:
                if state.unavailable:
                    self.send_response(503)
                    self.end_headers()
                    return
                existing = state.rows.get(key)
                if existing is None:
                    state.rows[key] = payload
                    status = "accepted"
                    stored = payload
                elif existing == payload:
                    status = "replayed"
                    stored = existing
                else:
                    status = "conflict"
                    stored = existing
                drop = state.drop_response and status == "accepted"
            if drop:
                self.connection.close()
                return
            data = json.dumps({"status": status, "payload": stored}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/effects"

    def post(payload: dict) -> dict[str, Any]:
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return {"http": response.status, "body": json.loads(response.read().decode())}
        except Exception as exc:
            return {"http": None, "error": type(exc).__name__}

    state.drop_response = True
    dropped = post({"key": "http", "payload": "once"})
    state.drop_response = False
    replayed = post({"key": "http", "payload": "once"})
    conflicted = post({"key": "http", "payload": "twice"})
    state.unavailable = True
    unknown = post({"key": "missing", "payload": "x"})
    server.shutdown()
    prepared_unknown = RunLedger(root / "unknown.sqlite")
    utoken, uversion = _running(prepared_unknown)
    sealed = prepared_unknown.seal_unconfirmed_external("run", "ext", utoken, uversion)
    retried = prepared_unknown.commit_authorized("run", idempotency_key="ext", fencing_token=utoken, state_version=uversion, sink=reopened, payload="ext")
    report = {
        "same_payload": replay["status"],
        "different_payload": conflict["status"],
        "restart_conflict": conflict_again["status"],
        "stored_payload": reopened.get("k")["payload"],
        "accepts": reopened.accepted_count("k"),
        "receipt_loss": report_fault,
        "http_drop": dropped,
        "http_replay": replayed,
        "http_conflict": conflicted,
        "http_unavailable": unknown,
        "sealed_external": sealed["status"],
        "external_retry": retried["status"],
        "boundary": "本地 HTTP 只说明响应丢失、冲突和不可查询时不能自动重试。不是任意 HTTP 服务的 exactly-once。",
    }
    prepared_unknown.close()
    reopened.close()
    (OUT / "e5.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _kill_after_accept(database: str, sink_database: str) -> None:
    ledger = RunLedger(database)
    sink = EffectSink(sink_database)
    state = ledger.get_run("run")
    sink.accept("k", expected_version=state.state_version, payload="payload")
    ledger.close()
    sink.close()
    time.sleep(30)


def _hold_transaction(database: str) -> None:
    import sqlite3
    connection = sqlite3.connect(database)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("UPDATE runs SET fencing_token = 99 WHERE run_id = 'run'")
    time.sleep(30)


def e6(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    ledger = RunLedger(root / "exit.sqlite")
    token, version = _running(ledger, "run")
    ledger.close()
    code = None
    try:
        run_fault(str(root / "exit.sqlite"), str(root / "exit-sink.sqlite"), "after_sink", "run", "k", token, version)
    except SystemExit as exc:
        code = exc.code
    recovered = RunLedger(root / "exit.sqlite")
    sink = EffectSink(root / "exit-sink.sqlite")
    decision = reconcile(recovered, sink, run_id="run", idempotency_key="k", fencing_token=token)
    controlled = {"exit": code, "decision": decision["decision"], "accepts": sink.accepted_count("k"), "kind": "SystemExit"}
    recovered.close()
    sink.close()

    context = multiprocessing.get_context("spawn")
    database = root / "kill.sqlite"
    sink_database = root / "kill-sink.sqlite"
    ledger = RunLedger(database)
    _running(ledger)
    ledger.close()
    process = context.Process(target=_kill_after_accept, args=(str(database), str(sink_database)))
    process.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        probe = EffectSink(sink_database)
        count = probe.accepted_count("k")
        probe.close()
        if count == 1:
            break
        time.sleep(0.05)
    process.kill()
    process.join(10)
    fresh = RunLedger(database)
    fresh_sink = EffectSink(sink_database)
    missing = fresh.effect_row("k")
    repaired = reconcile(fresh, fresh_sink, run_id="run", idempotency_key="k", fencing_token=1)
    forced = {
        "kind": "TerminateProcess",
        "not_power_loss": True,
        "sink_accepts": fresh_sink.accepted_count("k"),
        "ledger_phase_before_reconcile": None if missing is None else missing["phase"],
        "reconcile": repaired["decision"],
        "accepts_after": fresh_sink.accepted_count("k"),
    }
    fresh.close()
    fresh_sink.close()

    database = root / "rollback.sqlite"
    ledger = RunLedger(database)
    _running(ledger, "run")
    ledger.close()
    holder = context.Process(target=_hold_transaction, args=(str(database),))
    holder.start()
    time.sleep(0.5)
    holder.kill()
    holder.join(10)
    rolled = RunLedger(database)
    rollback = {"fencing_token": rolled.get_run("run").fencing_token, "kind": "uncommitted sqlite transaction rolled back by process death"}
    rolled.close()
    report = {"controlled_exit": controlled, "forced_termination": forced, "uncommitted_rollback": rollback}
    (OUT / "e6.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _e7_serial() -> dict[str, Any]:
    serial: dict[str, list[float]] = {name: [] for name in ("admit", "claim", "commit", "revoke", "supersede", "reconcile", "replay")}
    for sample in range(60):
        ledger = RunLedger()
        sink = EffectSink()
        _running(ledger)
        started = time.perf_counter()
        ledger.get_run("run")
        serial["admit"].append(time.perf_counter() - started)
        started = time.perf_counter()
        moved = ledger.supersede("run", sink)
        serial["supersede"].append(time.perf_counter() - started)
        started = time.perf_counter()
        ledger.claim_publish("run", f"file-{sample}", moved.fencing_token, "abc")
        serial["claim"].append(time.perf_counter() - started)
        started = time.perf_counter()
        ledger.commit_authorized("run", idempotency_key=f"c{sample}", fencing_token=moved.fencing_token, state_version=moved.state_version, sink=sink, payload="p")
        serial["commit"].append(time.perf_counter() - started)
        started = time.perf_counter()
        again = ledger.commit_authorized("run", idempotency_key=f"c{sample}", fencing_token=moved.fencing_token, state_version=moved.state_version, sink=sink, payload="p")
        serial["replay"].append(time.perf_counter() - started)
        if again["status"] != "replayed":
            raise RuntimeError(again["status"])
        started = time.perf_counter()
        reconcile(ledger, sink, run_id="run", idempotency_key=f"c{sample}", fencing_token=moved.fencing_token)
        serial["reconcile"].append(time.perf_counter() - started)
        started = time.perf_counter()
        ledger.revoke_with_sink("run", sink)
        serial["revoke"].append(time.perf_counter() - started)
        ledger.close()
        sink.close()
    return {name: _summary(values) for name, values in serial.items()}


def e7() -> dict[str, Any]:
    serial = _e7_serial()
    levels = {}
    for concurrency in (1, 2, 4, 8, 16):
        elapsed: list[float] = []
        failures: list[str] = []
        for batch in range(3):
            directory = OUT / "e7-work" / f"{concurrency}-{batch}"
            directory.mkdir(parents=True, exist_ok=True)
            database = directory / "ledger.sqlite"
            sink_database = directory / "sink.sqlite"
            setup = RunLedger(database)
            _running(setup)
            setup.close()
            barrier = threading.Barrier(concurrency)
            box: list[float] = []
            errors: list[str] = []

            def once() -> None:
                local_ledger = RunLedger(database)
                local_sink = EffectSink(sink_database)
                barrier.wait(10)
                started = time.perf_counter()
                try:
                    local_ledger.supersede("run", local_sink)
                    box.append(time.perf_counter() - started)
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")
                finally:
                    local_ledger.close()
                    local_sink.close()

            threads = [threading.Thread(target=once) for _ in range(concurrency)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            elapsed.extend(box)
            failures.extend(errors)
        levels[str(concurrency)] = {
            "supersede": _summary(elapsed),
            "supersede_failures": len(failures),
            "supersede_failure_samples": failures[:4],
        }
    base = levels["1"]["supersede"]["p50"] or 0
    for block in levels.values():
        stats = block["supersede"]
        stats["contention_over_concurrency_1_s"] = None if stats["p50"] is None else stats["p50"] - base
    report = {
        "host": "one machine, one sqlite connection per thread",
        "not_cross_machine": True,
        "serial_n": 60,
        "serial": serial,
        "contention_batches": 3,
        "levels": levels,
    }
    (OUT / "e7.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def e8() -> dict[str, Any]:
    samples: list[dict[str, float]] = []
    for index in range(60):
        ledger = RunLedger()
        sink = EffectSink()
        token, version = _running(ledger)
        model_s = 0.0
        queue_s = 0.0
        tool_started = time.perf_counter()
        digest = hashlib.sha256(b"OK").hexdigest()
        ledger.claim_publish("run", f"k{index}", token, digest)
        tool_s = time.perf_counter() - tool_started
        control_started = time.perf_counter()
        ledger.commit_authorized("run", idempotency_key=f"c{index}", fencing_token=token, state_version=version, sink=sink, payload="OK")
        control_s = time.perf_counter() - control_started
        recovery_started = time.perf_counter()
        reconcile(ledger, sink, run_id="run", idempotency_key=f"c{index}", fencing_token=token)
        recovery_s = time.perf_counter() - recovery_started
        total = model_s + queue_s + tool_s + control_s + recovery_s
        samples.append({"model_s": model_s, "queue_s": queue_s, "tool_s": tool_s, "control_s": control_s, "recovery_s": recovery_s, "total_s": total})
        ledger.close()
        sink.close()
    offline = {key: _summary([row[key] for row in samples]) for key in samples[0]}
    probe = chat("Reply with the single word OK", model="deepseek-flash", max_tokens=16)
    model_report: dict[str, Any]
    if not probe.get("ok"):
        model_report = {"status": "blocked", "error": probe.get("error"), "completed": 0}
    else:
        arm_samples: list[dict[str, Any]] = []
        for arm in ("B0", "B1", "B2"):
            for _index in range(20):
                started = time.perf_counter()
                reply = chat("Reply with the single word OK", model="deepseek-flash", max_tokens=16)
                model_s = time.perf_counter() - started
                ledger = RunLedger()
                sink = EffectSink()
                token, version = _running(ledger)
                control_started = time.perf_counter()
                if arm != "B0":
                    ledger.commit_authorized("run", idempotency_key="ok", fencing_token=token, state_version=version, sink=sink, payload="OK")
                if arm == "B2":
                    reconcile(ledger, sink, run_id="run", idempotency_key="ok", fencing_token=token)
                control_s = time.perf_counter() - control_started
                arm_samples.append({"arm": arm, "ok": bool(reply.get("ok")), "model_s": model_s, "control_s": control_s, "total_s": model_s + control_s})
                ledger.close()
                sink.close()
        model_report = {
            "status": "completed",
            "n_per_arm": 20,
            "completed": sum(1 for row in arm_samples if row["ok"]),
            "arms": {
                arm: {
                    "model": _summary([row["model_s"] for row in arm_samples if row["arm"] == arm]),
                    "control": _summary([row["control_s"] for row in arm_samples if row["arm"] == arm]),
                    "total": _summary([row["total_s"] for row in arm_samples if row["arm"] == arm]),
                }
                for arm in ("B0", "B1", "B2")
            },
        }
    report = {
        "historical_60_untouched": "docs/现行/论文/数据/01-主实验/tables.json",
        "offline_fixed_trace": offline,
        "live_model": model_report,
    }
    (OUT / "e8.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def e4() -> dict[str, Any]:
    dsh = ROOT / "infrastructure" / "dsh-profiles" / "research" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    node = shutil_which("node")
    reasons = []
    if not dsh.is_file():
        reasons.append(f"dsh binary missing: {dsh}")
    if node is None:
        reasons.append("node is not on PATH")
    if not docker_available():
        reasons.append("docker is not available for the coding task")
    reason = "; ".join(reasons) if reasons else ""
    tasks = {
        "inventory": ["state_change", "missing_notice", "version_mismatch", "receipt_loss"],
        "coding": ["concurrent_edit", "stale_credential", "publish_failure", "receipt_loss"],
        "approval": ["permission_revoked", "receipt_loss", "retry", "version_mismatch"],
    }
    arms = ["direct", "commit_only", "full"]
    path = OUT / "e4.jsonl"
    count = 0
    lines = []
    for task, faults in tasks.items():
        for fault in faults:
            for repeat in range(10):
                for arm in arms:
                    count += 1
                    lines.append(json.dumps({
                        "task": task,
                        "fault": fault,
                        "repeat": repeat,
                        "arm": arm,
                        "status": "blocked",
                        "reason": reason or "not started",
                        "scored": False,
                        "model_called": False,
                    }, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary = {"cells": count, "blocked": count, "completed": 0, "scored": 0, "reason": reason}
    (OUT / "e4_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    work = OUT / "work"
    meta = {
        "git": _git(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": "after",
    }
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("e1", json.dumps(e1(work / "e1"), ensure_ascii=False), flush=True)
    print("e2", json.dumps(e2(work / "e2"), ensure_ascii=False), flush=True)
    print("e3", json.dumps(e3(work / "e3"), ensure_ascii=False), flush=True)
    print("e5", json.dumps(e5(work / "e5"), ensure_ascii=False), flush=True)
    print("e6", json.dumps(e6(work / "e6"), ensure_ascii=False), flush=True)
    print("e7", "written", flush=True)
    e7()
    print("e8", "start", flush=True)
    print("e8", json.dumps(e8(), ensure_ascii=False)[:500], flush=True)
    print("e4", json.dumps(e4(), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "e7":
        OUT.mkdir(parents=True, exist_ok=True)
        print(json.dumps({"supersede_failures": {key: value.get("supersede_failures") for key, value in e7()["levels"].items()}}, ensure_ascii=False))
    else:
        main()
