"""产品入口。研究仓库里的 ``epochgate`` 命令不受这个入口影响。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import typer

from epochgate_runtime.api import Runtime, timeline_lines
from epochgate_runtime.cli import load_project_env
from epochgate_runtime.demo import format_crash, format_stale_write, prepare_dirs, run_crash_recovery, run_stale_write
from epochgate_runtime.processes import pid_alive
from epochgate_runtime.report import doctor as run_doctor
from epochgate_runtime.service import ControlServer

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Run the EpochGate control plane.")
runs_app = typer.Typer(add_completion=False, no_args_is_help=True, help="List and inspect local runs.")
effects_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Check stored effect receipts.")
app.add_typer(runs_app, name="runs")
app.add_typer(effects_app, name="effects")

DEFAULT_CONFIG = {
    "schema_version": "1",
    "data_dir": ".epochgate",
    "host": "127.0.0.1",
    "port": "8765",
}


def load_config(path: Path) -> dict[str, str]:
    found = dict(DEFAULT_CONFIG)
    if not path.is_file():
        return found
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        found[key.strip()] = value.strip().strip('"').strip("'")
    return found


def data_dir_from(config_path: Path, override: str | None) -> Path:
    if override:
        return Path(override)
    config = load_config(config_path)
    return Path(config["data_dir"])


def open_runtime(config_path: Path, override: str | None) -> Runtime:
    root = data_dir_from(config_path, override)
    prepare_dirs(root)
    return Runtime.open(root / "epochgate.db", data_dir=root)


@app.callback()
def _main() -> None:
    """读取仓库根的 .env，不覆盖已经存在的环境变量。"""
    load_project_env()


@app.command("init")
def init(config: str = typer.Option("epochgate.yaml", "--config")) -> None:
    """Write a local config and create the data directory."""
    path = Path(config)
    settings = load_config(path)
    if path.exists():
        typer.echo(f"config exists: {path}")
    else:
        lines = [
            "# EpochGate local control plane. This file is safe to commit only with local paths.",
            "schema_version: 1",
            f"data_dir: {settings['data_dir']}",
            f"host: {settings['host']}",
            f"port: {settings['port']}",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        typer.echo(f"wrote {path}")
    root = Path(settings["data_dir"])
    prepare_dirs(root)
    runtime = Runtime.open(root / "epochgate.db", data_dir=root)
    version = runtime.ledger.schema_version()
    runtime.close()
    typer.echo(f"data_dir={root} schema_version={version}")


@app.command("doctor")
def doctor(
    config: str = typer.Option("epochgate.yaml", "--config"),
    data_dir: str | None = typer.Option(None, "--data-dir"),
) -> None:
    """Check Python, the ledger, DSH, the plugin, and Docker."""
    report = run_doctor()
    root = data_dir_from(Path(config), data_dir)
    prepare_dirs(root)
    destination = root / "doctor.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checks = report["checks"]
    for name in (
        "python_ok",
        "ledger_ok",
        "workspace_writable",
        "node_ok",
        "dsh_bin_ok",
        "plugin_ok",
        "plugin_syntax_ok",
        "docker_ok",
        "container_linux",
    ):
        typer.echo(f"{name}={checks[name]}")
    typer.echo("bind_mount_configured=false")
    typer.echo("live_container_probe=false")
    typer.echo(f"status={report['status']} report={destination}")
    if report["status"] == "blocked":
        raise typer.Exit(code=1)


@app.command("serve")
def serve(
    config: str = typer.Option("epochgate.yaml", "--config"),
    data_dir: str | None = typer.Option(None, "--data-dir"),
) -> None:
    """Start the control service and keep its ledger across restarts."""
    path = Path(config)
    settings = load_config(path)
    root = data_dir_from(path, data_dir)
    prepare_dirs(root)
    marker = root / "server.json"
    if marker.is_file():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        pid = int(previous.get("pid") or 0)
        if previous.get("status") == "running" and pid_alive(pid):
            typer.echo(f"already running: {previous.get('base_url')}")
            raise typer.Exit(code=1)
    runtime = Runtime.open(root / "epochgate.db", data_dir=root)
    host = settings["host"]
    port = int(settings["port"])
    try:
        server = ControlServer(runtime.ledger, runtime.sink, runtime.workspace, host=host, port=port)
    except OSError as exc:
        runtime.close()
        typer.echo(f"cannot bind {host}:{port}: {exc}")
        raise typer.Exit(code=1) from exc
    record = {"pid": os.getpid(), "base_url": server.base_url, "status": "running", "data_dir": str(root)}
    marker.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    log = root / "logs" / "serve.log"
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"start {server.base_url} pid={os.getpid()}\n")
    typer.echo(f"serving {server.base_url}")
    typer.echo(f"data_dir={root}")
    typer.echo(f"log={log}")
    try:
        server.httpd.serve_forever()
    except KeyboardInterrupt:
        typer.echo("stopping")
    finally:
        server.httpd.server_close()
        record["status"] = "stopped"
        marker.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        runtime.close()


@app.command("demo")
def demo(
    scenario: str = typer.Option("stale-write", "--scenario", help="stale-write, crash-recovery, or coding"),
    offline: bool = typer.Option(False, "--offline", help="Run without a model API key."),
    model: str = typer.Option("deepseek-flash", "--model"),
    config: str = typer.Option("epochgate.yaml", "--config"),
    data_dir: str | None = typer.Option(None, "--data-dir"),
) -> None:
    """Run one scenario. Offline scenarios do not call a model."""
    root = data_dir_from(Path(config), data_dir)
    if scenario in {"stale-write", "crash-recovery"} and not offline:
        typer.echo("this scenario is offline. Re-run with --offline.")
        raise typer.Exit(code=2)
    if scenario == "stale-write":
        report = run_stale_write(root)
        typer.echo(format_stale_write(report))
        if not report["verified"]:
            raise typer.Exit(code=1)
        return
    if scenario == "crash-recovery":
        report = run_crash_recovery(root)
        typer.echo(format_crash(report))
        if not report["verified"]:
            raise typer.Exit(code=1)
        return
    if scenario == "coding":
        if offline:
            typer.echo("coding uses DeepSeek. Omit --offline.")
            raise typer.Exit(code=2)
        _run_coding(root, model)
        return
    typer.echo("known scenarios: stale-write, crash-recovery, coding")
    raise typer.Exit(code=2)


def _run_coding(root: Path, model: str) -> None:
    marker = root / "server.json"
    if not marker.is_file():
        typer.echo("start the control service first: egctl serve")
        raise typer.Exit(code=2)
    record = json.loads(marker.read_text(encoding="utf-8"))
    if record.get("status") != "running" or not pid_alive(int(record.get("pid") or 0)):
        typer.echo("control service is not running: egctl serve")
        raise typer.Exit(code=2)
    key = os.environ.get("DS_API_KEY") or os.environ.get("EPOCHGATE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key:
        typer.echo("missing DeepSeek key in the environment")
        raise typer.Exit(code=2)
    repo = None
    here = Path.cwd()
    for parent in [here, *here.parents]:
        if (parent / "scripts" / "runtime_coding_run.py").is_file():
            repo = parent
            break
    if repo is None:
        typer.echo("coding demo runs from an EpochGate checkout. The script is scripts/runtime_coding_run.py.")
        raise typer.Exit(code=2)
    out = root / "runs" / "coding-live"
    out.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["EG_USE_EXISTING_CONTROL"] = "1"
    env["EG_CONTROL_URL"] = str(record["base_url"])
    env["EG_CODING_OUT"] = str(out)
    env["EG_CODING_MODEL"] = model
    env.pop("EPOCHGATE_API_KEY", None)
    env.pop("DS_API_KEY", None)
    completed = subprocess.run([sys.executable, str(repo / "scripts" / "runtime_coding_run.py")], cwd=str(repo), env=env, check=False)
    if completed.returncode != 0:
        raise typer.Exit(code=completed.returncode)


@runs_app.command("list")
def runs_list(
    config: str = typer.Option("epochgate.yaml", "--config"),
    data_dir: str | None = typer.Option(None, "--data-dir"),
) -> None:
    """List runs in the local ledger."""
    runtime = open_runtime(Path(config), data_dir)
    try:
        rows = runtime.list_runs()
    finally:
        runtime.close()
    if not rows:
        typer.echo("no runs")
        return
    for row in rows:
        typer.echo(f"{row['run_id']}  phase={row['phase']}  token={row['fencing_token']}  eligible={str(row['eligible']).lower()}")


@runs_app.command("inspect")
def runs_inspect(
    run_id: str = typer.Argument(...),
    config: str = typer.Option("epochgate.yaml", "--config"),
    data_dir: str | None = typer.Option(None, "--data-dir"),
    report: bool = typer.Option(True, "--report/--no-report"),
) -> None:
    """Print the event timeline for one run."""
    from epochgate_runtime.api import write_report

    root = data_dir_from(Path(config), data_dir)
    runtime = open_runtime(Path(config), data_dir)
    try:
        try:
            snapshot = runtime.inspect(run_id)
        except KeyError:
            typer.echo(f"missing run {run_id}")
            raise typer.Exit(code=1)
    finally:
        runtime.close()
    typer.echo("\n".join(timeline_lines(snapshot)))
    if report:
        paths = write_report(root / "runs" / run_id, snapshot)
        typer.echo(f"Report: {paths['json']}")
        typer.echo(f"HTML: {paths['html']}")


@effects_app.command("verify")
def effects_verify(
    run_id: str = typer.Argument(...),
    config: str = typer.Option("epochgate.yaml", "--config"),
    data_dir: str | None = typer.Option(None, "--data-dir"),
) -> None:
    """Show whether a run has a stored receipt."""
    runtime = open_runtime(Path(config), data_dir)
    try:
        try:
            result = runtime.verify(run_id)
        except KeyError:
            typer.echo(f"missing run {run_id}")
            raise typer.Exit(code=1)
    finally:
        runtime.close()
    typer.echo(f"verified={str(result['verified']).lower()}")
    for receipt in result["receipts"]:
        typer.echo(f"receipt={receipt}")
    if not result["verified"]:
        raise typer.Exit(code=1)
