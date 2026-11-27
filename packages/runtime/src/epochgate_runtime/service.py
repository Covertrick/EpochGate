"""?????????????? RunLedger????????"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.files import publish_frozen_file
from epochgate_runtime.sandbox import publish_container
from epochgate_runtime.recovery import reconcile
from epochgate_runtime.sink import EffectSink


def run_view(ledger: RunLedger, run_id: str) -> dict[str, Any]:
    state = ledger.get_run(run_id)
    return {
        "run_id": state.run_id,
        "phase": state.phase,
        "eligible": state.eligible,
        "fencing_token": state.fencing_token,
        "state_version": state.state_version,
        "slot_released": state.cancel.slot_released,
        "gpu_reclaimed": False,
        "resource_observation": "not_observable",
        "events": ledger.events(run_id),
        "effects": ledger.effects_for_run(run_id),
    }


def dispatch_control(ledger: RunLedger, sink: EffectSink, workspace: Path, path: str, body: dict[str, Any]) -> dict[str, Any]:
    if path == "/runs":
        state = ledger.create_run(str(body["run_id"]))
        if body.get("session_id"):
            ledger.open_attempt(state.run_id, str(body.get("attempt_id") or f"attempt-{state.run_id}"), str(body["session_id"]))
        ledger.advance(state.run_id, "running")
        fresh = ledger.get_run(state.run_id)
        return {"run_id": fresh.run_id, "fencing_token": fresh.fencing_token, "state_version": fresh.state_version}
    if path.endswith("/admit") and path.startswith("/runs/"):
        run_id = path.split("/")[2]
        state = ledger.get_run(run_id)
        return {
            "allowed": state.eligible,
            "fencing_token": state.fencing_token,
            "state_version": state.state_version,
            "phase": state.phase,
        }
    if path == "/effects/commands/start":
        return ledger.start_command(str(body["run_id"]), str(body["idempotency_key"]), int(body["fencing_token"]))
    if path == "/effects/commands/finish":
        raw_exit = body.get("exit_code")
        exit_code = None if raw_exit is None else int(raw_exit)
        return ledger.finish_command(
            str(body["run_id"]),
            str(body["idempotency_key"]),
            int(body["fencing_token"]),
            exit_code,
        )
    if path == "/effects/external-unknown":
        return ledger.seal_unconfirmed_external(
            str(body["run_id"]),
            str(body["idempotency_key"]),
            int(body["fencing_token"]),
            int(body["state_version"]),
        )
    if path == "/effects/reserve":
        return ledger.record_intent(
            str(body["run_id"]),
            str(body["idempotency_key"]),
            int(body["fencing_token"]),
            int(body["state_version"]),
            effect_id=str(body.get("effect_id") or body["idempotency_key"]),
            arguments_hash=str(body.get("arguments_hash") or ""),
            tool_name=str(body.get("tool_name") or ""),
        )
    if path == "/effects/commit":
        return ledger.commit_authorized(
            str(body["run_id"]),
            idempotency_key=str(body["idempotency_key"]),
            fencing_token=int(body["fencing_token"]),
            state_version=int(body["state_version"]),
            sink=sink,
            payload=str(body.get("payload") or ""),
        )
    if path == "/effects/publish-container":
        return publish_container(
            ledger,
            container=str(body["container"]),
            run_id=str(body["run_id"]),
            idempotency_key=str(body["idempotency_key"]),
            fencing_token=int(body["fencing_token"]),
            relative_path=str(body["relative_path"]),
            staging_path=str(body["staging_path"]),
        )
    if path == "/effects/publish-file":
        return publish_frozen_file(
            ledger,
            workspace=workspace,
            run_id=str(body["run_id"]),
            idempotency_key=str(body["idempotency_key"]),
            fencing_token=int(body["fencing_token"]),
            relative_path=str(body["relative_path"]),
            staging_path=Path(str(body["staging_path"])),
        )
    if path.endswith("/revoke") and path.startswith("/runs/"):
        state = ledger.revoke_with_sink(path.split("/")[2], sink)
        return {"phase": state.phase, "fencing_token": state.fencing_token, "eligible": state.eligible}
    if path.endswith("/supersede") and path.startswith("/runs/"):
        state = ledger.supersede(path.split("/")[2], sink)
        return {
            "phase": state.phase,
            "fencing_token": state.fencing_token,
            "state_version": state.state_version,
            "eligible": state.eligible,
        }
    if path.startswith("/effects/") and path.endswith("/reconcile"):
        key = path.split("/")[2]
        return reconcile(
            ledger,
            sink,
            run_id=str(body["run_id"]),
            idempotency_key=key,
            fencing_token=int(body["fencing_token"]),
        )
    return {"error": "missing"}


class ControlServer:
    def __init__(self, ledger: RunLedger, sink: EffectSink, workspace: Path, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self.ledger = ledger
        self.sink = sink
        self.workspace = workspace
        self.host = host
        self.port = port
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/health":
                    self._send(200, {"ok": True, "authority": "run_ledger"})
                    return
                if path == "/runs":
                    self._send(200, {"runs": owner.ledger.list_runs()})
                    return
                if path.startswith("/runs/"):
                    run_id = path.split("/")[2]
                    try:
                        payload = run_view(owner.ledger, run_id)
                    except KeyError:
                        self._send(404, {"error": "missing"})
                        return
                    self._send(200, payload)
                    return
                self._send(404, {"error": "missing"})

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self._send(400, {"error": "json"})
                    return
                self._send(200, owner.handle(path, body))

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt: str, *args: object) -> None:
                return

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        self.ledger.repair_sink(self.sink)
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def handle(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return dispatch_control(self.ledger, self.sink, self.workspace, path, body)
