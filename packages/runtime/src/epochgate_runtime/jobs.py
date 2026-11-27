"""进程内任务。长操作立刻返回 job id，调用方再来取结果。"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any


class JobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, Any] = {}

    def submit(self, work: Callable[[], dict[str, Any]], *, run_id: str | None = None) -> dict[str, Any]:
        job_id = "job-" + uuid.uuid4().hex[:12]
        stamp = datetime.now(timezone.utc).isoformat()
        record = {"job_id": job_id, "status": "queued", "run_id": run_id, "error": None, "result": None,
                  "queued_at": stamp, "updated_at": stamp}
        with self._lock:
            self._jobs[job_id] = record

        def run() -> None:
            self._update(job_id, status="running")
            try:
                result = work()
            except Exception as exc:
                self._update(job_id, status="failed", error=str(exc))
                return
            produced = result.get("run_id") if isinstance(result, dict) else None
            self._update(job_id, status="succeeded", result=result, run_id=produced or run_id)

        threading.Thread(target=run, daemon=True).start()
        return {"job_id": job_id, "status": "queued", "run_id": run_id}

    def busy(self) -> bool:
        with self._lock:
            return any(item["status"] in {"queued", "running"} for item in self._jobs.values())

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._jobs.get(job_id)
            return None if found is None else dict(found)

    def attach_process(self, run_id: str, process: Any) -> None:
        with self._lock:
            self._processes[run_id] = process

    def process_for(self, run_id: str) -> Any | None:
        with self._lock:
            return self._processes.get(run_id)

    def drop_process(self, run_id: str) -> Any | None:
        with self._lock:
            return self._processes.pop(run_id, None)

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            current = self._jobs[job_id]
            current.update(fields)
            current["updated_at"] = datetime.now(timezone.utc).isoformat()
