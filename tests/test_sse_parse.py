"""固定 SSE 文本上的工具分片拼接，以及断开时不把读错误抛出运行时。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from epochgate.http_streaming import HttpStreamingBackend
from epochgate.ids import IdFactory
from epochgate.models import InferenceRequest, ToolSpec
from epochgate.sse import consume_sse_text

_TOOLS = [
    ToolSpec(
        name="reserve",
        role="effect",
        parameters={
            "type": "object",
            "properties": {"qty": {"type": "integer"}},
            "required": ["qty"],
        },
    )
]

FIXED_SSE = """\
data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}

data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"reserve","arguments":""}}]}}]}

data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"qty\\":"}}]}}]}

data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":" 1}"}}]}}]}

data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":3,"completion_tokens":4}}

data: [DONE]
"""


def _request() -> InferenceRequest:
    return InferenceRequest(
        attempt_id="a",
        task_id="t",
        plan_epoch=0,
        obs_revision=0,
        goal="reserve one unit",
        tools=list(_TOOLS),
    )


def test_fixed_sse_joins_split_tool_arguments() -> None:
    deltas, call, usage = consume_sse_text(FIXED_SSE, _TOOLS, IdFactory(1))
    fragments = [detail for kind, detail in deltas if kind == "tool_call_delta"]
    assert fragments == ["reserve", '{"qty":', " 1}"]
    assert call.name == "reserve"
    assert call.arguments == {"qty": 1}
    assert call.call_id == "call_1"
    assert usage == {"prompt_tokens": 3, "completion_tokens": 4}
    assert "".join(fragments[1:]) == '{"qty": 1}'


class _BytesTransport(httpx.AsyncBaseTransport):
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(self._body))


class _HoldStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):  # type: ignore[override]
        yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
        while not self.closed:
            await asyncio.sleep(0.01)
        raise httpx.ReadError("closed")

    async def aclose(self) -> None:
        self.closed = True


class _HoldTransport(httpx.AsyncBaseTransport):
    def __init__(self, stream: _HoldStream) -> None:
        self._stream = stream

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=self._stream)


@pytest.mark.asyncio
async def test_streaming_backend_assembles_fixed_sse() -> None:
    fired = {"n": 0}
    backend = HttpStreamingBackend(
        base_url="http://sse.local/v1",
        api_key="local",
        model="local",
        trust_env=False,
        transport=_BytesTransport(FIXED_SSE.encode()),
        on_first_delta=lambda: fired.__setitem__("n", fired["n"] + 1),
        first_delta_delay_s=0.0,
        ids=IdFactory(1),
    )
    events = [event async for event in backend.events(await backend.submit(_request()))]
    completed = [event for event in events if event.kind == "completed"]
    deltas = [event for event in events if event.kind == "tool_call_delta"]
    assert len(completed) == 1
    assert completed[0].tool_call is not None
    assert completed[0].tool_call.arguments == {"qty": 1}
    assert [event.detail for event in deltas] == ["reserve", '{"qty":', " 1}"]
    assert all(event.usage.get("usage_source") == "local" for event in deltas)
    assert completed[0].usage.get("provider_usage") == {"prompt_tokens": 3, "completion_tokens": 4}
    assert fired["n"] == 1
    assert "api_key" not in completed[0].model_dump_json()


@pytest.mark.asyncio
async def test_aclose_swallows_read_error() -> None:
    stream = _HoldStream()
    backend = HttpStreamingBackend(
        base_url="http://sse.local/v1",
        api_key="local",
        model="local",
        trust_env=False,
        transport=_HoldTransport(stream),
        stop_on_cancel=True,
        ids=IdFactory(1),
    )
    handle = await backend.submit(_request())

    async def _consume() -> list:
        return [event async for event in backend.events(handle)]

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)
    receipt = await backend.cancel(handle)
    events = await task
    assert receipt.reclaimed is True
    assert receipt.evidence == "本地循环已停"
    assert "gpu" not in receipt.evidence.lower()
    assert not any(event.kind == "completed" for event in events)
    assert any(event.kind == "token_delta" and event.detail == "x" for event in events)


class _RetryTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": "```json\\n{}\\n```"}}]})
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
                ]
            },
        )


@pytest.mark.asyncio
async def test_parse_failure_retries_once_and_records_the_count() -> None:
    from epochgate.http_backend import HttpToolBackend

    transport = _RetryTransport()
    backend = HttpToolBackend(
        base_url="http://retry.local/v1",
        api_key="local",
        model="local",
        trust_env=False,
        transport=transport,
        parse_retries=1,
    )
    events = [event async for event in backend.events(await backend.submit(_request()))]
    assert transport.calls == 2
    assert events[-1].kind == "completed"
    assert events[-1].tool_call is not None
    assert events[-1].tool_call.arguments == {"qty": 1}
    assert events[-1].usage["retry_count"] == 1
