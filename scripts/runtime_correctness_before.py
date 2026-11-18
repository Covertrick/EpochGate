"""记录修复前的 T1–T4 交错。只写 results/runtime/correctness/before，不改冻结稿。"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import subprocess
import time
import urllib.request
from pathlib import Path

import epochgate_runtime.files as files_mod
from epochgate_runtime.files import publish_file
from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.recovery import reconcile
from epochgate_runtime.service import ControlServer
from epochgate_runtime.sink import EffectSink
from epochgate_runtime.worker import run_fault

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "correctness" / "before"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return result.stdout.strip()


def _running(ledger: RunLedger, run_id: str) -> tuple[int, int]:
    ledger.create_run(run_id)
    ledger.advance(run_id, "running")
    state = ledger.get_run(run_id)
    return state.fencing_token, state.state_version


def t1(tmp: Path) -> dict:
    tmp.mkdir(parents=True, exist_ok=True)
    workspace = tmp / "ws"
    workspace.mkdir()
    staging = workspace / "staging.txt"
    staging.write_bytes(b"order 3")
    ledger = RunLedger(tmp / "ledger.sqlite")
    token, version = _running(ledger, "run")
    ledger.record_intent("run", "file-key", token, version, effect_id="e", arguments_hash="a", tool_name="eg_write")
    real = files_mod.os.replace

    def hooked(src, dst) -> None:
        ledger.revoke("run")
        real(src, dst)

    files_mod.os.replace = hooked
    try:
        result = publish_file(
            ledger, workspace=workspace, run_id="run", idempotency_key="file-key",
            fencing_token=token, relative_path="order.txt", staging_path=staging,
        )
    finally:
        files_mod.os.replace = real
    target = workspace / "order.txt"
    before = publish_file(
        RunLedger(tmp / "withheld.sqlite"),
        workspace=workspace,
        run_id="missing",
        idempotency_key="nope",
        fencing_token=1,
        relative_path="order.txt",
        staging_path=staging,
    ) if False else None
    withheld_ledger = RunLedger(tmp / "withheld.sqlite")
    wtoken, wversion = _running(withheld_ledger, "run")
    withheld_stage = workspace / "stage2.txt"
    withheld_stage.write_bytes(b"order 3")
    withheld_ledger.record_intent("run", "file-key", wtoken, wversion, effect_id="e", arguments_hash="a")
    withheld_ledger.revoke("run")
    withheld = publish_file(
        withheld_ledger, workspace=workspace, run_id="run", idempotency_key="file-key",
        fencing_token=wtoken, relative_path="other.txt", staging_path=withheld_stage,
    )
    record = {
        "published": result.get("published"),
        "status": result.get("status"),
        "file_bytes": target.read_bytes().decode("utf-8") if target.is_file() else None,
        "sha256": _sha(target) if target.is_file() else None,
        "receipt": None if ledger.effect_row("file-key") is None else ledger.effect_row("file-key")["receipt"],
        "run_token": ledger.get_run("run").fencing_token,
        "eligible": ledger.get_run("run").eligible,
        "events": [item["event_type"] for item in ledger.events("run")],
        "revoke_before_claim": withheld,
        "other_exists": (workspace / "other.txt").exists(),
    }
    ledger.close()
    withheld_ledger.close()
    return record


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
        "sink_version": sink._version,
        "payload": None if sink.get(key) is None else sink.get(key)["payload"],
    })
    ledger.close()
    sink.close()


def _supersede(database: str, barrier, queue) -> None:
    ledger = RunLedger(database)
    barrier.wait(10)
    started = time.perf_counter()
    state = ledger.supersede("run")
    queue.put({"token": state.fencing_token, "version": state.state_version, "elapsed_s": time.perf_counter() - started})
    ledger.close()


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


def t2(tmp: Path) -> dict:
    tmp.mkdir(parents=True, exist_ok=True)
    context = multiprocessing.get_context("spawn")
    database = tmp / "ledger.sqlite"
    sink_database = tmp / "sink.sqlite"
    ledger = RunLedger(database)
    token, version = _running(ledger, "run")
    moved = ledger.supersede("run")
    ledger.close()
    queue = context.Queue()
    old = context.Process(target=_commit, args=(str(database), str(sink_database), token, version, "old-key", "old", queue))
    successor = context.Process(target=_commit, args=(str(database), str(sink_database), moved.fencing_token, moved.state_version, "new-key", "new", queue))
    old.start()
    successor.start()
    old.join(20)
    successor.join(20)
    unaligned = [queue.get(), queue.get()]
    finals = []
    for index in range(12):
        path = tmp / f"sup-{index}.sqlite"
        one = RunLedger(path)
        _running(one, "run")
        one.close()
        barrier = context.Barrier(2)
        box = context.Queue()
        left = context.Process(target=_supersede, args=(str(path), barrier, box))
        right = context.Process(target=_supersede, args=(str(path), barrier, box))
        left.start()
        right.start()
        left.join(20)
        right.join(20)
        fresh = RunLedger(path)
        finals.append(fresh.get_run("run").fencing_token)
        fresh.close()
    return {"unaligned_commits": unaligned, "concurrent_supersede_final_tokens": finals}


def t3(tmp: Path) -> dict:
    tmp.mkdir(parents=True, exist_ok=True)
    context = multiprocessing.get_context("spawn")
    database = tmp / "ledger.sqlite"
    sink_database = tmp / "sink.sqlite"
    workspace = tmp / "ws"
    workspace.mkdir()
    queue = context.Queue()
    process = context.Process(target=_serve, args=(str(database), str(sink_database), str(workspace), queue))
    process.start()
    base = queue.get(timeout=15)
    created = _post(base + "/runs", {"run_id": "run", "session_id": "s"})
    moved = _post(base + "/runs/run/supersede", {})
    process.kill()
    process.join(15)
    fresh = RunLedger(database)
    sink = EffectSink(sink_database)
    state = fresh.get_run("run")
    old = fresh.commit_authorized("run", idempotency_key="old", fencing_token=1, state_version=1, sink=sink, payload="old")
    successor = fresh.commit_authorized(
        "run", idempotency_key="successor", fencing_token=state.fencing_token,
        state_version=state.state_version, sink=sink, payload="new",
    )
    record = {
        "created": created,
        "moved": moved,
        "restart_token": state.fencing_token,
        "restart_version": state.state_version,
        "sink_version": sink._version,
        "old": old,
        "successor": successor,
        "accepts": sink.accepted_count(),
    }
    fresh.close()
    sink.close()
    return record


def t4(tmp: Path) -> dict:
    tmp.mkdir(parents=True, exist_ok=True)
    database = tmp / "ledger.sqlite"
    sink_database = tmp / "sink.sqlite"
    ledger = RunLedger(database)
    token, version = _running(ledger, "run")
    ledger.close()
    code = None
    try:
        run_fault(str(database), str(sink_database), "after_sink", "run", "k", token, version)
    except SystemExit as exc:
        code = exc.code
    recovered = RunLedger(database)
    sink = EffectSink(sink_database)
    first = reconcile(recovered, sink, run_id="run", idempotency_key="k", fencing_token=token)
    second = reconcile(recovered, sink, run_id="run", idempotency_key="k", fencing_token=token)
    other = sink.accept("other", expected_version=version, payload="other-op")
    conflict = sink.accept("k", expected_version=version, payload="different")
    record = {
        "exit_code": code,
        "first": first,
        "second": second,
        "accepts_k": sink.accepted_count("k"),
        "stored_payload": sink.get("k")["payload"],
        "other": other["status"],
        "different_payload": conflict["status"],
        "payload_after_different": sink.get("k")["payload"],
    }
    recovered.close()
    sink.close()
    return record


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    root = OUT / "work"
    report = {
        "git": _git(),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": "before",
        "t1": t1(root / "t1"),
        "t2": t2(root / "t2"),
        "t3": t3(root / "t3"),
        "t4": t4(root / "t4"),
    }
    path = OUT / "probe.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(path), "tokens": report["t2"]["concurrent_supersede_final_tokens"], "t3_sink": report["t3"]["sink_version"], "t4": report["t4"]["different_payload"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
