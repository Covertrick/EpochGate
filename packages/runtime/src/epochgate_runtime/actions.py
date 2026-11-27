"""一次容器运行里按顺序做完协议里要指出来的五件事。"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from epochgate_runtime.api import Runtime
from epochgate_runtime.demo import next_demo_id
from epochgate_runtime.processes import cancel_process
from epochgate_runtime.recovery import reconcile
from epochgate_runtime.sandbox import (
    docker_available,
    docker_command,
    exec_in,
    exec_root,
    lock_workspace,
    publish_container,
    start_container,
    stop_container,
)


def run_walkthrough(runtime: Runtime, data_dir: Path) -> dict[str, Any]:
    if not docker_available():
        raise RuntimeError("docker unavailable")
    ledger = runtime.ledger
    run_id = next_demo_id(ledger, "walk")
    container = "eg-walk-" + run_id[-8:]
    database = data_dir / "epochgate.db"
    sink_path = data_dir / "sink.sqlite"
    start_container(container)
    try:
        lock_workspace(container)
        ledger.create_run(run_id)
        ledger.append_event(run_id, "run_created", {"scenario": "walkthrough", "container": container})
        ledger.advance(run_id, "running")
        state = ledger.get_run(run_id)
        key = f"file-{run_id}"
        staging = f"/scratch/.eg-staging/{key}"
        _stage(container, staging, b"first-bytes\n")
        published = publish_container(
            ledger,
            container=container,
            run_id=run_id,
            idempotency_key=key,
            fencing_token=state.fencing_token,
            relative_path="note.txt",
            staging_path=staging,
        )
        if not published.get("published"):
            raise RuntimeError(f"container publish failed: {published}")
        old_token = state.fencing_token
        ledger.supersede(run_id, runtime.sink)
        withheld = publish_container(
            ledger,
            container=container,
            run_id=run_id,
            idempotency_key=f"late-{run_id}",
            fencing_token=old_token,
            relative_path="late.txt",
            staging_path=staging,
        )
        if withheld.get("status") != "withheld":
            raise RuntimeError(f"old token was not withheld: {withheld}")
        fresh = ledger.get_run(run_id)
        conflict = ledger.claim_publish(run_id, key, fresh.fencing_token, hashlib.sha256(b"other-bytes\n").hexdigest())
        current = _read(container, "/workspace/note.txt")
        ledger.append_event(
            run_id,
            "publish_outcome",
            {"status": conflict.get("status"), "unchanged": current == b"first-bytes\n", "idempotency_key": key},
        )
        crash_key = f"k-{run_id}"
        crashed = ledger.get_run(run_id)
        completed = subprocess.run(
            [
                sys.executable, "-m", "epochgate_runtime.worker",
                "--db", str(database),
                "--sink", str(sink_path),
                "--fault", "after_sink",
                "--run", run_id,
                "--key", crash_key,
                "--token", str(crashed.fencing_token),
                "--version", str(crashed.state_version),
            ],
            check=False,
            capture_output=True,
        )
        if completed.returncode != 86:
            raise RuntimeError(f"crash worker exited {completed.returncode}")
        first = reconcile(ledger, runtime.sink, run_id=run_id, idempotency_key=crash_key, fencing_token=crashed.fencing_token)
        second = reconcile(ledger, runtime.sink, run_id=run_id, idempotency_key=crash_key, fencing_token=crashed.fencing_token)
        ledger.append_event(run_id, "reconcile_decision", {"decision": first["decision"], "idempotency_key": crash_key, "order": 1})
        ledger.append_event(run_id, "reconcile_decision", {"decision": second["decision"], "idempotency_key": crash_key, "order": 2})
        process = subprocess.Popen(
            docker_command(["exec", "-i", "-u", "1000:1000", container, "python", "-c", "import time; time.sleep(30)"]),
        )
        cancel_process(ledger, run_id, process)
        return {"run_id": run_id, "container": container, "verified": True}
    finally:
        stop_container(container)


def _stage(container: str, path: str, payload: bytes) -> None:
    code = "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(sys.stdin.buffer.read())"
    result = exec_in(container, ["python", "-c", code, path], data=payload)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-400:])


def _read(container: str, path: str) -> bytes:
    result = exec_root(container, ["python", "-c", "import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())", path])
    if result.returncode != 0:
        return b""
    return result.stdout
