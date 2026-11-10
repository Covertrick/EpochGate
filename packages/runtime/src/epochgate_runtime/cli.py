"""执行控制面命令。不导入研究包 epochgate。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, no_args_is_help=True, help="execution control")


def load_project_env(path: Path | None = None) -> None:
    """把仓库根的 ``.env`` 读进当前进程，不覆盖已经设置的变量。"""
    env_path = path
    if env_path is None:
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / ".env"
            if candidate.is_file() and (parent / "packages" / "runtime").is_dir():
                env_path = candidate
                break
    if env_path is None or not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def _write(out: str, report: dict) -> Path:
    destination = Path(out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


@app.callback()
def _main() -> None:
    """启动时读取仓库根的 ``.env``。"""
    load_project_env()


@app.command("doctor")
def doctor(out: str = typer.Option("results/runtime/doctor.json", "--out")) -> None:
    """Check Python, Node, DSH, the ledger, and Docker."""
    from epochgate_runtime.report import doctor as run_doctor

    report = run_doctor()
    destination = _write(out, report)
    typer.echo(f"status={report['status']} report={destination}")
    if report["status"] == "blocked":
        raise typer.Exit(code=1)


@app.command("chaos")
def chaos(out: str = typer.Option("results/runtime/chaos.json", "--out")) -> None:
    """Inject process crashes and a real cancellation."""
    from epochgate_runtime.report import run_chaos

    directory = Path("results/runtime/chaos") / os.urandom(4).hex()
    directory.mkdir(parents=True, exist_ok=True)
    report = run_chaos(directory)
    destination = _write(out, report)
    typer.echo(f"passed={report['passed']} report={destination}")
    if not report["passed"]:
        raise typer.Exit(code=1)


@app.command("bench")
def bench(out: str = typer.Option("results/runtime/benchmarks/w0.json", "--out")) -> None:
    """Run the W0 real-process workload."""
    from epochgate_runtime.report import run_w0

    report = run_w0()
    destination = _write(out, report)
    typer.echo(f"workload={report['workload']} report={destination}")


@app.command("scale")
def scale(out: str = typer.Option("results/runtime/benchmarks/w0_scale.json", "--out")) -> None:
    """Run W0 at concurrency 1, 2, 4, and 8."""
    from epochgate_runtime.report import run_w0_scale

    report = run_w0_scale()
    destination = _write(out, report)
    typer.echo(f"workload={report['workload']} report={destination}")


@app.command("inspect")
def inspect(
    run_id: str = typer.Option(..., "--run"),
    db: str = typer.Option(..., "--db"),
) -> None:
    """Answer why a run was rejected, which effect repeated, and which slot is held."""
    from epochgate_runtime.ledger import RunLedger

    ledger = RunLedger(db)
    state = ledger.get_run(run_id)
    events = ledger.events(run_id)
    rejected = [item for item in events if item["event_type"] in {"commit_rejected", "admission_rejected", "result_withheld"}]
    published = [item for item in events if item["event_type"] == "file_published"]
    exited = [item for item in events if item["event_type"] == "process_exited"]
    unknown = [item for item in events if item["event_type"] in {"outcome_unknown", "result_withheld"}]
    typer.echo(f"phase={state.phase} eligible={state.eligible} token={state.fencing_token}")
    typer.echo("rejected=" + (rejected[-1]["event_type"] if rejected else "none"))
    typer.echo(f"published={len(published)}")
    slots = ",".join(item["worker_id"] for item in ledger.held_leases()) or "none"
    typer.echo(f"held_slots={slots}")
    typer.echo("process_exited=" + ("yes" if exited else "no"))
    typer.echo("unknown=" + (unknown[-1]["event_type"] if unknown else "none"))
    ledger.close()


@app.command("verify")
def verify(
    run_id: str = typer.Option(..., "--run"),
    db: str = typer.Option(..., "--db"),
) -> None:
    """Print the authoritative events for one run."""
    from epochgate_runtime.ledger import RunLedger

    ledger = RunLedger(db)
    state = ledger.get_run(run_id)
    typer.echo(f"phase={state.phase} eligible={state.eligible} token={state.fencing_token} gpu_reclaimed={state.cancel.gpu_reclaimed}")
    for item in ledger.events(run_id):
        typer.echo(f"{item['seq']} {item['event_type']}")
    ledger.close()


def execute_check(out: str) -> None:
    from epochgate_runtime.f2p import recorded_12907
    from epochgate_runtime.ledger import run_acceptance

    report = run_acceptance()
    report["f2p_12907"] = recorded_12907()
    destination = _write(out, report)
    for item in report["checks"]:
        typer.echo(f"{item['name']}: {'pass' if item['passed'] else 'fail'} {item['detail']}")
    typer.echo(f"f2p_repair_demonstrated={report['f2p_12907']['repair_demonstrated']}")
    typer.echo(f"report={destination}")
    if not report["passed"]:
        raise typer.Exit(code=1)


@app.command("check")
def check(out: str = typer.Option("results/runtime/check.json", "--out")) -> None:
    """Run the deterministic control-plane checks."""
    execute_check(out)


def mount(parent: typer.Typer) -> None:
    """把同一组命令挂到研究仓库的 ``epochgate`` 入口上。"""
    parent.add_typer(app, name="runtime")

    @parent.command("runtime-check")
    def runtime_check(out: str = typer.Option("results/runtime/check.json", "--out")) -> None:
        """Run the deterministic control-plane checks."""
        execute_check(out)
