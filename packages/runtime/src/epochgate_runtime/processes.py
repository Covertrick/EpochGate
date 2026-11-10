"""真实进程的取消和槽位。槽位释放只在进程已经退出之后记录。"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from epochgate_runtime.ledger import RunLedger


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in completed.stdout
    completed = subprocess.run(["ps", "-p", str(pid)], capture_output=True, check=False)
    return completed.returncode == 0


def start_sleeper(seconds: float = 30) -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])


def start_parent_and_child(pid_file: Path) -> subprocess.Popen[bytes]:
    code = (
        "import pathlib, subprocess, sys, time\n"
        "flags = 0x00000008 | 0x00000200 if sys.platform == 'win32' else 0\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], creationflags=flags)\n"
        f"pathlib.Path(r'''{pid_file}''').write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    return subprocess.Popen([sys.executable, "-c", code])


def cancel_process(ledger: RunLedger, run_id: str, process: subprocess.Popen[bytes]) -> dict[str, Any]:
    """先撤销资格，再发停止，最后用进程是否退出决定槽位。"""
    started = time.perf_counter()
    ledger.revoke(run_id)
    requested = ledger.append_event(run_id, "cancel_requested", {"pid": process.pid})
    ledger.append_event(run_id, "stop_signal_sent", {"pid": process.pid, "signal": "terminate_process", "ack_kind": "process"}, causation_id=requested)
    process.terminate()
    killed = False
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        killed = True
        process.wait(timeout=3)
        ledger.append_event(run_id, "stop_signal_sent", {"pid": process.pid, "signal": "kill_process"})
    exited = process.poll() is not None
    ledger.append_event(run_id, "process_exited" if exited else "process_still_running", {"pid": process.pid, "returncode": process.poll(), "killed": killed})
    if exited:
        ledger.signal_executor(run_id, acked=True)
        ledger.observe_slot(run_id, free=True)
    else:
        ledger.signal_executor(run_id, acked=False)
        ledger.observe_slot(run_id, free=False)
    state = ledger.get_run(run_id)
    return {
        "exited": exited,
        "killed": killed,
        "slot_released": state.cancel.slot_released,
        "gpu_reclaimed": state.cancel.gpu_reclaimed,
        "resource_observation": "not_observable",
        "cancel_to_exit_seconds": time.perf_counter() - started if exited else None,
    }


def wait_for_pid_file(path: Path, timeout: float = 5) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return int(path.read_text(encoding="utf-8").strip())
        time.sleep(0.05)
    raise TimeoutError(str(path))


def reconcile_dead_workers(ledger: RunLedger) -> list[str]:
    """失联只在进程已经不存在时释放租约。还活着的进程保持占槽。"""
    released: list[str] = []
    for lease in ledger.held_leases():
        pid = lease["pid"]
        if pid and not pid_alive(int(pid)):
            ledger.release_lease(lease["lease_id"])
            ledger.append_event(lease["run_id"], "worker_reconciled", {"lease_id": lease["lease_id"], "pid": pid})
            released.append(lease["lease_id"])
    return released


def stop_pid(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
        return
    subprocess.run(["kill", "-9", str(pid)], check=False)
