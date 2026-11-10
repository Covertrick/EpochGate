"""单机有界调度。上限按真实子进程计数，不把脚本时间当成队列定律。"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from typing import Any

from epochgate_runtime.ledger import RunLedger


class ProcessScheduler:
    def __init__(self, ledger: RunLedger, *, capacity: int, queue_limit: int) -> None:
        self.ledger = ledger
        self.capacity = capacity
        self.queue_limit = queue_limit
        self._lock = threading.Lock()
        self._running: dict[str, subprocess.Popen[bytes]] = {}
        self._queue: list[dict[str, Any]] = []
        self.rejected = 0
        self.completed = 0
        self.max_active = 0
        self.waits: list[float] = []

    def submit(self, run_id: str, *, sleep_s: float = 0.2, deadline_at: float | None = None) -> str:
        item = {"run_id": run_id, "sleep_s": sleep_s, "deadline_at": deadline_at, "enqueued_at": time.perf_counter()}
        with self._lock:
            if len(self._running) < self.capacity:
                return self._start_locked(item)
            if len(self._queue) < self.queue_limit:
                self._queue.append(item)
                return "queued"
            self.rejected += 1
            self.ledger.append_event(run_id, "admission_rejected", {"reason": "queue_full"})
            return "rejected"

    def wait(self) -> None:
        while True:
            with self._lock:
                running = list(self._running.items())
                queued = len(self._queue)
            if not running and queued == 0:
                return
            for run_id, process in running:
                if process.poll() is not None:
                    self._finish(run_id, process)
            time.sleep(0.01)

    def active_pids(self) -> list[int]:
        with self._lock:
            return [process.pid for process in self._running.values() if process.poll() is None]

    def summary(self) -> dict[str, Any]:
        ordered = sorted(self.waits)
        def pick(quantile: float) -> float:
            if not ordered:
                return 0.0
            index = min(len(ordered) - 1, int(round(quantile * (len(ordered) - 1))))
            return ordered[index]
        return {
            "completed": self.completed,
            "rejected": self.rejected,
            "max_active_processes": self.max_active,
            "wait_p50_seconds": pick(0.50),
            "wait_p95_seconds": pick(0.95),
            "samples": len(ordered),
            "workload": "real_process",
        }

    def _start_locked(self, item: dict[str, Any]) -> None:
        now = time.perf_counter()
        if item["deadline_at"] is not None and now > float(item["deadline_at"]):
            self.rejected += 1
            self.ledger.append_event(item["run_id"], "admission_rejected", {"reason": "deadline"})
            return "rejected"
        self.waits.append(now - float(item["enqueued_at"]))
        status = "running"
        process = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({float(item['sleep_s'])})"])
        self._running[item["run_id"]] = process
        self.max_active = max(self.max_active, len(self._running))
        lease_id = "lease-" + uuid.uuid4().hex[:8]
        self.ledger.grant_lease(lease_id, item["run_id"], f"pid-{process.pid}", "tool_executor", process.pid)
        item["lease_id"] = lease_id
        self.ledger.append_event(item["run_id"], "executor_started", {"pid": process.pid, "lease_id": lease_id})
        item_store = getattr(self, "_items", {})
        item_store[item["run_id"]] = item
        self._items = item_store
        return status

    def _finish(self, run_id: str, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._running.pop(run_id, None)
            item = getattr(self, "_items", {}).pop(run_id, None)
            self.completed += 1
            if item and item.get("lease_id"):
                self.ledger.release_lease(item["lease_id"])
            self.ledger.append_event(run_id, "process_exited", {"pid": process.pid, "returncode": process.returncode})
            self.ledger.append_event(run_id, "slot_released", {"proof": "process_poll", "pid": process.pid})
            if self._queue and len(self._running) < self.capacity:
                self._start_locked(self._queue.pop(0))


class ClassScheduler:
    """Run、模型请求、工具执行各用自己的槽。占满一类不表示另一类也满了。"""

    def __init__(self, ledger: RunLedger, capacities: dict[str, int]) -> None:
        self.ledger = ledger
        self.capacities = capacities
        self._held: dict[str, list[str]] = {name: [] for name in capacities}

    def acquire(self, run_id: str, resource_class: str) -> dict[str, Any]:
        if resource_class not in self.capacities:
            raise KeyError(resource_class)
        if len(self._held[resource_class]) >= self.capacities[resource_class]:
            self.ledger.append_event(run_id, "admission_rejected", {"reason": "class_full", "resource_class": resource_class})
            return {"status": "rejected", "resource_class": resource_class}
        lease_id = "lease-" + uuid.uuid4().hex[:8]
        self.ledger.grant_lease(lease_id, run_id, lease_id, resource_class, None)
        self._held[resource_class].append(lease_id)
        return {"status": "held", "lease_id": lease_id, "resource_class": resource_class}

    def release(self, resource_class: str, lease_id: str) -> None:
        self.ledger.release_lease(lease_id)
        self._held[resource_class].remove(lease_id)
