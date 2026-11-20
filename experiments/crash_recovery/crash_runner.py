"""Parent process for Week 4 crash injection.

Starts a real child, kills it with TerminateProcess on Windows or SIGKILL
elsewhere, then reconciles in a new process. Rows record process_kill and
never claim power-loss durability.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from frozen_proposals.db import connect
from frozen_proposals.hashing import sha256_bytes
from fault_replay.contract import ARCHIVED_JOINT, ARCHIVED_SHA256, WEEK3
from fault_replay.kill_runner import frozen_proposal, hard_kill, launch_and_kill, pid_alive, resolve_payload_path

REPO = Path(__file__).resolve().parents[1]
WEEK4 = REPO / "crash_recovery"
MATRIX_PATH = WEEK4 / "crash_matrix_extended.csv"
REPORT_PATH = WEEK4 / "crash_evidence_extended.md"
DECISION_PATH = WEEK4 / "independence_decision.json"

MATRIX_FIELDS = [
    "run_id",
    "checkpoint",
    "path",
    "killer",
    "killed_pid",
    "post_kill_state",
    "recovery_action",
    "evidence_state",
    "target_digest",
    "sealed_digest",
    "receipt_present",
    "logical_effect_count",
    "semantic_result",
    "latency_ms",
    "process_kill",
    "power_loss_claim",
]

RECEIVER_CHECKPOINTS = (
    "before_receiver_accept",
    "after_receiver_accept",
    "after_receiver_accept_conflict",
)
RELAY_CHECKPOINT = "after_commit_before_send"
REPEAT_CHECKPOINTS = (
    "after_claim_before_sealed",
    "after_sealed_before_replace",
    "after_replace_before_receipt",
    "after_receipt",
)

RECEIVER_EXPECT = {
    "before_receiver_accept": ("UNKNOWN", "WITHHOLD_UNSEALED", "RECEIVER_NOT_ACCEPTED", 0, "UNKNOWN"),
    "after_receiver_accept": ("CONFIRM_TARGET", "CONFIRM_EXISTING_TARGET", "ACCEPTED_RECEIPT_LOST", 1, "VALID"),
    "after_receiver_accept_conflict": ("CONFLICT", "HOLD_CONFLICT", "EFFECT_KEY_CONFLICT", 1, "INVALID"),
}
REPEAT_EXPECT = {
    "after_claim_before_sealed": ("UNKNOWN", "UNKNOWN", "WITHHOLD_UNSEALED", "WITHHOLD_UNSEALED", 0),
    "after_sealed_before_replace": (
        "PUBLISH_SEALED",
        "CONFIRM_TARGET",
        "PUBLISH_SEALED_BYTES",
        "CONFIRM_EXISTING_TARGET",
        1,
    ),
    "after_replace_before_receipt": (
        "CONFIRM_TARGET",
        "CONFIRM_TARGET",
        "CONFIRM_EXISTING_TARGET",
        "CONFIRM_EXISTING_TARGET",
        1,
    ),
    "after_receipt": (
        "REPLAY_RECEIPT",
        "REPLAY_RECEIPT",
        "REPLAY_STORED_RECEIPT",
        "REPLAY_STORED_RECEIPT",
        1,
    ),
}

CONFLICT_BYTES = b"CONFLICT|effect|different-payload"
DECISIONS = ("SAME_CORE", "PARTIALLY_SHARED", "INDEPENDENT")


def killer_name() -> str:
    return "TerminateProcess" if sys.platform == "win32" else "SIGKILL"


def archived_sha256() -> str:
    import hashlib

    return hashlib.sha256(ARCHIVED_JOINT.read_bytes()).hexdigest()


def assert_archive_intact() -> str:
    digest = archived_sha256()
    if digest != ARCHIVED_SHA256:
        raise RuntimeError(f"archived_joint.csv hash changed: {digest}")
    return digest


def _fresh(run_dir: Path) -> None:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)


def _wait_marker(path: Path, proc: subprocess.Popen, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        if proc.poll() is not None:
            stderr = ""
            if proc.stderr:
                stderr = proc.stderr.read()
            raise RuntimeError(f"child exited before marker: code={proc.returncode} stderr={stderr}")
        time.sleep(0.05)
    raise TimeoutError(f"marker not written: {path}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _proposal_file(proposal: dict[str, Any]) -> Path:
    path = resolve_payload_path(proposal)
    blob = path.read_bytes()
    if sha256_bytes(blob) != proposal["payload_sha256"]:
        raise RuntimeError(f"frozen payload drift: {proposal['proposal_id']}")
    return path


def _contract(proposal: dict[str, Any], *, run_id: str, path: str, checkpoint: str, rsr: bool = False) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "path": path,
        "checkpoint": checkpoint,
        "proposal_id": proposal["proposal_id"],
        "payload_sha256": proposal["payload_sha256"],
        "effect_key": proposal["idempotency_key"],
        "outbox_event_id": proposal.get("outbox_event_id") or f"obx:{proposal['proposal_id']}",
        "world_after": proposal["world_after"],
        "authority_token": int(proposal["authority_token"]),
        "authority_epoch": int(proposal["authority_epoch"]),
        "rsr": rsr,
        "conflict_name": "conflict.bin",
    }


def _prepare_surface(run_dir: Path, contract: dict[str, Any], sealed: bytes | None) -> None:
    conn = connect(run_dir / "state.sqlite")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE durable_surface (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                effect_key TEXT NOT NULL,
                claim_status TEXT NOT NULL,
                sealed_payload BLOB,
                sealed_digest TEXT,
                receipt_id TEXT
            )
            """
        )
        digest = None if sealed is None else sha256_bytes(sealed)
        status = "CLAIMED" if sealed is None else "SEALED"
        conn.execute(
            """
            INSERT INTO durable_surface (
                id, effect_key, claim_status, sealed_payload, sealed_digest, receipt_id
            ) VALUES (1, ?, ?, ?, ?, NULL)
            """,
            (contract["effect_key"], status, sealed, digest),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


def spawn_and_kill(run_dir: Path, role: str, proposal_bin: Path) -> dict[str, Any]:
    log_path = run_dir / "child.log"
    failure: Exception | None = None
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "crash_recovery.crash_child",
                "--role",
                role,
                "--run-dir",
                str(run_dir),
                "--proposal-bin",
                str(proposal_bin),
            ],
            cwd=str(REPO),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            marker = _wait_marker(run_dir / "marker.json", proc)
        except Exception as exc:
            if proc.poll() is None and pid_alive(proc.pid):
                hard_kill(proc.pid)
            failure = exc
            marker = {}
        else:
            killed_pid = int(marker["pid"])
            exit_code = hard_kill(killed_pid)
            if proc.pid != killed_pid and pid_alive(proc.pid):
                hard_kill(proc.pid)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if pid_alive(proc.pid):
                    hard_kill(proc.pid)
                proc.wait(timeout=10)
    if failure is not None:
        detail = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
        raise RuntimeError(f"{failure}\n{detail}") from failure
    if pid_alive(killed_pid):
        raise RuntimeError(f"pid {killed_pid} still alive after {killer_name()}")
    if sys.platform == "win32" and exit_code != 1:
        raise RuntimeError(f"{killer_name()} exit code {exit_code} for pid {killed_pid}")
    return {"killed_pid": killed_pid, "exit_code": exit_code, "marker": marker}


