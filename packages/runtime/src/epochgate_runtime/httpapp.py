"""给页面和插件共用的控制服务。协议仍由 dispatch_control 执行。"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from epochgate_runtime.actions import run_walkthrough
from epochgate_runtime.agent_loop import api_key, redact
from epochgate_runtime.api import EVENT_LABELS, Runtime
from epochgate_runtime.demo import (
    run_corrected_effect,
    run_crash_recovery,
    run_idempotency_conflict,
    run_stale_write,
    run_title,
)
from epochgate_runtime.figures import phase_label, write_run_figures
from epochgate_runtime.jobs import JobRunner
from epochgate_runtime.observations import observe_run
from epochgate_runtime.observatory import ResearchArchive, evidence, summarize, workspace_preview
from epochgate_runtime.product import LaunchRequests, audit_record, cell_timeline, csv_bytes, research_export
from epochgate_runtime.plugin_patch import find_repo
from epochgate_runtime.processes import cancel_process, start_sleeper
from epochgate_runtime.sandbox import docker_available, docker_reason, ensure_docker, stop_container
from epochgate_runtime.service import dispatch_control
from epochgate_runtime.sessions import SessionError, SessionStore, missing_requirements, requirement_message

_CONSOLE = Path(__file__).resolve().parent / "console"
_PUBLIC = {"/", "/health", "/app.css", "/app.js", "/docs", "/openapi.json", "/redoc", "/session-token"}
_ACTIONS = {
    "stale-write": run_stale_write,
    "crash-window": run_crash_recovery,
    "idempotency-conflict": run_idempotency_conflict,
    "corrected-effect": run_corrected_effect,
}


class ControlBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_id: str | None = None
    session_id: str | None = None
    idempotency_key: str | None = None
    fencing_token: int | None = None
    state_version: int | None = None


class SessionBody(BaseModel):
    workspace: str
    task: str
    test_command: str = ""
    title: str = Field(default="", max_length=120)
    timeout: int = Field(default=180, ge=1, le=3600)


class WorkspaceBody(BaseModel):
    workspace: str


class PatchBody(BaseModel):
    path: str
    content: str


class CommandBody(BaseModel):
    command: str = Field(min_length=1)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message


def create_app(runtime: Runtime, data_dir: Path, token: str, *, repo: Path | None = None, control_url: str = "http://127.0.0.1:8765", demo_mode: bool = False) -> FastAPI:
    app = FastAPI(title="EpochGate", version="0.1.0", description="执行控制面。页面和 DSH 插件调用同一组路径。")
    jobs = JobRunner()
    action_locks = {name: threading.Lock() for name in _ACTIONS}
    app.state.runtime = runtime
    app.state.data_dir = data_dir
    app.state.token = token
    app.state.jobs = jobs
    app.state.sessions = SessionStore(runtime, data_dir, jobs, repo=repo if repo is not None else find_repo(), control_url=control_url, token=token)
    started_at = datetime.now(timezone.utc).isoformat()
    archive = ResearchArchive(app.state.sessions.repo)
    launches = LaunchRequests(data_dir / "launch-requests.sqlite")
    try:
        runtime_version = version("epochgate-runtime")
    except PackageNotFoundError:
        runtime_version = app.version

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(_request: Request, _exc: sqlite3.Error) -> JSONResponse:
        return JSONResponse({"code": "storage_unavailable", "message": "账本暂时不可用。请稍后重新读取。"}, status_code=503)

    @app.exception_handler(SessionError)
    async def session_error(_request: Request, exc: SessionError) -> JSONResponse:
        status = 404 if exc.code == "missing" else 409 if exc.code == "requirements" else 400
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=status)

    @app.middleware("http")
    async def gate(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        path = request.url.path
        if path not in _PUBLIC and not path.startswith("/docs"):
            header = request.headers.get("authorization", "")
            if header != f"Bearer {token}":
                return JSONResponse(
                    {"code": "unauthorized", "message": "需要本机写令牌。"},
                    status_code=401,
                    headers={"X-Request-Id": request_id},
                )
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        _log(data_dir, request_id, request.method, path)
        return response

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "authority": "run_ledger", "mode": "demo" if demo_mode else "local"}

    @app.get("/session-token")
    def session_token(request: Request) -> dict[str, str]:
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "testclient"}:
            raise ApiError(401, "unauthorized", "写令牌只发给本机页面。")
        return {"token": token}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_CONSOLE / "index.html")

    @app.get("/app.css")
    def css() -> FileResponse:
        return FileResponse(_CONSOLE / "app.css")

    @app.get("/app.js")
    def javascript() -> FileResponse:
        return FileResponse(_CONSOLE / "app.js")

    @app.get("/runs")
    def list_runs() -> dict[str, Any]:
        rows = []
        for row in runtime.ledger.list_runs():
            events = runtime.ledger.events(row["run_id"])
            row = dict(row)
            row["last_event"] = events[-1]["event_type"] if events else ""
            row["last_seq"] = int(events[-1]["seq"]) if events else 0
            row["title"] = run_title(row["run_id"])
            row["phase_label"] = phase_label(str(row["phase"]))
            effects = runtime.ledger.effects_for_run(row["run_id"])
            audit = evidence(runtime, row["run_id"], events, effects)
            row["attempt_count"] = len(audit["attempts"])
            row.update(summarize(row, events, effects, audit["receipts"]))
            rows.append(row)
        rows.sort(key=lambda item: item["last_seq"], reverse=True)
        return {"runs": rows}

    @app.get("/observatory/runs/export")
    def export_runs(ids: str = "") -> Response:
        selection = set(ids.split(",")) - {""}
        rows = [row for row in list_runs()["runs"] if not selection or row["run_id"] in selection]
        return Response(csv_bytes(rows), media_type="text/csv")

    @app.get("/observatory/search")
    def search(q: str, start: str = "", end: str = "") -> dict[str, Any]:
        query = q.strip().casefold()
        if not query:
            return {"matches": []}
        matches = []
        for row in list_runs()["runs"]:
            stamp = row["created_at"] or ""
            if (start and stamp[:10] < start) or (end and (not stamp or stamp[:10] > end)):
                continue
            run_id = row["run_id"]
            snapshot = _present_run(runtime, run_id, figures=False)
            candidates = [("run", run_id), ("commit", row["git_commit"])]
            candidates.extend(("attempt", item["attempt_id"]) for item in snapshot["attempts"])
            candidates.extend(("effect", item.get("effect_id") or item["idempotency_key"]) for item in snapshot["effects"])
            candidates.extend(("receipt", item["receipt_id"]) for item in snapshot["receipts"])
            matches.extend({"type": kind, "id": value, "run_id": run_id}
                           for kind, value in candidates if value and query in value.casefold())
        if not (start or end):
            try:
                matches.extend({"type": "experiment", "id": row["id"]}
                               for row in archive.data()["manifest"]["cases"] if query in row["id"].casefold())
            except FileNotFoundError:
                pass
        return {"matches": matches[:60]}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, figures: bool = True) -> dict[str, Any]:
        return _present_run(runtime, run_id, figures=figures)

    @app.get("/runs/{run_id}/events")
    def run_events(run_id: str, after: int = 0) -> StreamingResponse:
        _present_run(runtime, run_id, figures=False)
        return StreamingResponse(_stream(runtime, run_id, after), media_type="text/event-stream")

    @app.post("/actions/{name}")
    def start_action(name: str) -> dict[str, Any]:
        if name == "unknown-outcome":
            def unknown_demo() -> dict[str, Any]:
                ledger = runtime.ledger
                run_id = "unknown-" + uuid.uuid4().hex[:12]
                ledger.create_run(run_id)
                ledger.append_event(run_id, "run_created", {"scenario": name})
                state = ledger.advance(run_id, "running")
                key = run_id + ":effect"
                result = ledger.seal_unconfirmed_external(run_id, key, state.fencing_token, state.state_version)
                return {"run_id": run_id, **result}
            return jobs.submit(unknown_demo)
        if name == "cancellation":
            def cancel_demo() -> dict[str, Any]:
                started = time.perf_counter()
                run_id = "cancel-" + uuid.uuid4().hex[:12]
                runtime.ledger.create_run(run_id)
                runtime.ledger.append_event(run_id, "run_created", {"scenario": "cancellation"})
                runtime.ledger.advance(run_id, "running")
                with start_sleeper() as process:
                    try:
                        result = cancel_process(runtime.ledger, run_id, process)
                    finally:
                        if process.poll() is None:
                            process.kill()
                runtime.ledger.append_event(run_id, "demo_finished", {"scenario": name, "status": "succeeded", "duration_seconds": time.perf_counter() - started})
                return {"run_id": run_id, **result}

            return jobs.submit(cancel_demo)
        if name == "walkthrough":
            if not docker_available():
                ensure_docker()
            if not docker_available():
                state = docker_reason()
                raise ApiError(409, "requirements", requirement_message(["docker"], docker_state=state))
            job = jobs.submit(lambda: run_walkthrough(runtime, data_dir))
            return job
        function = _ACTIONS.get(name)
        if function is None:
            raise ApiError(404, "missing", "没有这个操作。")

        def checked() -> dict[str, Any]:
            with action_locks[name]:
                started = time.perf_counter()
                report = function(data_dir)
                if report.get("run_id"):
                    runtime.ledger.append_event(report["run_id"], "demo_finished", {
                        "scenario": name, "status": "failed" if report.get("verified") is False else "succeeded",
                        "duration_seconds": time.perf_counter() - started,
                    })
            if report.get("verified") is False:
                raise RuntimeError("这次样板没有对上账本。")
            return report

        return jobs.submit(checked)

    @app.get("/examples")
    def examples() -> dict[str, Any]:
        repo = app.state.sessions.repo
        missing = missing_requirements(repo)
        docker_state = docker_reason() if "docker" in missing else ""
        sample = (repo / "examples" / "sample-task") if repo is not None else None
        tasks = []
        if sample is not None and sample.is_dir():
            files = []
            for path in sorted(sample.iterdir()):
                if not path.is_file() or path.stat().st_size > 20_000:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                files.append({"name": path.name, "text": text})
            tasks.append({
                "id": "greet",
                "title": "问候样板",
                "workspace": str(sample),
                "task": "把 note.py 里的 greet 改成返回 hello, 加上名字。只改 note.py。",
                "test_command": "python -m unittest test_note.py",
                "files": files,
            })
        return {
            "ready": not missing,
            "missing": missing,
            "docker": docker_state,
            "message": "可以开始真实任务。" if not missing else requirement_message(missing, docker_state=docker_state),
            "tasks": tasks,
        }

    @app.post("/observatory/workspace")
    def inspect_workspace(body: WorkspaceBody) -> dict[str, Any]:
        try:
            return workspace_preview(body.workspace)
        except (OSError, ValueError) as exc:
            raise ApiError(400, "workspace", str(exc)) from exc

    @app.post("/observatory/preflight")
    def preflight(body: SessionBody) -> dict[str, Any]:
        workspace = inspect_workspace(WorkspaceBody(workspace=body.workspace))
        requirements = examples()
        return {"ready": requirements["ready"] and bool(body.task.strip()), "missing": requirements["missing"],
                "message": requirements["message"] if body.task.strip() else "请填写任务描述。",
                "workspace": workspace["workspace"], "working_directory": "/workspace",
                "runner": "DSH · DeepSeek Flash", "timeout": body.timeout,
                "policy": {"执行资格": "REQUIRED", "Fencing Token": "REQUIRED", "幂等提交": "受控发布",
                           "回执核验": "容器文件发布读回", "崩溃恢复": "按副作用类型对账"}}

    @app.get("/observatory/system")
    def system() -> dict[str, Any]:
        start = time.perf_counter()
        engine = docker_available()
        docker_ms = round((time.perf_counter() - start) * 1000, 2)
        with jobs._lock:
            counts = {state: sum(job["status"] == state for job in jobs._jobs.values())
                      for state in ("queued", "running", "failed", "succeeded")}
            processes = list(jobs._processes)
            recent = [{key: task[key] for key in ("job_id", "run_id", "status")} for task in list(jobs._jobs.values())[-10:]]
        sqlite_start = time.perf_counter()
        with runtime.ledger._lock:
            db = runtime.ledger._conn
            storage_path = db.execute("PRAGMA database_list").fetchone()[2]
            journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]
            schema = runtime.ledger.schema_version()
            transaction = db.in_transaction
        with runtime.sink._lock:
            sink_path = runtime.sink._conn.execute("PRAGMA database_list").fetchone()[2]
        def storage_info(path):
            file = Path(path)
            return {"name": file.name if path else ":memory:",
                    "bytes": file.stat().st_size if path and file.is_file() else None}
        return {"checked_at": datetime.now(timezone.utc).isoformat(), "started_at": started_at,
                "uptime_seconds": (datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds(),
                "runtime": runtime_version, "python": sys.version.split()[0], "control_plane": "AVAILABLE",
                "sqlite": {"status": "CONNECTED", "version": sqlite3.sqlite_version, "schema": schema, "journal": journal_mode,
                           "check_ms": round((time.perf_counter() - sqlite_start) * 1000, 2), "transaction": transaction},
                "storage": {"ledger": storage_info(storage_path), "sink": storage_info(sink_path)},
                "docker": {"status": "AVAILABLE" if engine else docker_reason(), "check_ms": docker_ms},
                "worker": {"status": "IN_PROCESS", "jobs": counts, "attached_runs": processes, "recent": recent,
                           "held_leases": len(runtime.ledger.held_leases())},
                "model": {"status": "CONFIGURED" if api_key() else "NOT_CONFIGURED", "connectivity": "NOT_OBSERVED"}}

    @app.get("/observatory/system/log")
    def system_log() -> dict[str, str]:
        path = data_dir / "logs" / "serve.log"
        if not path.is_file():
            return {"text": ""}
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 32_768))
            return {"text": redact(handle.read(32_768).decode("utf-8", errors="replace"))}

    @app.get("/observatory/research")
    def research() -> dict[str, Any]:
        return research_read(archive.overview)

    @app.post("/observatory/research/audit")
    def audit_research() -> dict[str, Any]:
        return research_read(lambda: audit_record(archive, data_dir / "research-audits"))

    @app.get("/observatory/research/audits")
    def audit_history() -> dict[str, Any]:
        paths = sorted((data_dir / "research-audits").glob("*.json"), reverse=True)[:30]
        return {"audits": [json.loads(path.read_bytes()) for path in paths]}

    @app.get("/observatory/research/export/{kind}")
    def export_research(kind: str, cell_id: str | None = None) -> Response:
        content, media_type = research_read(lambda: research_export(archive, kind, cell_id))
        return Response(content, media_type=media_type)

    @app.get("/observatory/research/cells/{cell_id}")
    def research_cell(cell_id: str) -> dict[str, Any]:
        cell = research_read(lambda: archive.cell(cell_id))
        return {**cell, "timeline": cell_timeline(cell)}

    @app.get("/observatory/research/artifacts/{name:path}")
    def research_artifact(name: str) -> Response:
        if name == "protocol":
            content = research_read(archive.protocol)
            return Response(content, media_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="PROTOCOL.md"'})
        path = research_read(lambda: archive.artifact(name))
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @app.post("/requirements/start")
    def start_requirements() -> dict[str, Any]:
        if not docker_available():
            ensure_docker()
        return examples()

    @app.post("/runs/clear")
    def clear_saved_runs() -> dict[str, Any]:
        if jobs.busy():
            raise ApiError(409, "busy", "还有任务在进行，先等它结束再清空。")
        _stop_session_containers(data_dir)
        cleared = runtime.ledger.clear_runs()
        _wipe_children(data_dir / "runs")
        _wipe_children(data_dir / "sessions")
        _wipe_children(data_dir / "workspace")
        logs = data_dir / "logs"
        if logs.is_dir():
            for path in logs.glob("*.log"):
                if path.name != "serve.log":
                    path.unlink(missing_ok=True)
        return {"cleared": cleared}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        found = jobs.get(job_id)
        if found is None:
            raise ApiError(404, "missing", "没有这个任务。")
        return found

    @app.get("/logs/{effect_id}")
    def get_log(effect_id: str) -> dict[str, str]:
        path = data_dir / "logs" / f"{effect_id}.log"
        if not path.is_file():
            raise ApiError(404, "missing", "还没有这份输出。")
        return {"effect_id": effect_id, "text": path.read_text(encoding="utf-8")}

    @app.get("/sessions")
    def list_sessions() -> dict[str, Any]:
        return {"sessions": app.state.sessions.list_sessions()}

    @app.post("/sessions")
    def create_session(body: SessionBody, request: Request) -> dict[str, Any]:
        if demo_mode:
            raise ApiError(409, "demo_mode", "演示实例只支持演示场景。")
        key = request.headers.get("Idempotency-Key")
        if key:
            if len(key) > 128:
                raise ApiError(400, "invalid_launch_key", "启动请求标识过长。")
            state, previous = launches.claim(key, body.model_dump())
            if state == "replay":
                return previous
            if state != "new":
                raise ApiError(409, "launch_" + state, "启动请求已登记或参数不一致。请在运行历史中确认结果。")
        try:
            view = app.state.sessions.create(body.workspace, body.task, body.test_command, title=body.title, timeout=body.timeout)
        except SessionError:
            if key:
                launches.reject(key)
            raise
        if key:
            launches.complete(key, {name: view[name] for name in ("run_id", "session_id", "job_id") if name in view})
        view["events"] = [_present_event(item) for item in view["events"]]
        return _with_figures(data_dir, view)

    @app.get("/observatory/launch/{key}")
    def lookup_launch(key: str) -> dict[str, Any]:
        return launches.lookup(key)

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        view = app.state.sessions.get(session_id)
        view["events"] = [_present_event(item) for item in view["events"]]
        return _with_figures(data_dir, view)

    @app.post("/sessions/{session_id}/patch")
    def publish_patch(session_id: str, body: PatchBody) -> dict[str, Any]:
        return app.state.sessions.publish(session_id, body.path, body.content)

    @app.post("/sessions/{session_id}/commands")
    def start_command(session_id: str, body: CommandBody) -> dict[str, Any]:
        return app.state.sessions.command(session_id, body.command)

    @app.post("/sessions/{session_id}/cancel")
    def cancel_session(session_id: str) -> dict[str, Any]:
        return app.state.sessions.cancel(session_id)

    @app.post("/sessions/{session_id}/reconcile")
    def reconcile_session(session_id: str) -> dict[str, Any]:
        return app.state.sessions.reconcile_session(session_id)

    @app.post("/sessions/{session_id}/resume")
    def resume_session(session_id: str) -> dict[str, Any]:
        view = app.state.sessions.resume(session_id)
        view["events"] = [_present_event(item) for item in view["events"]]
        return _with_figures(data_dir, view)

    @app.post("/runs")
    def proxy_create(body: ControlBody) -> dict[str, Any]:
        return _proxy(runtime, "/runs", body.model_dump(exclude_none=True))

    @app.post("/runs/{run_id}/{action}")
    def proxy_run(run_id: str, action: str, body: ControlBody) -> dict[str, Any]:
        return _proxy(runtime, f"/runs/{run_id}/{action}", body.model_dump(exclude_none=True))

    @app.post("/effects/commands/start")
    def proxy_command_start(body: ControlBody) -> dict[str, Any]:
        return _proxy(runtime, "/effects/commands/start", body.model_dump(exclude_none=True))

    @app.post("/effects/commands/finish")
    def proxy_command_finish(body: ControlBody) -> dict[str, Any]:
        return _proxy(runtime, "/effects/commands/finish", body.model_dump(exclude_none=True))

    @app.post("/effects/external-unknown")
    def proxy_unknown(body: ControlBody) -> dict[str, Any]:
        return _proxy(runtime, "/effects/external-unknown", body.model_dump(exclude_none=True))

    @app.post("/effects/reserve")
    def proxy_reserve(body: ControlBody) -> dict[str, Any]:
        return _proxy(runtime, "/effects/reserve", body.model_dump(exclude_none=True))

    @app.post("/effects/commit")
    def proxy_commit(body: ControlBody) -> dict[str, Any]:
        return _proxy(runtime, "/effects/commit", body.model_dump(exclude_none=True))

    @app.post("/effects/publish-container")
    def proxy_publish_container(body: ControlBody) -> dict[str, Any]:
        return _proxy(runtime, "/effects/publish-container", body.model_dump(exclude_none=True))

    @app.post("/effects/publish-file")
    def proxy_publish_file(body: ControlBody) -> dict[str, Any]:
        return _proxy(runtime, "/effects/publish-file", body.model_dump(exclude_none=True))

    @app.post("/effects/{key}/reconcile")
    def proxy_reconcile(key: str, body: ControlBody) -> dict[str, Any]:
        return _proxy(runtime, f"/effects/{key}/reconcile", body.model_dump(exclude_none=True))

    return app


def research_read(operation):
    try:
        return operation()
    except FileNotFoundError as exc:
        raise ApiError(404, "not_available", str(exc)) from exc
    except (OSError, ValueError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
        raise ApiError(409, "archive_unreadable", str(exc)) from exc


def _proxy(runtime: Runtime, path: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        result = dispatch_control(runtime.ledger, runtime.sink, runtime.workspace, path, body)
    except KeyError as exc:
        raise ApiError(404, "missing", "没有这次运行。") from exc
    if result.get("error") == "missing":
        raise ApiError(404, "missing", "没有这条路径。")
    return result


def _stop_session_containers(data_dir: Path) -> None:
    if not docker_available():
        return
    directory = data_dir / "sessions"
    if not directory.is_dir():
        return
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = str(record.get("container") or "")
        if name:
            stop_container(name)


def _wipe_children(directory: Path) -> None:
    if not directory.is_dir():
        return
    for child in directory.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _present_run(runtime: Runtime, run_id: str, *, figures: bool = True) -> dict[str, Any]:
    try:
        state = runtime.ledger.get_run(run_id)
    except KeyError as exc:
        raise ApiError(404, "missing", "没有这次运行。") from exc
    events = [_present_event(item) for item in runtime.ledger.events(run_id)]
    view = {
        "run_id": run_id,
        "phase": state.phase,
        "eligible": state.eligible,
        "fencing_token": state.fencing_token,
        "state_version": state.state_version,
        "slot_released": state.cancel.slot_released,
        "events": events,
        "effects": runtime.ledger.effects_for_run(run_id),
        "observations": observe_run(runtime.ledger, run_id),
    }
    view.update(evidence(runtime, run_id, events, view["effects"]))
    view["summary"] = summarize(view, events, view["effects"], view["receipts"])
    return _with_figures(runtime.data_dir, view) if figures else view


def _with_figures(data_dir: Path, view: dict[str, Any]) -> dict[str, Any]:
    written = write_run_figures(data_dir / "runs" / str(view["run_id"]), view)
    view["effect_svg"] = written["svg"]
    view["table_html"] = written["html"]
    view["effect_summary"] = written["summary"]
    return view


def _present_event(event: dict[str, Any]) -> dict[str, Any]:
    shown = dict(event)
    shown["label"] = EVENT_LABELS.get(event["event_type"], event["event_type"])
    return shown


def _stream(runtime: Runtime, run_id: str, after: int) -> Iterator[str]:
    import time

    last = after
    idle = 0
    while idle < 240:
        fresh = [item for item in runtime.ledger.events(run_id) if int(item["seq"]) > last]
        if not fresh:
            idle += 1
            time.sleep(0.5)
            continue
        idle = 0
        for item in fresh:
            last = int(item["seq"])
            payload = json.dumps(_present_event(item), ensure_ascii=False)
            yield f"id: {last}\ndata: {payload}\n\n"


def _log(data_dir: Path, request_id: str, method: str, path: str) -> None:
    run_id = ""
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] in {"runs", "sessions"}:
        run_id = parts[1]
    line = redact(f"{request_id} {method} {path} run_id={run_id}\n")
    destination = data_dir / "logs" / "serve.log"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(line)
