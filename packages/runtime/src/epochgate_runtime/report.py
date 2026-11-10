"""doctor?????? W0 ???????????????"""

from __future__ import annotations

import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from epochgate_runtime import __version__
from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.processes import cancel_process, start_sleeper
from epochgate_runtime.recovery import reconcile
from epochgate_runtime.sandbox import docker_command
from epochgate_runtime.sink import EffectSink


def repo_root() -> Path | None:
    """仓库根。单独安装控制面时没有研究仓库，返回 None。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "infrastructure" / "dsh-profiles").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    return None


ROOT = repo_root()


def doctor() -> dict[str, Any]:
    import tempfile

    root = ROOT
    dsh = (root / "infrastructure" / "dsh-profiles" / "research" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js") if root else Path("missing")
    plugin = (root / "packages" / "eg-workspace" / "lib" / "index.js") if root else Path("missing")
    client = (root / "packages" / "eg-workspace" / "lib" / "control-client.js") if root else Path("missing")
    node = shutil.which("node")
    plugin_syntax = False
    if node and client.is_file():
        checked = subprocess.run([node, "--check", str(client)], capture_output=True, text=True, check=False, timeout=20)
        plugin_syntax = checked.returncode == 0
    docker_stdout = ""
    docker_stderr = ""
    docker_ok = False
    try:
        docker = subprocess.run(
            docker_command(["info"]),
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        docker_ok = docker.returncode == 0
        docker_stdout = docker.stdout
        docker_stderr = docker.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        docker_stderr = str(exc)
    probe_dir = (root / "results" / "runtime") if root else Path(tempfile.mkdtemp(prefix="eg-doctor-"))
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe = probe_dir / "doctor-ledger.sqlite"
    if probe.exists():
        probe.unlink()
    ledger = RunLedger(probe)
    ledger.create_run("doctor")
    schema_ok = ledger.schema_version() == "1"
    ledger.close()
    workspace_writable = False
    try:
        probe_file = probe_dir / "doctor-write-probe"
        probe_file.write_text("ok", encoding="utf-8")
        workspace_writable = probe_file.read_text(encoding="utf-8") == "ok"
        probe_file.unlink()
    except OSError:
        workspace_writable = False
    checks = {
        "python_ok": sys.version_info >= (3, 12),
        "node_ok": node is not None,
        "dsh_bin_ok": dsh.is_file(),
        "plugin_ok": plugin.is_file() and client.is_file(),
        "plugin_syntax_ok": plugin_syntax,
        "ledger_ok": probe.is_file() and schema_ok,
        "schema_version": "1" if schema_ok else "unknown",
        "workspace_writable": workspace_writable,
        "docker_ok": docker_ok,
        "container_linux": "OSType: linux" in docker_stdout,
        "bind_mount_configured": False,
    }
    core = checks["python_ok"] and checks["ledger_ok"] and checks["workspace_writable"]
    full = core and checks["node_ok"] and checks["dsh_bin_ok"] and checks["plugin_ok"] and checks["plugin_syntax_ok"] and checks["docker_ok"] and checks["container_linux"]
    if not core:
        status = "blocked"
    elif full:
        status = "ready"
    else:
        status = "degraded"
    return {
        "status": status,
        "checks": checks,
        "docker_stderr_tail": docker_stderr[-400:],
        "sandbox_profile": {
            "network": "none",
            "bind_mount": False,
            "publisher_uid": "1001",
            "live_container_probe": False,
        },
        "package": "epochgate-runtime",
        "package_version": __version__,
        "runtime_line": "3.0",
    }


def run_chaos(directory: Path) -> dict[str, Any]:
    rows = []
    for index, fault in enumerate(("before_dispatch", "before_sink", "after_sink", "after_receipt")):
        db = directory / f"{fault}.sqlite"
        sink_path = directory / f"{fault}-sink.sqlite"
        run_id = f"chaos-{index}"
        ledger = RunLedger(db)
        ledger.create_run(run_id)
        ledger.advance(run_id, "running")
        state = ledger.get_run(run_id)
        ledger.close()
        completed = subprocess.run(
            [
                sys.executable, "-m", "epochgate_runtime.worker",
                "--db", str(db), "--sink", str(sink_path), "--fault", fault,
                "--run", run_id, "--key", "k", "--token", str(state.fencing_token), "--version", str(state.state_version),
            ],
            check=False,
        )
        recovered = RunLedger(db)
        sink = EffectSink(sink_path)
        decision = reconcile(recovered, sink, run_id=run_id, idempotency_key="k", fencing_token=state.fencing_token)
        rows.append({
            "fault": fault,
            "exit_code": completed.returncode,
            "decision": decision["decision"],
            "sink_accepts": sink.accepted_count("k"),
        })
        recovered.close()
        sink.close()
    cancel_ledger = RunLedger(directory / "cancel.sqlite")
    cancel_ledger.create_run("cancel")
    cancel_ledger.advance("cancel", "running")
    process = start_sleeper(30)
    cancelled = cancel_process(cancel_ledger, "cancel", process)
    rows.append({
        "fault": "cancel_process",
        "decision": "exited" if cancelled["exited"] else "running",
        "slot_released": cancelled["slot_released"],
        "gpu_reclaimed": cancelled["gpu_reclaimed"],
        "cancel_to_exit_seconds": cancelled["cancel_to_exit_seconds"],
    })
    cancel_ledger.close()
    faults_ok = all(row["exit_code"] == 86 for row in rows if row["fault"] != "cancel_process")
    return {"passed": faults_ok and rows[-1]["slot_released"] is True and rows[-1]["gpu_reclaimed"] is False, "rows": rows}


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def run_w0(*, repeats: int = 3, concurrency: int = 2, sleep_s: float = 0.05) -> dict[str, Any]:
    arms = []
    for arm in ("B0", "B1", "B2"):
        samples = []
        for _ in range(repeats):
            started = time.perf_counter()
            if arm == "B0":
                processes = [subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({sleep_s})"]) for _ in range(concurrency)]
                for process in processes:
                    process.wait(timeout=30)
            else:
                ledger = RunLedger()
                sink = EffectSink()
                processes = []
                for index in range(concurrency):
                    run_id = f"{arm}-{index}-{len(samples)}"
                    ledger.create_run(run_id)
                    ledger.advance(run_id, "running")
                    state = ledger.get_run(run_id)
                    if arm == "B2":
                        ledger.commit_authorized(
                            run_id,
                            idempotency_key=f"k-{index}",
                            fencing_token=state.fencing_token,
                            state_version=state.state_version,
                            sink=sink,
                            payload="w0",
                        )
                    processes.append(subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({sleep_s})"]))
                for process in processes:
                    process.wait(timeout=30)
                ledger.close()
                sink.close()
            samples.append(time.perf_counter() - started)
        arms.append({"arm": arm, "samples_seconds": samples, "median_seconds": _median(samples)})
    return {
        "workload": "W0_real_process",
        "concurrency": concurrency,
        "repeats": repeats,
        "sleep_seconds": sleep_s,
        "arms": arms,
        "measurement": "local process sleep plus control-plane work; not a model or GPU measurement",
    }


def run_w0_scale() -> dict[str, Any]:
    """同一睡眠负载在并发 1、2、4、8 上各重复 3 次。这不是在线 Agent。"""
    levels = [run_w0(repeats=3, concurrency=concurrency, sleep_s=0.05) for concurrency in (1, 2, 4, 8)]
    return {
        "workload": "W0_scale",
        "levels": levels,
        "measurement": "real local processes; not online agents, not a GPU measurement",
    }