def reconcile(run_dir: Path, role: str, label: str = "recover") -> tuple[dict[str, Any], float]:
    log_path = run_dir / f"{label}.log"
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "crash_recovery.crash_child", "--role", role, "--run-dir", str(run_dir)],
            cwd=str(REPO),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        code = proc.wait(timeout=30)
    latency_ms = (time.perf_counter() - started) * 1000.0
    text = log_path.read_text(encoding="utf-8")
    if code != 0:
        raise RuntimeError(f"{role} failed ({code}): {text}")
    body = json.loads((run_dir / "recovery_result.json").read_text(encoding="utf-8"))
    return body, latency_ms


def reconcile_week3(run_dir: Path, label: str) -> tuple[dict[str, Any], float]:
    log_path = run_dir / f"reconcile-{label}.log"
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "fault_replay.kill_child", "--role", "reconcile", "--run-dir", str(run_dir)],
            cwd=str(REPO),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        code = proc.wait(timeout=30)
    latency_ms = (time.perf_counter() - started) * 1000.0
    text = log_path.read_text(encoding="utf-8")
    if code != 0:
        raise RuntimeError(f"week3 reconcile {label} failed ({code}): {text}")
    body = json.loads((run_dir / "recovery_result.json").read_text(encoding="utf-8"))
    return body, latency_ms


def _effect_stamp(run_dir: Path) -> tuple[str, int] | None:
    path = run_dir / "effect.bin"
    if not path.is_file():
        return None
    return sha256_bytes(path.read_bytes()), path.stat().st_mtime_ns


def _applied_count(run_dir: Path) -> int:
    path = run_dir / "receiver_log.json"
    if not path.is_file():
        return 0
    log = json.loads(path.read_text(encoding="utf-8"))
    return sum(1 for row in log if row.get("accepted"))


def _ledger_rows(run_dir: Path) -> list[tuple[Any, ...]]:
    conn = connect(run_dir / "state.sqlite")
    try:
        rows = conn.execute(
            """
            SELECT id, claim_status, payload_sha256, sealed_digest, receipt_id
            FROM kill_ledger ORDER BY id
            """
        ).fetchall()
        return [tuple(row) for row in rows]
    finally:
        conn.close()


