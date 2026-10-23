"""Standalone demo script using the in-tree FakeEnvironment.

Run from repo root after ``python -m uv sync --extra dev``::

    python -m uv run python examples/fake_dynamic_env.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# Allow running without install when PYTHONPATH is unset.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from epochgate.backend import FakeBackend
from epochgate.environment import FakeEnvironment
from epochgate.models import Task, TaskStatus
from epochgate.runtime import Runtime


async def main() -> None:
    env = FakeEnvironment(stock=3)
    runtime = Runtime(environment=env, backend=FakeBackend(qty=1))
    now = time.monotonic()
    task = await runtime.run_task(
        Task(
            task_id="ex-1",
            arrival_mono=now,
            deadline_mono=now + 30.0,
            goal="book one unit from shared inventory",
        )
    )
    print(f"status={task.status.value} stock_left={env.stock}")
    assert task.status == TaskStatus.SUCCEEDED
    assert env.stock == 2


if __name__ == "__main__":
    asyncio.run(main())
