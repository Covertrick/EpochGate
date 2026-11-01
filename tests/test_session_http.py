"""多轮会话把观测正文送进请求；HTTP 后端能解析一次工具调用。"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from epochgate.backend import FakeBackend
from epochgate.cli import app, http_settings
from epochgate.environment import FakeEnvironment
from epochgate.http_backend import HttpToolBackend
from epochgate.models import (
    BackendCapabilities,
    BackendEvent,
    BackendHandle,
    CancelReceipt,
    InferenceRequest,
    Task,
    TaskStatus,
    ToolCall,
    ToolSpec,
)
from epochgate.runtime import Runtime


class ScriptedBackend:
    """第一次返回读工具，第二次返回写工具，并留下请求正文。"""

    def __init__(self) -> None:
        self.requests: list[InferenceRequest] = []
        self._handles: dict[str, int] = {}

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(supports_cancel=False, max_inflight=2)

    async def submit(self, request: InferenceRequest) -> BackendHandle:
        self.requests.append(request)
        handle_id = f"h-{len(self.requests)}"
        self._handles[handle_id] = len(self.requests)
        return BackendHandle(handle_id=handle_id, attempt_id=request.attempt_id)

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        return CancelReceipt(handle_id=handle.handle_id, acknowledged=False, evidence="cancel_not_supported")

    async def events(self, handle: BackendHandle):
        turn = self._handles[handle.handle_id]
        now = time.monotonic()
        yield BackendEvent(handle_id=handle.handle_id, kind="accepted", mono_time=now)
        yield BackendEvent(handle_id=handle.handle_id, kind="started", mono_time=now)
        if turn == 1:
            tool = ToolCall(call_id="call-read", name="read_inventory", arguments={})
        else:
            tool = ToolCall(call_id="call-write", name="reserve", arguments={"qty": 1})
        yield BackendEvent(
            handle_id=handle.handle_id,
            kind="completed",
            mono_time=time.monotonic(),
            tool_call=tool,
        )


@pytest.mark.asyncio
async def test_scripted_backend_sees_observation_payload_and_reserves() -> None:
    env = FakeEnvironment(stock=3)
    backend = ScriptedBackend()
    runtime = Runtime(environment=env, backend=backend)
    now = time.monotonic()
    result = await runtime.run_task(
        Task(
            task_id="t-turns",
            arrival_mono=now,
            deadline_mono=now + 30.0,
            goal="reserve one unit",
        )
    )
    assert result.status == TaskStatus.SUCCEEDED
    assert env.stock == 2
    assert len(backend.requests) >= 2
    payload = backend.requests[0].context["observations"][0]["payload"]
    assert payload["stock"] == 3
    assert backend.requests[1].context["messages"]


@pytest.mark.asyncio
async def test_http_backend_tool_call_commits_through_runtime() -> None:
    seen: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            seen["body"] = json.loads(self.rfile.read(length).decode())
            payload = {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "reserve", "arguments": '{"qty": 1}'},
                                }
                            ]
                        }
                    }
                ]
            }
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        backend = HttpToolBackend(
            base_url=f"http://127.0.0.1:{port}/v1",
            api_key="test",
            model="fake-model",
            trust_env=False,
        )
        runtime = Runtime(
            environment=FakeEnvironment(stock=3),
            backend=backend,
            inline_dispatch=True,
        )
        now = time.monotonic()
        result = await runtime.run_task(
            Task(
                task_id="t-http",
                arrival_mono=now,
                deadline_mono=now + 30.0,
                goal="reserve one unit",
            )
        )
        assert result.status == TaskStatus.SUCCEEDED
        assert runtime.environment.stock == 2  # type: ignore[attr-defined]
        body = seen["body"]
        assert isinstance(body, dict)
        assert "stock" in body["messages"][-1]["content"]
        names = [tool["function"]["name"] for tool in body["tools"]]
        assert "reserve" in names
        assert "read_inventory" in names
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_http_cancel_receipt_does_not_claim_gpu_release() -> None:
    backend = HttpToolBackend(base_url="http://127.0.0.1:9/v1", api_key="k", model="m")
    handle = await backend.submit(
        InferenceRequest(
            attempt_id="a",
            task_id="t",
            plan_epoch=0,
            obs_revision=0,
            goal="noop",
            tools=[ToolSpec(name="reserve", role="effect")],
        )
    )
    receipt = await backend.cancel(handle)
    assert receipt.acknowledged is True
    assert "gpu" not in receipt.evidence.lower()


def test_http_smoke_exits_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EPOCHGATE_BASE_URL", raising=False)
    monkeypatch.delenv("EPOCHGATE_API_KEY", raising=False)
    monkeypatch.delenv("EPOCHGATE_MODEL", raising=False)
    monkeypatch.setattr("epochgate.cli.load_project_env", lambda path=None: None)
    assert http_settings() is None
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["http-smoke"])
    assert result.exit_code == 2
    assert "EPOCHGATE_BASE_URL" in result.output


@pytest.mark.asyncio
async def test_fake_backend_still_reserves() -> None:
    env = FakeEnvironment(stock=3)
    runtime = Runtime(environment=env, backend=FakeBackend(qty=1))
    now = time.monotonic()
    result = await runtime.run_task(
        Task(
            task_id="t-fake",
            arrival_mono=now,
            deadline_mono=now + 30.0,
            goal="reserve one unit",
        )
    )
    assert result.status == TaskStatus.SUCCEEDED
    assert env.stock == 2


class _UsageTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"id": "ok", "function": {"name": "reserve", "arguments": '{"qty": 1}'}}
                            ]
                        }
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )


@pytest.mark.asyncio
async def test_provider_usage_is_kept_raw_without_a_sum() -> None:
    backend = HttpToolBackend(
        base_url="http://usage.local/v1",
        api_key="local",
        model="local",
        trust_env=False,
        transport=_UsageTransport(),
    )
    request = InferenceRequest(
        attempt_id="a",
        task_id="t",
        plan_epoch=0,
        obs_revision=0,
        goal="reserve one unit",
        tools=[ToolSpec(name="reserve", role="effect")],
    )
    events = [event async for event in backend.events(await backend.submit(request))]
    usage = events[-1].usage
    assert usage["usage_source"] == "backend"
    assert usage["provider_usage"] == {"prompt_tokens": 11, "completion_tokens": 7}
    assert "tokens" not in usage