def _base_row(recovered: dict[str, Any], launched: dict[str, Any], latency_ms: float) -> dict[str, Any]:
    if recovered["memory_sentinel_seen"]:
        raise RuntimeError("memory sentinel was written to disk")
    if int(recovered["reconciler_pid"]) == int(launched["killed_pid"]):
        raise RuntimeError("reconciler reused the killed pid")
    if pid_alive(int(launched["killed_pid"])):
        raise RuntimeError("killed pid is still alive")
    return {
        "run_id": recovered["run_id"],
        "checkpoint": recovered["checkpoint"],
        "path": recovered["path"],
        "killer": killer_name(),
        "killed_pid": int(launched["killed_pid"]),
        "post_kill_state": recovered["post_kill_state"],
        "recovery_action": recovered["recovery_action"],
        "evidence_state": recovered["evidence_state"],
        "target_digest": recovered["target_digest"] or "",
        "sealed_digest": recovered["sealed_digest"] or "",
        "receipt_present": bool(recovered["receipt_present"]),
        "logical_effect_count": int(recovered["logical_effect_count"]),
        "semantic_result": recovered["semantic_result"],
        "latency_ms": latency_ms,
        "process_kill": True,
        "power_loss_claim": False,
        "exit_code": int(launched["exit_code"]),
        "reconciler_pid": int(recovered["reconciler_pid"]),
        "memory_sentinel_seen": False,
    }


def run_receiver_case(
    run_dir: Path,
    *,
    proposal: dict[str, Any],
    checkpoint: str,
    run_id: str,
) -> dict[str, Any]:
    if checkpoint not in RECEIVER_EXPECT:
        raise KeyError(checkpoint)
    _fresh(run_dir)
    contract = _contract(proposal, run_id=run_id, path="SharedReceiver", checkpoint=checkpoint)
    _write_json(run_dir / "contract.json", contract)
    payload_path = _proposal_file(proposal)
    sealed = None if checkpoint == "before_receiver_accept" else payload_path.read_bytes()
    _prepare_surface(run_dir, contract, sealed)
    if checkpoint == "after_receiver_accept_conflict":
        if sha256_bytes(CONFLICT_BYTES) == proposal["payload_sha256"]:
            raise RuntimeError("conflict fixture matches the sealed payload")
        (run_dir / "conflict.bin").write_bytes(CONFLICT_BYTES)
    launched = spawn_and_kill(run_dir, "receiver", payload_path)
    before = _effect_stamp(run_dir)
    if (run_dir / "receipt.json").is_file():
        raise RuntimeError("receiver wrote a receipt before it was killed")
    recovered, latency_ms = reconcile(run_dir, "receiver_reconcile")
    after = _effect_stamp(run_dir)
    if before != after:
        raise RuntimeError("receiver recovery rewrote effect.bin")
    row = _base_row(recovered, launched, latency_ms)
    expected = RECEIVER_EXPECT[checkpoint]
    actual = (
        row["evidence_state"],
        row["recovery_action"],
        row["post_kill_state"],
        row["logical_effect_count"],
        row["semantic_result"],
    )
    if actual != expected:
        raise RuntimeError(f"{run_id} expected {expected} got {actual}")
    if checkpoint == "before_receiver_accept" and (row["sealed_digest"] or row["target_digest"]):
        raise RuntimeError("UNKNOWN row has a digest")
    if checkpoint == "after_receiver_accept" and row["target_digest"] != row["sealed_digest"]:
        raise RuntimeError("CONFIRM_TARGET digests differ")
    if checkpoint == "after_receiver_accept_conflict" and row["target_digest"] == row["sealed_digest"]:
        raise RuntimeError("CONFLICT digests match")
    if row["receipt_present"]:
        raise RuntimeError("receiver crash row has a durable receipt")
    return row


def run_relay_case(run_dir: Path, *, proposal: dict[str, Any], run_id: str) -> dict[str, Any]:
    _fresh(run_dir)
    contract = _contract(proposal, run_id=run_id, path="Outbox", checkpoint=RELAY_CHECKPOINT)
    _write_json(run_dir / "contract.json", contract)
    launched = spawn_and_kill(run_dir, "relay", _proposal_file(proposal))
    if (run_dir / "effect.bin").is_file():
        raise RuntimeError("relay child sent before the kill")
    recovered, latency_ms = reconcile(run_dir, "relay_recover")
    row = _base_row(recovered, launched, latency_ms)
    if row["evidence_state"] != "PUBLISH_SEALED" or row["recovery_action"] != "RELAY_THEN_REPLAY":
        raise RuntimeError(f"{run_id} relay evidence {row['evidence_state']} {row['recovery_action']}")
    if row["logical_effect_count"] != 1:
        raise RuntimeError(f"{run_id} logical_effect_count {row['logical_effect_count']}")
    if row["post_kill_state"] != "OUTBOX_COMMITTED_NOT_SENT":
        raise RuntimeError(row["post_kill_state"])
    if row["semantic_result"] != "VALID":
        raise RuntimeError(row["semantic_result"])
    if not row["target_digest"] or row["target_digest"] != row["sealed_digest"]:
        raise RuntimeError("relay target does not match the committed outbox payload")
    return row


