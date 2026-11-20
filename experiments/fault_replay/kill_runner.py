"""Parent: start a real child, hard-kill it, reconcile in a new process.

Windows uses TerminateProcess. This is controlled process-crash injection.
It is not power-loss durability and it is not a failure probability.
"""

from __future__ import annotations

import csv
import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from frozen_proposals.hashing import sha256_bytes, sha256_file
from frozen_proposals.predicate import legal_payload
from fault_replay.contract import (
    RECOVERY_PATHS,
    STUDY_CHECKPOINTS,
    WEEK2,
    WEEK3,
)

MATRIX_FIELDS = [
    "run_id",
    "checkpoint",
    "path",
    "killed_pid",
    "post_kill_state",
    "recovery_action",
    "evidence_state",
    "target_digest",
    "sealed_digest",
    "receipt_present",
    "semantic_oracle_result",
    "proposal_id",
    "payload_sha256",
    "world_before_hash",
    "world_after_hash",
    "authority_initial_hash",
    "proposal_contract_match",
    "exit_code",
    "reconciler_pid",
    "memory_sentinel_seen",
    "process_kill",
    "power_loss_claim",
]


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.TerminateProcess.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.GetExitCodeProcess.restype = ctypes.c_int
    return kernel


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel = _kernel32()
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_uint32()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        if not ok:
            return False
        return code.value == 259
    try:
        os_kill = __import__("os").kill
        os_kill(pid, 0)
    except OSError:
        return False
    return True


