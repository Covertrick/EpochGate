"""OpenAI 兼容的流式工具调用后端。用 httpx 读 SSE，取消时关闭响应。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from epochgate.http_backend import _record_provider_usage, build_chat_payload
from epochgate.ids import IdFactory
from epochgate.models import (
    BackendCapabilities,
    BackendEvent,
    BackendHandle,
    CancelReceipt,
    InferenceRequest,
)
from epochgate.sse import ToolCallAssembler, parse_sse_data

_STREAM_CLOSED = (httpx.ReadError, httpx.RemoteProtocolError, asyncio.CancelledError)


class HttpStreamingBackend:
    """向 ``/v1/chat/completions`` 发流式请求，并拼接第一条工具调用。

    ``reclaimed`` 只表示本地读循环已停。关闭连接不能说明供应商停止计费或停止计算。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 60.0,
        max_inflight: int = 1,
        extra_payload: dict[str, Any] | None = None,
        trust_env: bool = True,
        ids: IdFactory | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        system_prompt: str | None = None,
        tool_choice: Any = "auto",
        stop_on_cancel: bool = True,
        on_first_delta: Callable[[], None] | None = None,
        first_delta_delay_s: float = 0.3,
    ) -> None:
        if first_delta_delay_s < 0:
            raise ValueError("first_delta_delay_s must be >= 0")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._trust_env = trust_env
        self._ids = ids or IdFactory(None)
        self._transport = transport
        self._system_prompt = system_prompt
        self._tool_choice = tool_choice
        self._stop_on_cancel = stop_on_cancel
        self._on_first_delta = on_first_delta
        self._first_delta_delay_s = first_delta_delay_s
        self._extra_payload = dict(extra_payload or {})
        self._caps = BackendCapabilities(
            supports_cancel=True,
            supports_streaming=True,
            resource_reclaim_observable=False,
            max_inflight=max_inflight,
            streaming_capability=True,
            local_read_abort=stop_on_cancel,
            client_stream_close_issued=stop_on_cancel,
            transport_disconnect_observed=False,
            provider_cancel_api_invoked=False,
            provider_cancel_ack_received=False,
            provider_cancel_request=False,
            provider_cancel_ack_semantics="UNKNOWN",
            server_generation_observability=False,
            server_generation_stopped_observed=False,
            server_resource_reclaim_observability=False,
            server_resource_reclaim_observed=False,
        )
        self._requests: dict[str, InferenceRequest] = {}
        self._responses: dict[str, httpx.Response] = {}
        self._cancel_flags: dict[str, asyncio.Event] = {}
        self._stopped: dict[str, asyncio.Event] = {}
        self._stopped_by_cancel: set[str] = set()
        self._finished: set[str] = set()
        self._first_fired: set[str] = set()
        self._delay_tasks: dict[str, asyncio.Task[None]] = {}
        self.argument_text: dict[str, str] = {}

    def capabilities(self) -> BackendCapabilities:
        """报告可取消、可流式。本地读停止可观察，服务端回收不可观察。副作用：无。失败：无。"""
        return self._caps

    async def submit(self, request: InferenceRequest) -> BackendHandle:
        """登记一次流式生成。HTTP 在 events 里才发出。

        输入：推理请求。
        输出：句柄。
        副作用：准备取消和停止信号。
        失败：无。
        """
        handle_id = self._ids("bh")
        self._requests[handle_id] = request
        self._cancel_flags[handle_id] = asyncio.Event()
        self._stopped[handle_id] = asyncio.Event()
        return BackendHandle(handle_id=handle_id, attempt_id=request.attempt_id)

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        """请求停止本地读循环。臂 A 只记账，臂 B 关闭响应。

        输入：句柄。
        输出：取消回执。只有读循环因取消退出时 ``reclaimed`` 才为真。
        副作用：``stop_on_cancel`` 为真时调用 ``response.aclose()``，只关闭本地正在读的响应。
        这不是供应商取消 API，回执里的确认也不是服务端 ACK。
        失败：未知句柄返回未确认回执，不抛异常。断开时的读错误由 events 接住。
        """
        if handle.handle_id not in self._requests:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=False,
                evidence="unknown_handle",
                grade="UNKNOWN",
                reclaimed=False,
            )
        if handle.handle_id in self._finished:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=True,
                evidence="already_completed",
                grade="ACKNOWLEDGED",
                reclaimed=False,
            )
        self._cancel_flags[handle.handle_id].set()
        if not self._stop_on_cancel:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=True,
                evidence="cancel_accepted_compute_may_continue",
                grade="ACKNOWLEDGED",
                reclaimed=False,
            )
        response = self._responses.get(handle.handle_id)
        closed = False
        if response is not None:
            await response.aclose()
            closed = True
        try:
            await asyncio.wait_for(self._stopped[handle.handle_id].wait(), timeout=2.0)
        except TimeoutError:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=True,
                evidence="loop_not_stopped",
                grade="ACKNOWLEDGED",
                reclaimed=False,
                connection_closed=closed,
            )
        reclaimed = handle.handle_id in self._stopped_by_cancel
        return CancelReceipt(
            handle_id=handle.handle_id,
            acknowledged=True,
            evidence="本地循环已停" if reclaimed else "loop_not_stopped",
            grade="ACKNOWLEDGED",
            reclaimed=reclaimed,
            connection_closed=closed,
        )

    def events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        """流出增量。完整工具调用只在参数拼完且未被取消时交出。

        输入：句柄。
        输出：accepted、started、token_delta、tool_call_delta，以及 completed 或 failed。
        副作用：对配置的端点发起流式 POST。收到首个非空增量后，可按延迟调用 ``on_first_delta``。
        失败：未知句柄抛出 ``KeyError``。主动断开引起的读错误被接住，不向外抛。
        """
        return self._events(handle)

    async def _events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        if handle.handle_id not in self._requests:
            raise KeyError(handle.handle_id)
        request = self._requests[handle.handle_id]
        now = time.monotonic()
        yield BackendEvent(handle_id=handle.handle_id, kind="accepted", mono_time=now)
        yield BackendEvent(handle_id=handle.handle_id, kind="started", mono_time=time.monotonic())
        if self._cancel_flags[handle.handle_id].is_set() and self._stop_on_cancel:
            self._stopped_by_cancel.add(handle.handle_id)
            self._stopped[handle.handle_id].set()
            return
        payload = build_chat_payload(
            request,
            model=self._model,
            system_prompt=self._system_prompt,
            tool_choice=self._tool_choice,
            stream=True,
        )
        if self._extra_payload:
            payload.update(self._extra_payload)
        assembler = ToolCallAssembler()
        provider: dict[str, Any] = {}
        local_index = 0
        client_kwargs: dict[str, Any] = {"timeout": self._timeout_s, "trust_env": self._trust_env}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
            client_kwargs["trust_env"] = False
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                async with client.stream(
                    "POST",
                    self._completions_url(),
                    headers=self._headers(),
                    json=payload,
                ) as response:
                    self._responses[handle.handle_id] = response
                    if response.status_code != 200:
                        body = (await response.aread())[:300].decode(errors="replace")
                        self._finished.add(handle.handle_id)
                        yield BackendEvent(
                            handle_id=handle.handle_id,
                            kind="failed",
                            mono_time=time.monotonic(),
                            detail=(
                                f"http_{response.status_code}:"
                                f"exception_type=HTTPStatusError;"
                                f"status_code={response.status_code};"
                                f"timeout=false;closed_by_cancel=false;"
                                f"body={body}"
                            ),
                        )
                        return
                    async for line in response.aiter_lines():
                        if self._should_stop(handle.handle_id):
                            self._stopped_by_cancel.add(handle.handle_id)
                            return
                        parsed = parse_sse_data(line)
                        if parsed is None:
                            continue
                        usage = parsed.get("usage")
                        if isinstance(usage, dict) and usage:
                            provider = {str(key): value for key, value in usage.items()}
                        choices = parsed.get("choices") or []
                        delta = choices[0].get("delta") if choices else None
                        if not isinstance(delta, dict):
                            continue
                        if self._should_stop(handle.handle_id):
                            self._stopped_by_cancel.add(handle.handle_id)
                            return
                        for kind, detail in assembler.feed_delta(delta):
                            if self._should_stop(handle.handle_id):
                                self._stopped_by_cancel.add(handle.handle_id)
                                return
                            local_index += 1
                            if kind == "tool_call_delta":
                                self.argument_text[handle.handle_id] = (
                                    self.argument_text.get(handle.handle_id, "") + detail
                                )
                            self._schedule_first_delta(handle.handle_id)
                            yield BackendEvent(
                                handle_id=handle.handle_id,
                                kind=kind,  # type: ignore[arg-type]
                                mono_time=time.monotonic(),
                                detail=detail,
                                usage={
                                    "event_count": 1,
                                    "usage_source": "local",
                                    "index": local_index,
                                },
                            )
                            if self._should_stop(handle.handle_id):
                                self._stopped_by_cancel.add(handle.handle_id)
                                return
                    if self._should_stop(handle.handle_id):
                        self._stopped_by_cancel.add(handle.handle_id)
                        return
                    tool_call = None
                    try:
                        tool_call = assembler.finish(request.tools, self._ids)
                    except ValueError as exc:
                        if "no tool_calls" not in str(exc):
                            raise
                    self._finished.add(handle.handle_id)
                    completed_usage: dict[str, Any] = {"local_tokens": local_index, "usage_source": "local"}
                    _record_provider_usage(completed_usage, provider)
                    yield BackendEvent(
                        handle_id=handle.handle_id,
                        kind="completed",
                        mono_time=time.monotonic(),
                        tool_call=tool_call,
                        usage=completed_usage,
                    )
        except _STREAM_CLOSED as exc:
            if handle.handle_id in self._finished:
                return
            if self._cancel_flags[handle.handle_id].is_set():
                self._stopped_by_cancel.add(handle.handle_id)
                return
            self._finished.add(handle.handle_id)
            yield BackendEvent(
                handle_id=handle.handle_id,
                kind="failed",
                mono_time=time.monotonic(),
                detail=_http_failure_detail(exc, closed_by_cancel=False),
            )
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
            self._finished.add(handle.handle_id)
            yield BackendEvent(
                handle_id=handle.handle_id,
                kind="failed",
                mono_time=time.monotonic(),
                detail=f"parse_error:{type(exc).__name__}:{exc}",
            )
        except httpx.HTTPError as exc:
            self._finished.add(handle.handle_id)
            yield BackendEvent(
                handle_id=handle.handle_id,
                kind="failed",
                mono_time=time.monotonic(),
                detail=_http_failure_detail(exc, closed_by_cancel=self._cancel_flags[handle.handle_id].is_set()),
            )
        finally:
            self._responses.pop(handle.handle_id, None)
            delay = self._delay_tasks.pop(handle.handle_id, None)
            if delay is not None:
                delay.cancel()
            self._stopped[handle.handle_id].set()

    def _schedule_first_delta(self, handle_id: str) -> None:
        if self._on_first_delta is None or handle_id in self._first_fired:
            return
        self._first_fired.add(handle_id)
        if self._first_delta_delay_s == 0:
            self._on_first_delta()
            return

        async def _fire() -> None:
            try:
                await asyncio.sleep(self._first_delta_delay_s)
                if self._on_first_delta is not None:
                    self._on_first_delta()
            except asyncio.CancelledError:
                return

        self._delay_tasks[handle_id] = asyncio.create_task(_fire())

    def _should_stop(self, handle_id: str) -> bool:
        return self._stop_on_cancel and self._cancel_flags[handle_id].is_set()

    def _completions_url(self) -> str:
        if self._base_url.endswith("/v1"):
            return self._base_url + "/chat/completions"
        return self._base_url + "/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }


def _http_failure_detail(exc: BaseException, *, closed_by_cancel: bool) -> str:
    """把传输失败写成可分类的短字符串。

    输入：异常，以及是否由本地取消关闭引起。
    输出：含异常类型、状态码、超时与正文片段的 detail。
    副作用：无。
    失败：无。
    """
    status = getattr(exc, "response", None)
    status_code = getattr(status, "status_code", None) if status is not None else None
    body = ""
    if status is not None:
        try:
            body = (status.text or "")[:300]
        except Exception:
            body = ""
    timeout = isinstance(exc, httpx.TimeoutException)
    kind = "stream_closed" if isinstance(exc, _STREAM_CLOSED) else "http_error"
    return (
        f"{kind}:"
        f"exception_type={type(exc).__name__};"
        f"status_code={status_code};"
        f"timeout={str(timeout).lower()};"
        f"closed_by_cancel={str(closed_by_cancel).lower()};"
        f"message={str(exc)[:200]};"
        f"body={body}"
    )