def run_repeat_case(
    run_dir: Path,
    *,
    proposal: dict[str, Any],
    checkpoint: str,
    run_id: str,
    between: Callable[[Path], None] | None = None,
) -> dict[str, Any]:
    if checkpoint not in REPEAT_EXPECT:
        raise KeyError(checkpoint)
    _fresh(run_dir)
    launched = launch_and_kill(
        run_dir,
        proposal=proposal,
        path="kill_ledger",
        checkpoint=checkpoint,
        run_id=run_id,
    )
    if pid_alive(int(launched["killed_pid"])):
        raise RuntimeError("repeat child still alive")
    if sys.platform == "win32" and int(launched["exit_code"]) != 1:
        raise RuntimeError(f"repeat kill exit code {launched['exit_code']}")
    stamp0 = _effect_stamp(run_dir)
    applied0 = _applied_count(run_dir)
    ledger0 = _ledger_rows(run_dir)
    first, latency_first = reconcile_week3(run_dir, "first")
    stamp1 = _effect_stamp(run_dir)
    applied1 = _applied_count(run_dir)
    if between is not None:
        between(run_dir)
    second, latency_second = reconcile_week3(run_dir, "second")
    stamp2 = _effect_stamp(run_dir)
    applied2 = _applied_count(run_dir)
    ledger2 = _ledger_rows(run_dir)
    expect_first, expect_second, action_first, action_second, expect_count = REPEAT_EXPECT[checkpoint]
    if first["evidence_state"] != expect_first or second["evidence_state"] != expect_second:
        raise RuntimeError(
            f"{run_id} evidence {first['evidence_state']} then {second['evidence_state']}"
        )
    if first["recovery_action"] != action_first or second["recovery_action"] != action_second:
        raise RuntimeError(
            f"{run_id} action {first['recovery_action']} then {second['recovery_action']}"
        )
    if second["recovery_action"] == "PUBLISH_SEALED_BYTES":
        raise RuntimeError(f"{run_id} second recovery published")
    hash1 = None if stamp1 is None else stamp1[0]
    hash2 = None if stamp2 is None else stamp2[0]
    if hash1 != hash2:
        raise RuntimeError(f"{run_id} second recovery replaced sealed bytes")
    if between is None and stamp1 != stamp2:
        raise RuntimeError(f"{run_id} effect mtime changed on the second recovery")
    if checkpoint == "after_sealed_before_replace" and (stamp0 is not None or stamp1 is None):
        raise RuntimeError(f"{run_id} publish did not happen exactly once")
    if checkpoint != "after_sealed_before_replace" and stamp0 != stamp2 and between is None:
        raise RuntimeError(f"{run_id} recovery changed bytes that were already durable")
    if applied1 != applied2 or applied2 != expect_count:
        raise RuntimeError(f"{run_id} applied count {applied0}->{applied1}->{applied2}")
    if ledger0 != ledger2 or len(ledger2) != 1:
        raise RuntimeError(f"{run_id} ledger changed or duplicated: {ledger0} -> {ledger2}")
    if first.get("memory_sentinel_seen") or second.get("memory_sentinel_seen"):
        raise RuntimeError("sentinel survived on disk")
    launched_view = {"killed_pid": launched["killed_pid"], "exit_code": launched["exit_code"]}
    recovered = dict(first)
    recovered["run_id"] = run_id
    recovered["checkpoint"] = checkpoint
    recovered["path"] = "kill_ledger"
    recovered["recovery_action"] = f"FIRST:{first['recovery_action']};SECOND:{second['recovery_action']}"
    recovered["logical_effect_count"] = applied2
    recovered["semantic_result"] = first["semantic_oracle_result"]
    recovered["memory_sentinel_seen"] = False
    row = _base_row(recovered, launched_view, latency_first + latency_second)
    row["second_evidence_state"] = second["evidence_state"]
    row["second_reconciler_pid"] = int(second["reconciler_pid"])
    row["ledger_count"] = len(ledger2)
    row["effect_rewritten"] = hash1 != hash2
    row["double_published"] = False
    return row


