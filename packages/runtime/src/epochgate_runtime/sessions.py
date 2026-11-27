"""一次绑定仓库的会话。发布和命令都进 Linux 容器，权威状态留在账本。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from epochgate_runtime.agent_loop import api_key, redact, run_agent_turn
from epochgate_runtime.api import Runtime
from epochgate_runtime.jobs import JobRunner
from epochgate_runtime.observations import observe_run
from epochgate_runtime.plugin_patch import dsh_binary, find_repo, write_plugin_patch
from epochgate_runtime.processes import cancel_process
from epochgate_runtime.recovery import reconcile
from epochgate_runtime.sandbox import (
    docker_available,
    exec_in,
    lock_workspace,
    publish_container,
    read_file,
    start_container,
    stop_container,
    write_file,
)

_SKIP_DIRS = {".git", ".epochgate", "node_modules", "__pycache__", ".venv", "gold"}
_MAX_BYTES = 1_000_000


class SessionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _agent_failure(result: dict[str, Any]) -> str:
    names = (
        ("initialize_error", "初始化"),
        ("prompt_error", "提问"),
        ("wait_error", "等待"),
    )
    for key, label in names:
        if result.get(key):
            return f"模型这一轮没有完成，停在{label}。"
    return ""


def missing_requirements(repo: Path | None) -> list[str]:
    missing: list[str] = []
    if not docker_available():
        missing.append("docker")
    if not api_key():
        missing.append("api_key")
    if repo is None or not dsh_binary(repo).is_file():
        missing.append("dsh")
    if repo is None or not (repo / "packages" / "eg-workspace" / "lib" / "index.js").is_file():
        missing.append("plugins")
    return missing


def requirement_message(missing: list[str], *, docker_state: str = "") -> str:
    labels = {
        "docker": "Docker",
        "api_key": "DeepSeek 密钥",
        "dsh": "DSH",
        "plugins": "EpochGate 插件",
    }
    if "docker" in missing and docker_state == "denied":
        others = [labels.get(item, item) for item in missing if item != "docker"]
        lead = "Docker 已经在运行。当前进程打不开引擎管道，没有再次启动。"
        if not others:
            return lead
        return lead + "另外还缺" + "、".join(others) + "。"
    names = "、".join(labels.get(item, item) for item in missing)
    return f"还不能开始这次任务，缺的是{names}。"


class SessionStore:
    def __init__(self, runtime: Runtime, data_dir: Path, jobs: JobRunner, *, repo: Path | None, control_url: str, token: str) -> None:
        self.runtime = runtime
        self.data_dir = data_dir
        self.jobs = jobs
        self.repo = repo
        self.control_url = control_url
        self.token = token
        (data_dir / "sessions").mkdir(parents=True, exist_ok=True)

    def create(self, workspace: str, task: str, test_command: str = "", *, title: str = "", timeout: int = 180) -> dict[str, Any]:
        root = Path(workspace)
        if not root.is_dir():
            raise SessionError("workspace", "仓库目录不存在。")
        try:
            git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5)
            commit = git.stdout.strip() if git.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            commit = None
        missing = missing_requirements(self.repo)
        if missing:
            raise SessionError("requirements", requirement_message(missing))
        assert self.repo is not None
        session_id = "sess-" + uuid.uuid4().hex[:12]
        container = "eg-sess-" + session_id[-8:]
        start_container(container)
        try:
            skipped: list[str] = []
            baseline: dict[str, str] = {}
            for relative, content in _iter_text(root):
                write_file(container, relative, content)
                baseline[relative] = hashlib.sha256(content.encode("utf-8")).hexdigest()
            skipped = _skipped(root)
            lock_workspace(container)
            ledger = self.runtime.ledger
            ledger.create_run(session_id)
            ledger.append_event(session_id, "run_created", {"container": container, "task": task, "title": title,
                                                            "repository": root.resolve().name, "git_commit": commit})
            ledger.advance(session_id, "running")
            record = {
                "session_id": session_id,
                "run_id": session_id,
                "workspace": str(root),
                "task": task,
                "test_command": test_command,
                "title": title,
                "timeout": timeout,
                "container": container,
                "baseline": baseline,
                "skipped": skipped,
            }
            self._write(record)
            patch = write_plugin_patch(self.repo, self.data_dir / "dsh" / "cordis.patch.yml")
            job = self.jobs.submit(lambda: self._turn(record, patch, task), run_id=session_id)
        except Exception:
            stop_container(container)
            raise
        view = self.get(session_id)
        view["job_id"] = job["job_id"]
        return view

    def get(self, session_id: str) -> dict[str, Any]:
        record = self._read(session_id)
        view = self._public_run(session_id)
        view.update({
            "session_id": session_id,
            "task": record.get("task", ""),
            "title": record.get("title", ""),
            "workspace": record.get("workspace", ""),
            "container": record.get("container", ""),
            "skipped": record.get("skipped") or [],
            "diff": self._diff(record),
        })
        return view

    def list_sessions(self) -> list[dict[str, Any]]:
        directory = self.data_dir / "sessions"
        rows = []
        for path in sorted(directory.glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            try:
                state = self.runtime.ledger.get_run(record["run_id"])
            except KeyError:
                continue
            events = self.runtime.ledger.events(record["run_id"])
            rows.append({
                "session_id": record["session_id"],
                "run_id": record["run_id"],
                "task": record.get("task", ""),
                "phase": state.phase,
                "fencing_token": state.fencing_token,
                "eligible": state.eligible,
                "last_event": events[-1]["event_type"] if events else "",
            })
        return rows

    def publish(self, session_id: str, path: str, content: str) -> dict[str, Any]:
        record = self._read(session_id)
        if _refused(path):
            raise SessionError("path", "这个路径不能发布。")
        state = self.runtime.ledger.get_run(session_id)
        key = hashlib.sha256(f"{session_id}:{path}".encode("utf-8")).hexdigest()
        staging = f"/scratch/.eg-staging/{key}"
        _stage(record["container"], staging, content.encode("utf-8"))
        result = publish_container(
            self.runtime.ledger,
            container=record["container"],
            run_id=session_id,
            idempotency_key=key,
            fencing_token=state.fencing_token,
            relative_path=path,
            staging_path=staging,
        )
        if result.get("status") in {"conflict", "replayed"}:
            self.runtime.ledger.append_event(
                session_id,
                "publish_outcome",
                {"status": result.get("status"), "unchanged": True, "idempotency_key": key},
            )
        return result

    def command(self, session_id: str, command: str) -> dict[str, Any]:
        record = self._read(session_id)
        state = self.runtime.ledger.get_run(session_id)
        key = "cmd-" + uuid.uuid4().hex[:12]
        reserved = self.runtime.ledger.record_intent(
            session_id,
            key,
            state.fencing_token,
            state.state_version,
            effect_id=key,
            arguments_hash=hashlib.sha256(command.encode("utf-8")).hexdigest(),
            tool_name="eg_bash",
        )
        if not reserved.get("allowed"):
            raise SessionError("refused", "当前资格不能启动这条命令。")
        started = self.runtime.ledger.start_command(session_id, key, state.fencing_token)
        if not started.get("allowed"):
            raise SessionError("refused", "命令没有进入已开始状态。")
        log_path = self.data_dir / "logs" / f"{key}.log"

        def work() -> dict[str, Any]:
            process = subprocess.Popen(
                _command_argv(record["container"], command),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.jobs.attach_process(session_id, process)
            stdout, stderr = process.communicate()
            text = redact((stdout + stderr).decode("utf-8", "replace"))
            log_path.write_text(text, encoding="utf-8")
            code = process.returncode
            self.runtime.ledger.finish_command(session_id, key, state.fencing_token, code)
            self.jobs.drop_process(session_id)
            return {"run_id": session_id, "effect_id": key, "exit_code": code}

        job = self.jobs.submit(work, run_id=session_id)
        job["effect_id"] = key
        return job

    def cancel(self, session_id: str) -> dict[str, Any]:
        record = self._read(session_id)
        process = self.jobs.drop_process(session_id)
        if process is not None and process.poll() is None:
            result = cancel_process(self.runtime.ledger, session_id, process)
        else:
            state = self.runtime.ledger.revoke_with_sink(session_id, self.runtime.sink)
            result = {"phase": state.phase, "eligible": state.eligible, "slot_released": state.cancel.slot_released}
        stop_container(record["container"])
        return result

    def reconcile_session(self, session_id: str) -> dict[str, Any]:
        self._read(session_id)
        state = self.runtime.ledger.get_run(session_id)
        results = []
        for effect in self.runtime.ledger.effects_for_run(session_id):
            if effect["phase"] == "completed" and effect["receipt"]:
                continue
            decision = reconcile(
                self.runtime.ledger,
                self.runtime.sink,
                run_id=session_id,
                idempotency_key=effect["idempotency_key"],
                fencing_token=state.fencing_token,
            )
            self.runtime.ledger.append_event(
                session_id,
                "reconcile_decision",
                {"decision": decision["decision"], "idempotency_key": effect["idempotency_key"]},
            )
            results.append(decision)
        return {"run_id": session_id, "results": results}

    def resume(self, session_id: str) -> dict[str, Any]:
        record = self._read(session_id)
        self.reconcile_session(session_id)
        state = self.runtime.ledger.get_run(session_id)
        if not (state.phase == "running" and state.eligible):
            view = self.get(session_id)
            view["resumed"] = False
            return view
        self.runtime.ledger.supersede(session_id, self.runtime.sink)
        assert self.repo is not None
        patch = write_plugin_patch(self.repo, self.data_dir / "dsh" / "cordis.patch.yml")
        unknown = [
            item["idempotency_key"]
            for item in self.runtime.ledger.effects_for_run(session_id)
            if item["effect_state"] == "OUTCOME_UNKNOWN"
        ]
        note = record["task"] + "\n\nAlready published:\n" + self._diff(record) + "\nUnknown effects: " + ", ".join(unknown)
        job = self.jobs.submit(lambda: self._turn(record, patch, note), run_id=session_id)
        view = self.get(session_id)
        view["resumed"] = True
        view["job_id"] = job["job_id"]
        return view

    def _turn(self, record: dict[str, Any], patch: Path, task: str) -> dict[str, Any]:
        assert self.repo is not None
        started = time.perf_counter()
        status = "failed"
        try:
            result = run_agent_turn(
                repo=self.repo,
                patch=patch,
                control_url=self.control_url,
                run_id=record["run_id"],
                container=record["container"],
                task=task,
                token=self.token,
                on_process=lambda process: self.jobs.attach_process(record["run_id"], process),
                timeout=record.get("timeout", 180),
            )
            failure = _agent_failure(result)
            if failure:
                raise RuntimeError(failure)
            status = "succeeded"
        finally:
            self.jobs.drop_process(record["run_id"])
            self.runtime.ledger.append_event(record["run_id"], "agent_turn_finished", {
                "status": status,
                "duration_seconds": time.perf_counter() - started,
            })
        command = record.get("test_command") or ""
        if command:
            self.command(record["session_id"], command)
        return result

    def _public_run(self, run_id: str) -> dict[str, Any]:
        view = {
            "run_id": run_id,
            "phase": self.runtime.ledger.get_run(run_id).phase,
            "eligible": self.runtime.ledger.get_run(run_id).eligible,
            "fencing_token": self.runtime.ledger.get_run(run_id).fencing_token,
            "state_version": self.runtime.ledger.get_run(run_id).state_version,
            "slot_released": self.runtime.ledger.get_run(run_id).cancel.slot_released,
            "events": self.runtime.ledger.events(run_id),
            "effects": self.runtime.ledger.effects_for_run(run_id),
            "observations": observe_run(self.runtime.ledger, run_id),
        }
        return view

    def _diff(self, record: dict[str, Any]) -> str:
        lines: list[str] = []
        baseline: dict[str, str] = record.get("baseline") or {}
        for relative, digest in list(baseline.items())[:40]:
            try:
                current = read_file(record["container"], relative)
            except RuntimeError:
                lines.append(f"{relative} 现在读不到")
                continue
            now = hashlib.sha256(current.encode("utf-8")).hexdigest()
            if now == digest:
                continue
            snippet = current[:800]
            lines.append(f"{relative}\nsha256 {now}\n{snippet}")
        return "\n\n".join(lines) if lines else "发布树相对基线没有可读的文本差异。"

    def _path(self, session_id: str) -> Path:
        return self.data_dir / "sessions" / f"{session_id}.json"

    def _write(self, record: dict[str, Any]) -> None:
        self._path(record["session_id"]).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _read(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        if not path.is_file():
            raise SessionError("missing", "没有这次会话。")
        return json.loads(path.read_text(encoding="utf-8"))


def _command_argv(container: str, command: str) -> list[str]:
    from epochgate_runtime.sandbox import docker_command

    return docker_command(["exec", "-i", "-u", "1000:1000", "-w", "/workspace", container, "sh", "-c", command])


def _stage(container: str, path: str, payload: bytes) -> None:
    code = "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(sys.stdin.buffer.read())"
    result = exec_in(container, ["python", "-c", code, path], data=payload)
    if result.returncode != 0:
        raise SessionError("stage", "暂存文件没有写进容器。")


def _refused(path: str) -> bool:
    parts = Path(path).parts
    return path.startswith("/") or ".." in parts or ".env" in parts or "gold" in parts


def _iter_text(root: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if name == ".env" or path.stat().st_size > _MAX_BYTES:
                continue
            relative = path.relative_to(root).as_posix()
            if _refused(relative):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            found.append((relative, text))
    return found


def _skipped(root: Path) -> list[str]:
    names: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in list(dirnames):
            if name in _SKIP_DIRS:
                names.append(str(Path(dirpath, name).relative_to(root)))
        for name in filenames:
            path = Path(dirpath) / name
            if name == ".env" or path.stat().st_size > _MAX_BYTES:
                names.append(str(path.relative_to(root)))
    return names[:80]
