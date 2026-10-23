"""Typer CLI entrypoint: ``epochgate demo``."""

from __future__ import annotations

import asyncio
import time

import typer

from epochgate.backend import FakeBackend
from epochgate.environment import FakeEnvironment
from epochgate.models import Task, TaskStatus
from epochgate.runtime import Runtime

app = typer.Typer(add_completion=False, no_args_is_help=True, help="EpochGate research runtime CLI")


@app.callback()
def _root() -> None:
    """EpochGate research runtime CLI."""


@app.command("demo")
def demo(qty: int = typer.Option(1, help="Reserve quantity for the fake action")) -> None:
    """Run one happy-path task against FakeEnvironment + FakeBackend."""

    async def _run() -> None:
        now = time.monotonic()
        runtime = Runtime(
            environment=FakeEnvironment(stock=5),
            backend=FakeBackend(qty=qty),
        )
        task = Task(
            task_id="task-demo-1",
            arrival_mono=now,
            deadline_mono=now + 60.0,
            goal="reserve inventory item",
        )
        result = await runtime.run_task(task)
        typer.echo(f"run_id={runtime.run_id}")
        typer.echo(f"status={result.status.value}")
        typer.echo(f"plan_epoch={result.plan_epoch} obs_revision={result.obs_revision}")
        typer.echo(f"env_stock={runtime.environment.stock}")  # type: ignore[attr-defined]
        for event in runtime.events.iter_events(run_id=runtime.run_id):
            typer.echo(f"  [{event.seq}] {event.event_type}: {event.reason or ''}")
        if result.status != TaskStatus.SUCCEEDED:
            raise typer.Exit(code=1)

    asyncio.run(_run())


if __name__ == "__main__":
    app()