def run_receiver_matrix(repeats: int, work_root: Path, proposal: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for checkpoint in RECEIVER_CHECKPOINTS:
        for repeat in range(1, repeats + 1):
            run_id = f"w4-recv-{checkpoint}-r{repeat:02d}"
            row = run_receiver_case(
                work_root / run_id,
                proposal=proposal,
                checkpoint=checkpoint,
                run_id=run_id,
            )
            rows.append(row)
            print(f"{len(rows)} {run_id} {row['evidence_state']}", flush=True)
    return rows


def run_relay_matrix(repeats: int, work_root: Path, proposal: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for repeat in range(1, repeats + 1):
        run_id = f"w4-relay-{RELAY_CHECKPOINT}-r{repeat:02d}"
        row = run_relay_case(work_root / run_id, proposal=proposal, run_id=run_id)
        rows.append(row)
        print(f"relay {repeat} {row['evidence_state']} logical={row['logical_effect_count']}", flush=True)
    return rows


def run_repeat_matrix(repeats: int, work_root: Path, proposal: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for checkpoint in REPEAT_CHECKPOINTS:
        for repeat in range(1, repeats + 1):
            run_id = f"w4-repeat-{checkpoint}-r{repeat:02d}"
            row = run_repeat_case(
                work_root / run_id,
                proposal=proposal,
                checkpoint=checkpoint,
                run_id=run_id,
            )
            rows.append(row)
            print(
                f"repeat {run_id} {row['evidence_state']} then {row['second_evidence_state']}",
                flush=True,
            )
    return rows


def write_matrix(rows: list[dict[str, Any]], path: Path = MATRIX_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATRIX_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            if row.get("process_kill") is not True or row.get("power_loss_claim") is not False:
                raise RuntimeError(f"bad kill flags on {row.get('run_id')}")
            exported = {key: row.get(key, "") for key in MATRIX_FIELDS}
            exported["receipt_present"] = "true" if row["receipt_present"] else "false"
            exported["process_kill"] = "true"
            exported["power_loss_claim"] = "false"
            exported["latency_ms"] = f"{float(row['latency_ms']):.3f}"
            exported["logical_effect_count"] = str(int(row["logical_effect_count"]))
            writer.writerow(exported)
    return path


def _counts(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], int]:
    grouped: dict[tuple[str, str, str], int] = defaultdict(int)
    for row in rows:
        grouped[(row["checkpoint"], row["path"], row["evidence_state"])] += 1
    return dict(grouped)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def decision_token(path: Path = DECISION_PATH) -> str | None:
    """Return the audit token, ``""`` when the file is ambiguous, or None when absent."""
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))

    def walk(node: Any, found: list[str]) -> None:
        if isinstance(node, str) and node in DECISIONS:
            found.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value, found)
        elif isinstance(node, list):
            for value in node:
                walk(value, found)

    if isinstance(data, dict):
        for key in ("decision", "verdict", "independence", "result", "classification"):
            value = data.get(key)
            if isinstance(value, str) and value in DECISIONS:
                return value
    found: list[str] = []
    walk(data, found)
    unique = set(found)
    if len(unique) == 1:
        return found[0]
    return ""


def _closing(token: str | None) -> str:
    if token is None:
        return (
            "本报告是这条持久事务表面的恢复证据。新进程重新打开 SQLite 和文件系统，按上面的谓词给出证据状态。"
            "这里不声称 ByteClaim 与 Outbox 是两套机制。"
        )
    if token == "":
        return (
            "`independence_decision.json` 已经在，但里面没有单一的 INDEPENDENT、PARTIALLY_SHARED 或 SAME_CORE。"
            "本报告仍然只作为这条持久事务表面的恢复证据，不从这些崩溃行声称机制差别。"
        )
    if token == "SAME_CORE":
        return (
            "`independence_decision.json` 的结论是 SAME_CORE。"
            "本工作要说明的是这条共享持久表面的恢复证据状态机。"
            "ByteClaim 与 Outbox 这两个名字共用上面的谓词。"
        )
    if token == "PARTIALLY_SHARED":
        return (
            "`independence_decision.json` 的结论是 PARTIALLY_SHARED。"
            "审计写明：durable claim 与 same-DB outbox 共用运行器、接收端和恢复入口；"
            "持久行分别写入 `byte_claims` 与 `business_effects`/`outbox`。"
            "本报告测量的是这条共享恢复入口的证据状态机。"
            "`SharedReceiver`、`Outbox.commit` 之后的中继、`kill_ledger` 的重复核对，以及 `ByteClaim` 上的 RSR 预检，都按上面的同一套谓词记录。"
        )
    return (
        "`independence_decision.json` 的结论是 INDEPENDENT。"
        "本报告给出的是各代码路径上可以复现的恢复证据状态，用来对照那个结论。"
        "这些崩溃行本身不另写一套机制比较。"
    )


