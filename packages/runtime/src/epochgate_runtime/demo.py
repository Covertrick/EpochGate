"""离线演示。每次使用新的 Run ID，重复执行不必删除 SQLite。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from epochgate_runtime.api import Runtime, timeline_lines, write_report
from epochgate_runtime.recovery import reconcile


_KIND_LABELS = {
    "token": "旧令牌",
    "demo": "旧令牌",
    "key": "同一把钥匙",
    "idem": "同一把钥匙",
    "crash": "崩溃窗口",
    "fix": "先无效，后修正",
    "walk": "一次看完",
    "sess": "真实任务",
}


def next_demo_id(ledger: Any, kind: str) -> str:
    """同一种样板按 01、02 往后排，不再用一串随机号。"""
    prefix = f"{kind}-"
    numbers = []
    for row in ledger.list_runs():
        run_id = str(row["run_id"])
        if not run_id.startswith(prefix):
            continue
        tail = run_id[len(prefix):]
        if tail.isdigit():
            numbers.append(int(tail))
    return f"{kind}-{max(numbers, default=0) + 1:02d}"


def run_title(run_id: str) -> str:
    kind, _, rest = str(run_id).partition("-")
    label = _KIND_LABELS.get(kind)
    if label is None:
        return str(run_id)
    if rest.isdigit():
        return f"{label} {int(rest)}"
    return f"{label} {rest[:4]}" if rest else label


def prepare_dirs(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "runs").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    (data_dir / "workspace").mkdir(parents=True, exist_ok=True)


def run_stale_write(data_dir: Path) -> dict[str, Any]:
    """旧 token 发布被拒绝，新 token 发布一次，同一键再次发布不再写文件。"""
    prepare_dirs(data_dir)
    runtime = Runtime.open(data_dir / "epochgate.db", data_dir=data_dir)
    try:
        run_id = next_demo_id(runtime.ledger, "token")
        first = runtime.reserve(run_id, "workspace/main.py")
        second = runtime.reserve(run_id, "workspace/main.py")
        stale = runtime.publish(first, b"old-bytes")
        fresh = runtime.publish(second, b"new-bytes")
        target = runtime.workspace / run_id / "main.py"
        written = target.read_bytes() if target.is_file() else b""
        replay = runtime.publish(second, b"should-not-replace")
        rewritten = target.read_bytes() if target.is_file() else b""
        runtime.ledger.append_event(
            run_id,
            "publish_outcome",
            {"status": replay.get("status"), "unchanged": rewritten == b"new-bytes", "idempotency_key": second.idempotency_key},
        )
        snapshot = runtime.inspect(run_id)
    finally:
        runtime.close()
    reopened = Runtime.open(data_dir / "epochgate.db", data_dir=data_dir)
    try:
        verified = reopened.verify(run_id)
        snapshot = reopened.inspect(run_id)
    finally:
        reopened.close()
    duplicate_writes = 0 if rewritten == b"new-bytes" else 1
    ok = (
        first.fencing_token != second.fencing_token
        and stale.get("status") == "withheld"
        and fresh.get("published") is True
        and fresh.get("receipt")
        and replay.get("status") in {"replayed", "conflict"}
        and written == b"new-bytes"
        and duplicate_writes == 0
        and verified["verified"] is True
    )
    report = {
        "scenario": "stale-write",
        "offline": True,
        "run_id": run_id,
        "phase": snapshot["phase"],
        "fencing_token": snapshot["fencing_token"],
        "attempt_1": "REVOKED",
        "attempt_1_token": first.fencing_token,
        "attempt_2": "COMMITTED" if fresh.get("published") else "REJECTED",
        "attempt_2_token": second.fencing_token,
        "effect_receipt": "VERIFIED" if verified["verified"] else "MISSING",
        "duplicate_accepts": duplicate_writes,
        "stale_status": stale.get("status"),
        "replay_status": replay.get("status"),
        "verified": ok,
        "events": snapshot["events"],
        "effects": snapshot["effects"],
        "note": "token 1 已作废。Run 仍接受 token 2。取消整次 Run 使用 revoke，不会留下可提交的新 token。",
    }
    paths = write_report(data_dir / "runs" / run_id, report)
    report["report_json"] = paths["json"]
    report["report_html"] = paths["html"]
    return report


def run_crash_recovery(data_dir: Path) -> dict[str, Any]:
    """Worker 在写入接收器后、receipt 前退出。重新打开账本后补记同一次效果。"""
    prepare_dirs(data_dir)
    database = data_dir / "epochgate.db"
    sink_path = data_dir / "sink.sqlite"
    runtime = Runtime.open(database, data_dir=data_dir)
    try:
        run_id = next_demo_id(runtime.ledger, "crash")
        runtime.ledger.create_run(run_id)
        runtime.ledger.append_event(run_id, "run_created", {"scenario": "crash-recovery"})
        runtime.ledger.advance(run_id, "running")
        state = runtime.ledger.get_run(run_id)
        token = state.fencing_token
        version = state.state_version
        key = f"k-{run_id}"
    finally:
        runtime.close()
    completed = subprocess.run(
        [
            sys.executable, "-m", "epochgate_runtime.worker",
            "--db", str(database),
            "--sink", str(sink_path),
            "--fault", "after_sink",
            "--run", run_id,
            "--key", key,
            "--token", str(token),
            "--version", str(version),
        ],
        check=False,
        capture_output=True,
    )
    recovered = Runtime.open(database, data_dir=data_dir)
    try:
        recovered.ledger.append_event(run_id, "worker_crashed", {
            "checkpoint": "after_sink" if completed.returncode == 86 else None,
            "exit_code": completed.returncode,
            "idempotency_key": key,
        })
        first = reconcile(recovered.ledger, recovered.sink, run_id=run_id, idempotency_key=key, fencing_token=token)
        second = reconcile(recovered.ledger, recovered.sink, run_id=run_id, idempotency_key=key, fencing_token=token)
        recovered.ledger.append_event(run_id, "reconcile_decision", {"decision": first["decision"], "idempotency_key": key, "order": 1})
        recovered.ledger.append_event(run_id, "reconcile_decision", {"decision": second["decision"], "idempotency_key": key, "order": 2})
        accepts = recovered.sink.accepted_count(key)
        snapshot = recovered.inspect(run_id)
    finally:
        recovered.close()
    ok = (
        completed.returncode == 86
        and first["decision"] == "confirmed_from_sink"
        and second["decision"] == "replay"
        and accepts == 1
    )
    report = {
        "scenario": "crash-recovery",
        "offline": True,
        "run_id": run_id,
        "phase": snapshot["phase"],
        "fencing_token": snapshot["fencing_token"],
        "worker_exit": completed.returncode,
        "recovery": first["decision"],
        "second_recovery": second["decision"],
        "duplicate_accepts": 0 if accepts == 1 else accepts,
        "effect_receipt": "VERIFIED" if ok else "MISSING",
        "verified": ok,
        "events": snapshot["events"],
        "effects": snapshot["effects"],
    }
    paths = write_report(data_dir / "runs" / run_id, report)
    report["report_json"] = paths["json"]
    report["report_html"] = paths["html"]
    report["timeline"] = timeline_lines(snapshot)
    return report


def run_idempotency_conflict(data_dir: Path) -> dict[str, Any]:
    """同一资格、同一幂等键，第二份不同字节不能替换已经发布的文件。"""
    prepare_dirs(data_dir)
    runtime = Runtime.open(data_dir / "epochgate.db", data_dir=data_dir)
    try:
        run_id = next_demo_id(runtime.ledger, "key")
        attempt = runtime.reserve(run_id, "workspace/main.py")
        first = runtime.publish(attempt, b"kept-bytes")
        second = runtime.publish(attempt, b"other-bytes")
        target = runtime.workspace / run_id / "main.py"
        written = target.read_bytes() if target.is_file() else b""
        runtime.ledger.append_event(
            run_id,
            "publish_outcome",
            {
                "status": second.get("status"),
                "unchanged": written == b"kept-bytes",
                "idempotency_key": attempt.idempotency_key,
            },
        )
        snapshot = runtime.inspect(run_id)
    finally:
        runtime.close()
    ok = first.get("published") is True and second.get("status") in {"replayed", "conflict"} and written == b"kept-bytes"
    report = {
        "scenario": "idempotency-conflict",
        "offline": True,
        "run_id": run_id,
        "phase": snapshot["phase"],
        "fencing_token": snapshot["fencing_token"],
        "replay_status": second.get("status"),
        "verified": ok,
        "events": snapshot["events"],
        "effects": snapshot["effects"],
    }
    paths = write_report(data_dir / "runs" / run_id, report)
    report["report_json"] = paths["json"]
    report["report_html"] = paths["html"]
    return report


def run_corrected_effect(data_dir: Path) -> dict[str, Any]:
    """第一份已经落地的字节业务上无效，后继 token 发布修正后的字节。第一份回执仍在。"""
    prepare_dirs(data_dir)
    runtime = Runtime.open(data_dir / "epochgate.db", data_dir=data_dir)
    try:
        run_id = next_demo_id(runtime.ledger, "fix")
        first = runtime.reserve(run_id, "workspace/main.py")
        early = runtime.publish(first, b"return 0\n")
        second = runtime.reserve(run_id, "workspace/main.py")
        later = runtime.publish(second, b"return 1\n")
        target = runtime.workspace / run_id / "main.py"
        written = target.read_bytes() if target.is_file() else b""
        effects = runtime.ledger.effects_for_run(run_id)
        snapshot = runtime.inspect(run_id)
    finally:
        runtime.close()
    early_receipt = early.get("receipt")
    kept = any(item.get("receipt") == early_receipt and early_receipt for item in effects)
    ok = early.get("published") is True and later.get("published") is True and written == b"return 1\n" and kept
    report = {
        "scenario": "corrected-effect",
        "offline": True,
        "run_id": run_id,
        "phase": snapshot["phase"],
        "fencing_token": snapshot["fencing_token"],
        "verified": ok,
        "events": snapshot["events"],
        "effects": snapshot["effects"],
    }
    paths = write_report(data_dir / "runs" / run_id, report)
    report["report_json"] = paths["json"]
    report["report_html"] = paths["html"]
    return report


def format_stale_write(report: dict[str, Any]) -> str:
    state = "Execution control verified" if report["verified"] else "Execution control failed"
    return "\n".join(
        [
            f"Run                  {report['run_id']}",
            f"Attempt #1           {report['attempt_1']}",
            f"Attempt #2           {report['attempt_2']}",
            f"Effect receipt       {report['effect_receipt']}",
            f"Duplicate accepts    {report['duplicate_accepts']}",
            state,
            f"Report: {report['report_json']}",
        ]
    )


def format_crash(report: dict[str, Any]) -> str:
    state = "Recovery verified" if report["verified"] else "Recovery failed"
    return "\n".join(
        [
            f"Run                  {report['run_id']}",
            f"Worker exit          {report['worker_exit']}",
            f"Recovery             {report['recovery']}",
            f"Second recovery      {report['second_recovery']}",
            f"Effect receipt       {report['effect_receipt']}",
            f"Duplicate accepts    {report['duplicate_accepts']}",
            state,
            f"Report: {report['report_json']}",
        ]
    )