def hard_kill(pid: int) -> int:
    """Kill so the child cannot catch it. Windows: TerminateProcess. Unix: SIGKILL.

    The venv executable can be a launcher whose pid differs from the interpreter
    that wrote the marker. Callers pass the marker pid: that is the process
    whose memory holds the payload.
    """
    if sys.platform != "win32":
        import os
        import signal

        os.kill(pid, signal.SIGKILL)
        return -9
    kernel = _kernel32()
    handle = kernel.OpenProcess(0x0001 | 0x0400, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenProcess failed for pid {pid}")
    ok = kernel.TerminateProcess(handle, 1)
    error = ctypes.get_last_error()
    if not ok:
        kernel.CloseHandle(handle)
        raise OSError(error, f"TerminateProcess failed for pid {pid}")
    code = ctypes.c_uint32(259)
    for _ in range(50):
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        if code.value != 259:
            break
        time.sleep(0.02)
    kernel.CloseHandle(handle)
    return int(code.value)


def load_week2_registry() -> list[dict[str, Any]]:
    rows = []
    for line in (WEEK2 / "proposal_registry.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def resolve_payload_path(proposal: dict[str, Any]) -> Path:
    raw = Path(proposal["payload_path"])
    if raw.is_absolute():
        return raw
    base = WEEK3 if proposal.get("source") == "week3" else WEEK2
    return base / raw


def frozen_proposal(proposal_id: str) -> dict[str, Any]:
    for row in load_week2_registry():
        if row["proposal_id"] == proposal_id:
            return row
    raise KeyError(proposal_id)


def _wait_marker(path: Path, proc: subprocess.Popen, timeout: float = 15.0) -> dict[str, Any]:
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


def _reconcile(run_dir: Path) -> dict[str, Any]:
    log_path = run_dir / "reconcile.log"
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "fault_replay.kill_child", "--role", "reconcile", "--run-dir", str(run_dir)],
            cwd=str(WEEK3.parent),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        code = proc.wait(timeout=30)
    if code != 0:
        raise RuntimeError(f"reconcile failed: {log_path.read_text(encoding='utf-8')}")
    return json.loads((run_dir / "recovery_result.json").read_text(encoding="utf-8"))


def launch_and_kill(
    run_dir: Path,
    *,
    proposal: dict[str, Any],
    path: str,
    checkpoint: str,
    run_id: str,
    scoring_world: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start the child, wait for the checkpoint marker, TerminateProcess."""
    run_dir.mkdir(parents=True, exist_ok=True)
    payload_path = resolve_payload_path(proposal)
    payload = payload_path.read_bytes()
    payload_sha = sha256_bytes(payload)
    if payload_sha != proposal["payload_sha256"]:
        raise RuntimeError(f"{proposal['proposal_id']} payload hash drifted before kill")
    world_after = scoring_world if scoring_world is not None else proposal["world_after"]
    from frozen_proposals.hashing import sha256_obj

    contract = {
        "run_id": run_id,
        "path": path,
        "checkpoint": checkpoint,
        "proposal_id": proposal["proposal_id"],
        "payload_sha256": payload_sha,
        "world_before_hash": proposal["world_before_hash"],
        "world_after_hash": sha256_obj(world_after),
        "world_after": world_after,
        "authority_initial_hash": proposal["authority_initial_hash"],
        "authority_token": int(proposal["authority_token"]),
        "authority_epoch": int(proposal["authority_epoch"]),
        "effect_key": proposal["idempotency_key"],
    }
    (run_dir / "contract.json").write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    log_path = run_dir / "child.log"
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "fault_replay.kill_child",
                "--role",
                "child",
                "--run-dir",
                str(run_dir),
                "--proposal-bin",
                str(payload_path),
            ],
            cwd=str(WEEK3.parent),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        marker = _wait_marker(run_dir / "marker.json", proc)
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
    if pid_alive(killed_pid):
        raise RuntimeError(f"pid {killed_pid} still alive after TerminateProcess")
    if sha256_file(payload_path) != proposal["payload_sha256"]:
        raise RuntimeError("proposal file changed during the kill")
    return {
        "killed_pid": killed_pid,
        "exit_code": exit_code,
        "marker": marker,
        "payload_path": str(payload_path),
    }


def kill_once(
    run_dir: Path,
    *,
    proposal: dict[str, Any],
    path: str,
    checkpoint: str,
    run_id: str,
    scoring_world: dict[str, Any] | None = None,
) -> dict[str, Any]:
    launched = launch_and_kill(
        run_dir,
        proposal=proposal,
        path=path,
        checkpoint=checkpoint,
        run_id=run_id,
        scoring_world=scoring_world,
    )
    recovered = _reconcile(run_dir)
    file_sha = sha256_file(resolve_payload_path(proposal))
    contract_match = (
        recovered["proposal_id"] == proposal["proposal_id"]
        and recovered["payload_sha256"] == proposal["payload_sha256"]
        and file_sha == proposal["payload_sha256"]
        and recovered["world_before_hash"] == proposal["world_before_hash"]
        and recovered["authority_initial_hash"] == proposal["authority_initial_hash"]
    )
    # When the caller supplies a drifted scoring world, world_after_hash is that
    # world, not the Week 2 registry world. The registry match stays on the
    # frozen payload, the initial world, and the initial authority.
    if scoring_world is None:
        contract_match = contract_match and recovered["world_after_hash"] == proposal["world_after_hash"]
    row = {
        "run_id": run_id,
        "checkpoint": checkpoint,
        "path": path,
        "killed_pid": launched["killed_pid"],
        "post_kill_state": recovered["post_kill_state"],
        "recovery_action": recovered["recovery_action"],
        "evidence_state": recovered["evidence_state"],
        "target_digest": recovered["target_digest"],
        "sealed_digest": recovered["sealed_digest"],
        "receipt_present": recovered["receipt_present"],
        "semantic_oracle_result": recovered["semantic_oracle_result"],
        "proposal_id": proposal["proposal_id"],
        "payload_sha256": proposal["payload_sha256"],
        "world_before_hash": proposal["world_before_hash"],
        "world_after_hash": recovered["world_after_hash"],
        "authority_initial_hash": proposal["authority_initial_hash"],
        "proposal_contract_match": contract_match,
        "exit_code": launched["exit_code"],
        "reconciler_pid": recovered["reconciler_pid"],
        "memory_sentinel_seen": recovered["memory_sentinel_seen"],
        "process_kill": True,
        "power_loss_claim": False,
        "restored_bytes": recovered["restored_bytes"],
        "physical_present": recovered["physical_present"],
        "ledger_readable": recovered["ledger_readable"],
    }
    return row


def _study_assignments() -> list[tuple[str, str]]:
    """Same proposal for both paths at a checkpoint. Two frozen proposals are reused."""
    assigned = []
    for checkpoint in STUDY_CHECKPOINTS[:2]:
        assigned.append((checkpoint, "p10"))
    for checkpoint in STUDY_CHECKPOINTS[2:]:
        assigned.append((checkpoint, "p11"))
    return assigned


def run_matrix(repeats: int = 10, work_root: Path | None = None) -> list[dict[str, Any]]:
    root = work_root or (WEEK3 / "kill_runs")
    root.mkdir(parents=True, exist_ok=True)
    cache = {proposal_id: frozen_proposal(proposal_id) for proposal_id in ("p10", "p11")}
    for proposal in cache.values():
        blob = resolve_payload_path(proposal).read_bytes()
        if sha256_bytes(blob) != proposal["payload_sha256"]:
            raise RuntimeError(f"frozen bytes drifted: {proposal['proposal_id']}")
        if blob != legal_payload(proposal["world_before"]):
            raise RuntimeError(f"frozen bytes are not predicate.fulfillment.v1: {proposal['proposal_id']}")
    rows: list[dict[str, Any]] = []
    for checkpoint, proposal_id in _study_assignments():
        proposal = cache[proposal_id]
        for path in RECOVERY_PATHS:
            for repeat in range(1, repeats + 1):
                run_id = f"w3-kill-{proposal_id}-{path}-{checkpoint}-r{repeat:02d}"
                row = kill_once(
                    root / run_id,
                    proposal=proposal,
                    path=path,
                    checkpoint=checkpoint,
                    run_id=run_id,
                )
                rows.append(row)
                print(f"{len(rows)} {run_id} {row['evidence_state']}", flush=True)
    return rows


def write_matrix(rows: list[dict[str, Any]], path: Path | None = None) -> Path:
    target = path or (WEEK3 / "crash_matrix.csv")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATRIX_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            exported = dict(row)
            exported["receipt_present"] = str(bool(row["receipt_present"])).lower()
            exported["proposal_contract_match"] = str(bool(row["proposal_contract_match"])).lower()
            exported["memory_sentinel_seen"] = str(bool(row["memory_sentinel_seen"])).lower()
            exported["process_kill"] = "true"
            exported["power_loss_claim"] = "false"
            writer.writerow(exported)
    return target


def _stable_groups(rows: list[dict[str, Any]]) -> dict[tuple[str, str], set[str]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        key = (row["checkpoint"], row["path"])
        grouped.setdefault(key, set()).add(row["evidence_state"])
    return grouped


def write_summary(rows: list[dict[str, Any]], path: Path | None = None) -> Path:
    target = path or (WEEK3 / "crash_evidence_summary.md")
    groups = _stable_groups(rows)
    stable = {key: next(iter(states)) for key, states in groups.items() if len(states) == 1}
    distinct = sorted({state for state in stable.values()})
    proposals = sorted({row["proposal_id"] for row in rows})
    matched = sum(1 for row in rows if row["proposal_contract_match"] is True or row["proposal_contract_match"] == "true")
    lines = [
        "# Week 3 进程崩溃注入",
        "",
        "这是受控的进程崩溃注入：父进程确认检查点标记后，用 Windows `TerminateProcess` 结束子进程。子进程内存随之消失，另一个新进程重新打开 SQLite 和文件系统再做核对。",
        "",
        "这不是掉电耐久性。这不是失败概率。矩阵里的重复次数是预先固定的重复，不是从随机崩溃里估计出来的比率。",
        "",
        "Week 2 里标成 CRASHED 的格子是进程内故障 `IN_PROCESS_FAULT`，不是这里的操作系统级结束，也不是掉电。本矩阵的 `process_kill` 为 true，`power_loss_claim` 为 false。不声称任意 HTTP 恰好一次，不声称跨库原子性。",
        "",
        f"- 行数：{len(rows)}。检查点 4，路径 2（Claim、Outbox），每格重复 {len(rows) // 8 if len(rows) else 0} 次。",
        f"- 复用的冻结提案：{', '.join(proposals)}。字节文件没有重写。提案合同匹配行数：{matched} / {len(rows)}。",
        f"- 稳定复现的证据状态：{', '.join(distinct) if distinct else '无'}。共 {len(distinct)} 个。",
        "- Claim 与 Outbox 使用同一个子进程代码路径、同一张 `kill_ledger`、同一套接收端文件条件。路径名只是列标签。恢复不读取子进程内存里的载荷。",
        "",
        "## 每个检查点的稳定状态",
        "",
        "| 检查点 | 路径 | 证据状态 | 是否单一 |",
        "| --- | --- | --- | --- |",
    ]
    for checkpoint, path in ((cp, pth) for cp in STUDY_CHECKPOINTS for pth in RECOVERY_PATHS):
        states = groups.get((checkpoint, path), set())
        shown = ", ".join(sorted(states)) if states else "缺失"
        lines.append(f"| {checkpoint} | {path} | {shown} | {'是' if len(states) == 1 else '否'} |")
    lines.extend(
        [
            "",
            "## 证据状态的含义",
            "",
            "- `UNKNOWN`：只有未密封的 claim，没有可发布的字节。核对不发明载荷。",
            "- `PUBLISH_SEALED`：密封字节已落盘，目标文件还没有。新进程从 SQLite 读出密封字节再发布。",
            "- `CONFIRM_TARGET`：目标文件的摘要已经等于密封摘要，回执还没有。新进程只确认，不重写。",
            "- `REPLAY_RECEIPT`：回执和目标都已落盘且摘要一致。新进程重放回执，不再发布一次。",
            "- `CONFLICT`：密封摘要与目标摘要同时存在且不相等。本研究的四个一致检查点不把 CONFLICT 当作稳定结果；状态本身仍由核对器实现。",
            "",
            "语义 oracle 使用 Week 2 的 `semantics_oracle` 1.0.0 和 `predicate.fulfillment.v1`，评分世界是该提案冻结的 `world_after`。p10 与 p11 的崩溃日程不改变世界，所以发布后的语义结果是 VALID；未发布时物理字节缺失，语义结果是 UNKNOWN。UNKNOWN 不是 FAILURE。",
            "",
        ]
    )
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def main() -> None:
    rows = run_matrix(10)
    write_matrix(rows)
    write_summary(rows)
    distinct = {row["evidence_state"] for row in rows}
    print(f"rows={len(rows)} states={sorted(distinct)}")


if __name__ == "__main__":
    main()