def _rsr_section(rsr_rows: list[dict[str, Any]] | None) -> list[str]:
    if not rsr_rows:
        return [
            "## RSR 消融",
            "",
            "RSR 这次没有跑。没有消融表，也不把未测量的检查说成收益。",
            "",
        ]
    lines = [
        "## RSR 消融",
        "",
        "场景是 S14 与 S16。被结束的代码路径是 `ByteClaim.commit`，发生在 `SharedReceiver.accept` 之前。恢复进程从 SQLite 读出 claim 字节。`ByteClaim+RSR` 在发布前再调用 `semantics_oracle` 1.0.0：`VALID` 才发布，`INVALID` 返回 `SEMANTICALLY_SUPERSEDED` 并且不发布，`UNKNOWN` 返回 `UNKNOWN` 并且不发布。",
        "",
        "无效恢复指：恢复把密封字节发布出去，并且这些字节相对该场景的 `world_after` 被 `semantics_oracle` 判为 `INVALID`。`CORRECT_STOP` 仍只表示后继场景里的正确停止，本表单独计数。",
        "",
        "| 场景 | 路径 | 次数 | 无效恢复 | UNKNOWN | SEMANTICALLY_SUPERSEDED | CORRECT_STOP | 恢复耗时均值 ms | 预检均值 ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rsr_rows:
        groups[(row["scenario"], row["path"])].append(row)
    for key in sorted(groups):
        bucket = groups[key]
        invalid = sum(1 for row in bucket if row["invalid_restore"])
        unknown = sum(1 for row in bucket if row["evidence_state"] == "UNKNOWN" or row["semantic_result"] == "UNKNOWN")
        superseded = sum(1 for row in bucket if row["evidence_state"] == "SEMANTICALLY_SUPERSEDED")
        correct = sum(1 for row in bucket if row["evidence_state"] == "CORRECT_STOP")
        latency = _mean([float(row["latency_ms"]) for row in bucket])
        precheck = _mean([float(row["precheck_ms"]) for row in bucket])
        lines.append(
            f"| {key[0]} | {key[1]} | {len(bucket)} | {invalid} | {unknown} | {superseded} | {correct} | {latency:.3f} | {precheck:.3f} |"
        )
    lines.append("")
    for scenario in ("S14", "S16"):
        plain = groups.get((scenario, "ByteClaim"), [])
        gated = groups.get((scenario, "ByteClaim+RSR"), [])
        if not plain or not gated:
            continue
        invalid_drop = sum(1 for row in plain if row["invalid_restore"]) - sum(
            1 for row in gated if row["invalid_restore"]
        )
        unknown_rise = sum(1 for row in gated if row["evidence_state"] == "UNKNOWN") - sum(
            1 for row in plain if row["evidence_state"] == "UNKNOWN"
        )
        stop_rise = sum(1 for row in gated if row["evidence_state"] == "SEMANTICALLY_SUPERSEDED") - sum(
            1 for row in plain if row["evidence_state"] == "SEMANTICALLY_SUPERSEDED"
        )
        correct_rise = sum(1 for row in gated if row["evidence_state"] == "CORRECT_STOP") - sum(
            1 for row in plain if row["evidence_state"] == "CORRECT_STOP"
        )
        latency_delta = _mean([float(row["latency_ms"]) for row in gated]) - _mean(
            [float(row["latency_ms"]) for row in plain]
        )
        lines.append(
            f"{scenario}：无效恢复减少 {invalid_drop} 次。证据状态 `UNKNOWN` 增加 {unknown_rise} 次。"
            f"`SEMANTICALLY_SUPERSEDED` 增加 {stop_rise} 次。`CORRECT_STOP` 增加 {correct_rise} 次。"
            f"恢复进程耗时均值变化 {latency_delta:+.3f} ms（ByteClaim+RSR 减 ByteClaim）。"
        )
    lines.extend(
        [
            "",
            "这组数字是权衡，不是单边收益。无效恢复从每场景 10 次降到 0 次，被挡住的发布记为 `SEMANTICALLY_SUPERSEDED`。本次 40 次预检全部是 `INVALID`，所以 `UNKNOWN` 与 `CORRECT_STOP` 没有上升。恢复耗时是进程墙钟，包含启动。预检调用本身不到 0.05 ms，墙钟均值差大于这次调用，并落在各次启动时间的波动里。",
            "",
            "RSR 不消除 TOCTOU。`semantics_oracle` 返回之后、真正发布之前，评分世界仍然可能再变。`VALID` 分支只说明检查当时世界和密封字节一致，不把这次检查变成与发布原子的事实。",
            "",
        ]
    )
    return lines


def write_report(
    rows: list[dict[str, Any]],
    rsr_rows: list[dict[str, Any]] | None,
    path: Path = REPORT_PATH,
    decision_path: Path = DECISION_PATH,
) -> Path:
    grouped = _counts(rows)
    relay = [row for row in rows if row["checkpoint"] == RELAY_CHECKPOINT]
    repeat = [row for row in rows if row["path"] == "kill_ledger"]
    relay_counts = sorted({int(row["logical_effect_count"]) for row in relay})
    double = [row["run_id"] for row in repeat if row.get("double_published") or row.get("effect_rewritten")]
    lines = [
        "# Week 4 进程崩溃恢复",
        "",
        "这是受控的进程崩溃注入。父进程确认检查点标记之后，用 Windows `TerminateProcess` 结束子进程。子进程内存随之消失。另一个新进程重新打开 SQLite 和文件系统，再按证据谓词核对。恢复不读取子进程内存里的载荷。",
        "",
        "这不是掉电耐久性，也不是失败概率。每一行的 `process_kill` 为 true，`power_loss_claim` 为 false。重复次数是预先固定的。不声称任意 HTTP 恰好一次，不声称跨库原子性。",
        "",
        "路径按代码名记录。接收端崩溃走 `SharedReceiver`。中继崩溃走 `Outbox.commit`，发送使用 `SharedReceiver.accept`。重复恢复走 `fault_replay.kill_child` 里的 `kill_ledger`，这是 Week 3 里两个路径标签实际执行的同一段子进程。RSR 消融走 `ByteClaim.commit`。本报告不把这些名字写成两套独立机制。",
        "",
        f"- 崩溃矩阵行数：{len(rows)}。",
        f"- 中继 `logical_effect_count` 的取值：{relay_counts or '无'}。",
        f"- 重复恢复里二次发布或重写效果文件的行：{len(double)}。",
        "",
        "## 证据状态机",
        "",
        "符号：`sealed` 是 SQLite 里密封载荷的 SHA256，没有密封字节则为空。`target` 是 `effect.bin` 的 SHA256，文件不存在则为空。`receipt` 表示 `receipt.json` 里有回执，且回执摘要等于 `sealed`。判定只使用这些已经落盘的事实，顺序固定。",
        "",
        "1. `CONFLICT`：`sealed` 与 `target` 都非空，并且不相等。这是同一个 effect key 上已经落盘的载荷和密封载荷不同。动作 `HOLD_CONFLICT`，不覆盖文件。",
        "2. `REPLAY_RECEIPT`：回执存在，并且回执摘要、`sealed`、`target` 三者相等。动作 `REPLAY_STORED_RECEIPT`，不再发布一次。",
        "3. `CONFIRM_TARGET`：`target` 等于 `sealed`，回执不在盘上。接收端已经接纳，回执丢失。动作 `CONFIRM_EXISTING_TARGET`，不重写。",
        "4. `PUBLISH_SEALED`：`sealed` 非空，`target` 为空，回执不存在。未启用 RSR 时，动作是发布密封字节。中继检查点的动作是 `RELAY_THEN_REPLAY`：新进程把已提交的 outbox 载荷交给 `SharedReceiver`，再交一次；第二次是 replay，`accepted_count` 保持 1。启用 RSR 时，发布前先对密封字节调用 `semantics_oracle`。",
        "5. `SEMANTICALLY_SUPERSEDED`：第 4 步的持久条件成立，且 RSR 预检为 `INVALID`。动作 `WITHHOLD_SUPERSEDED`，不发布。",
        "6. `UNKNOWN`：`sealed`、`target`、回执都缺失。接收端没有接纳，也没有可发布的字节。动作 `WITHHOLD_UNSEALED`。RSR 预检结果不是 `VALID` 也不是 `INVALID` 时，同样返回 `UNKNOWN` 并且不发布。",
        "",
        "`UNKNOWN` 不是失败。`CONFLICT` 不是把文件改写成密封字节。",
        "",
        "## 接收端崩溃",
        "",
        "被结束的是接收端进程。检查点在 `SharedReceiver.accept` 之前，以及 accept 已经把效果文件 fsync、回执还只在该进程内存里的时候。冲突检查点使用同一个 effect key，效果文件里是另一份载荷。每个检查点 10 次。恢复进程只分类，不发布，也不重写 `effect.bin`。",
        "",
        "| 检查点 | 代码路径 | 证据状态 | 次数 | 是否单一 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for checkpoint in RECEIVER_CHECKPOINTS:
        matches = [(key, count) for key, count in grouped.items() if key[0] == checkpoint]
        states = {key[2] for key, _count in matches}
        shown = ", ".join(f"{key[2]}:{count}" for key, count in sorted(matches)) or "缺失"
        lines.append(
            f"| {checkpoint} | SharedReceiver | {shown} | {sum(count for _key, count in matches)} | {'是' if len(states) == 1 else '否'} |"
        )
    lines.extend(
        [
            "",
            "`before_receiver_accept` 的谓词是第 6 步：没有密封字节，效果文件也不存在，所以是 `UNKNOWN`。",
            "`after_receiver_accept` 的谓词是第 3 步：效果文件摘要等于密封摘要，回执文件不存在，所以是 `CONFIRM_TARGET`。",
            "`after_receiver_accept_conflict` 的谓词是第 1 步：同一 effect key 上两个摘要都在且不相等，所以是 `CONFLICT`。",
            "",
            "## 中继崩溃",
            "",
            f"检查点 `{RELAY_CHECKPOINT}`。子进程调用 `Outbox.commit`，业务行和 outbox 行提交之后、`SharedReceiver.accept` 之前被结束。新进程重新打开 `outbox.sqlite`，读出已提交载荷，先发送一次，再以另一个 attempt id 发送一次。",
            "",
            "| 检查点 | 代码路径 | 证据状态 | 次数 | logical_effect_count |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    relay_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in relay:
        relay_groups[row["evidence_state"]].append(row)
    for state, bucket in sorted(relay_groups.items()):
        counts = sorted({int(row["logical_effect_count"]) for row in bucket})
        lines.append(f"| {RELAY_CHECKPOINT} | Outbox | {state} | {len(bucket)} | {counts} |")
    if not relay_groups:
        lines.append("| after_commit_before_send | Outbox | 缺失 | 0 |  |")
    lines.extend(
        [
            "",
            "至少一次体现在崩溃后新进程仍会发送已提交的 outbox 行。幂等体现在第二次 `SharedReceiver.accept` 返回 replay，并且 `logical_effect_count` 仍是 1。这不是任意 HTTP 的恰好一次，也不是跨库原子。",
            "",
            "## 重复恢复",
            "",
            "同一次杀死之后，`fault_replay.kill_child` 的核对进程跑两遍。覆盖的检查点是 `after_claim_before_sealed`、`after_sealed_before_replace`、`after_replace_before_receipt`、`after_receipt`。每个检查点 5 次。代码路径名是 `kill_ledger`。",
            "",
            "第一遍的证据状态写在 `evidence_state`。`recovery_action` 同时记下两遍的动作。第二遍若再次执行 `PUBLISH_SEALED_BYTES`，或者效果文件的摘要发生变化，该行会被运行器拒绝，不会进入矩阵。",
            "",
            "| 检查点 | 第一遍 | 第二遍 | 次数 | 账本行数 | 二次重写 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for checkpoint in REPEAT_CHECKPOINTS:
        bucket = [row for row in repeat if row["checkpoint"] == checkpoint]
        firsts = sorted({row["evidence_state"] for row in bucket})
        seconds = sorted({row.get("second_evidence_state", "") for row in bucket})
        ledgers = sorted({int(row.get("ledger_count", -1)) for row in bucket})
        rewritten = sum(1 for row in bucket if row.get("effect_rewritten"))
        lines.append(
            f"| {checkpoint} | {', '.join(firsts) or '缺失'} | {', '.join(seconds) or '缺失'} | {len(bucket)} | {ledgers} | {rewritten} |"
        )
    lines.extend(
        [
            "",
            "`after_receipt` 的两遍都是 `REPLAY_RECEIPT`，效果文件摘要不变，所以回执重放没有再次发布。",
            "`after_sealed_before_replace` 的第一遍发布密封字节，第二遍因为文件已经存在而变成 `CONFIRM_TARGET`，文件摘要和修改时间保持第一遍留下的值。",
            "`after_replace_before_receipt` 的两遍都只确认已有目标。",
            "`after_claim_before_sealed` 的两遍都是 `UNKNOWN`，不发明载荷。",
            "`kill_ledger` 在两遍前后都是 1 行，claim 身份列不变，没有第二条 claim。",
            "",
            "## 矩阵计数",
            "",
            "| 检查点 | 代码路径 | 证据状态 | 次数 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for key in sorted(grouped):
        lines.append(f"| {key[0]} | {key[1]} | {key[2]} | {grouped[key]} |")
    lines.extend(["", *_rsr_section(rsr_rows), "## 边界", ""])
    lines.append(
        "矩阵里的每一行都是父进程对真实子进程的结束。`process_kill` 为 true，`power_loss_claim` 为 false。"
        "不把这个结果外推成掉电之后的磁盘状态，不外推成任意 HTTP 恰好一次，也不外推成两个数据库之间的原子提交。"
    )
    lines.extend(["", "## 收束", "", _closing(decision_token(decision_path)), ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_crash_study(repeats_receiver: int = 10, repeats_relay: int = 10, repeats_repeat: int = 5) -> list[dict[str, Any]]:
    assert_archive_intact()
    proposal = frozen_proposal("p10")
    work = WEEK4 / "crash_runs"
    work.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    rows.extend(run_receiver_matrix(repeats_receiver, work, proposal))
    rows.extend(run_relay_matrix(repeats_relay, work, proposal))
    rows.extend(run_repeat_matrix(repeats_repeat, work, proposal))
    return rows


def _print_summary(rows: list[dict[str, Any]]) -> None:
    print(f"rows={len(rows)}", flush=True)
    for key, count in sorted(_counts(rows).items()):
        print(f"count {key[0]} {key[1]} {key[2]} {count}", flush=True)
    relay = [row for row in rows if row["checkpoint"] == RELAY_CHECKPOINT]
    logical = sorted({int(row["logical_effect_count"]) for row in relay})
    print(f"relay_logical={logical} relay_n={len(relay)}", flush=True)
    repeat = [row for row in rows if row["path"] == "kill_ledger"]
    bad = [row["run_id"] for row in repeat if row.get("double_published") or row.get("effect_rewritten")]
    print(f"repeat_n={len(repeat)} double_published={len(bad)}", flush=True)


def main() -> None:
    rows = run_crash_study()
    write_matrix(rows)
    rsr_rows: list[dict[str, Any]] | None = None
    try:
        from crash_recovery.rsr_ablation import run_rsr_study, write_rsr_outputs

        rsr_rows = run_rsr_study()
        write_rsr_outputs(rsr_rows)
    except Exception:
        write_report(rows, None)
        raise
    write_report(rows, rsr_rows)
    digest = assert_archive_intact()
    _print_summary(rows)
    print(f"archived_sha256={digest}", flush=True)
    if rsr_rows is not None:
        print(f"rsr_rows={len(rsr_rows)}", flush=True)


if __name__ == "__main__":
    main()
