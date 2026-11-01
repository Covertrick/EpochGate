"""OpenAI 兼容的 HTTP 工具调用后端。非流式，只解析第一条 tool_call。"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from epochgate.faults import FaultInjectedError, FaultPlan, FaultTransport
from epochgate.ids import IdFactory
from epochgate.models import (
    BackendCapabilities,
    BackendEvent,
    BackendHandle,
    CancelReceipt,
    InferenceRequest,
    ToolCall,
    ToolSpec,
)


class HttpToolBackend:
    """向 ``/v1/chat/completions`` 发一次非流式请求。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 60.0,
        max_inflight: int = 1,
        trust_env: bool = True,
        ids: IdFactory | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        fault_plan: FaultPlan | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._trust_env = trust_env
        self._ids = ids or IdFactory(None)
        self._fault_plan = fault_plan
        if fault_plan is not None:
            upstream = transport or httpx.AsyncHTTPTransport(trust_env=trust_env)
            self._transport: httpx.AsyncBaseTransport | None = FaultTransport(fault_plan, upstream)
        else:
            self._transport = transport
        self._caps = BackendCapabilities(
            supports_cancel=True,
            supports_streaming=False,
            resource_reclaim_observable=False,
            max_inflight=max_inflight,
        )
        self._requests: dict[str, InferenceRequest] = {}
        self._cancelled: set[str] = set()
        self._finished: set[str] = set()

    def capabilities(self) -> BackendCapabilities:
        """报告支持客户端取消、不支持流式。副作用：无。失败：无。"""
        return self._caps

    async def submit(self, request: InferenceRequest) -> BackendHandle:
        """记下请求，真正的 HTTP 调用发生在 events。

        输入：推理请求，其中 tools 与 context 会进入请求体。
        输出：句柄。
        副作用：保存请求。
        失败：无。
        """
        handle_id = self._ids("bh")
        self._requests[handle_id] = request
        return BackendHandle(handle_id=handle_id, attempt_id=request.attempt_id)

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        """标记客户端取消。已经完成时不再改写成资源已释放。

        输入：句柄。
        输出：回执。evidence 只描述客户端见到的事实。
        副作用：未完成的句柄记入取消集合。
        失败：未知句柄返回未确认。
        """
        if handle.handle_id not in self._requests:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=False,
                evidence="unknown_handle",
                grade="UNKNOWN",
            )
        if handle.handle_id in self._finished:
            return CancelReceipt(
                handle_id=handle.handle_id,
                acknowledged=False,
                evidence="already_completed",
                grade="UNKNOWN",
            )
        self._cancelled.add(handle.handle_id)
        return CancelReceipt(
            handle_id=handle.handle_id,
            acknowledged=True,
            evidence="client_cancel_requested",
            grade="ACCEPTED",
            reclaimed=False,
        )

    async def events(self, handle: BackendHandle) -> AsyncIterator[BackendEvent]:
        """发一次 chat completions，并解析第一条工具调用。

        输入：句柄。
        输出：accepted、started，然后 completed 或 failed。
        副作用：对配置的端点发起 HTTP POST。
        失败：未知句柄抛出 ``KeyError``；HTTP 或解析失败变成 failed 事件。
        """
        request = self._requests.get(handle.handle_id)
        if request is None:
            raise KeyError(f"unknown handle: {handle.handle_id}")
        now = time.monotonic()
        yield BackendEvent(handle_id=handle.handle_id, kind="accepted", mono_time=now)
        if handle.handle_id in self._cancelled:
            yield BackendEvent(
                handle_id=handle.handle_id,
                kind="failed",
                mono_time=time.monotonic(),
                detail="client_cancel_requested",
            )
            return
        yield BackendEvent(handle_id=handle.handle_id, kind="started", mono_time=time.monotonic())
        payload = self._build_payload(request)
        try:
            response = await self._post(payload)
        except FaultInjectedError as exc:
            self._finished.add(handle.handle_id)
            yield BackendEvent(
                handle_id=handle.handle_id,
                kind="failed",
                mono_time=time.monotonic(),
                detail="fault_disconnect",
                usage=_fault_usage(exc.fault),
            )
            return
        except httpx.HTTPError as exc:
            self._finished.add(handle.handle_id)
            yield BackendEvent(
                handle_id=handle.handle_id,
                kind="failed",
                mono_time=time.monotonic(),
                detail=f"http_error:{exc}",
            )
            return
        self._finished.add(handle.handle_id)
        fault_usage = _fault_usage(response.extensions.get("epochgate_fault"))
        if handle.handle_id in self._cancelled:
            yield BackendEvent(
                handle_id=handle.handle_id,
                kind="failed",
                mono_time=time.monotonic(),
                detail="client_cancel_requested",
                usage=fault_usage,
            )
            return
        if response.status_code != 200:
            yield BackendEvent(
                handle_id=handle.handle_id,
                kind="failed",
                mono_time=time.monotonic(),
                detail=f"http_{response.status_code}:{response.text[:300]}",
                usage=fault_usage,
            )
            return
        try:
            tool_call = self._parse_tool_call(response.json(), request)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            yield BackendEvent(
                handle_id=handle.handle_id,
                kind="failed",
                mono_time=time.monotonic(),
                detail=f"parse_error:{exc}",
                usage=fault_usage,
            )
            return
        usage = dict(fault_usage)
        body_usage = {}
        try:
            body_usage = response.json().get("usage") or {}
        except (json.JSONDecodeError, AttributeError):
            body_usage = {}
        _record_provider_usage(usage, body_usage)
        yield BackendEvent(
            handle_id=handle.handle_id,
            kind="completed",
            mono_time=time.monotonic(),
            tool_call=tool_call,
            usage=usage,
        )

    def _completions_url(self) -> str:
        if self._base_url.endswith("/v1"):
            return self._base_url + "/chat/completions"
        return self._base_url + "/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, request: InferenceRequest) -> dict[str, Any]:
        observations = request.context.get("observations", [])
        messages = list(request.context.get("messages", []))
        messages.append(
            {
                "role": "user",
                "content": (
                    f"goal: {request.goal}\n"
                    f"observations: {json.dumps(observations, ensure_ascii=False, default=str)}"
                ),
            }
        )
        return {
            "model": self._model,
            "messages": messages,
            "tools": [_openai_tool(spec) for spec in request.tools],
            "tool_choice": "auto",
        }

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        client_kwargs: dict[str, Any] = {"timeout": self._timeout_s, "trust_env": self._trust_env}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
            client_kwargs["trust_env"] = False
        async with httpx.AsyncClient(**client_kwargs) as client:
            return await client.post(
                self._completions_url(),
                headers=self._headers(),
                json=payload,
            )

    def _parse_tool_call(self, body: Mapping[str, Any], request: InferenceRequest) -> ToolCall:
        message = body["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            raise ValueError("response has no tool_calls")
        call = tool_calls[0]
        function = call["function"]
        raw_args = function.get("arguments") or "{}"
        if isinstance(raw_args, str):
            arguments = json.loads(raw_args or "{}")
        elif isinstance(raw_args, Mapping):
            arguments = dict(raw_args)
        else:
            raise ValueError("tool arguments must be a JSON object or string")
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        _reject_schema_mismatch(str(function["name"]), arguments, request.tools)
        return ToolCall(
            call_id=str(call.get("id") or self._ids("call")),
            name=str(function["name"]),
            arguments=arguments,
        )


def _record_provider_usage(usage: dict[str, Any], body_usage: object) -> None:
    """把供应商 usage 原样放进事件。

    输入：要写回的 usage，以及响应里的 usage 对象。
    输出：无。
    副作用：有 ``tokens``、``prompt_tokens`` 或 ``completion_tokens`` 时标 ``usage_source=backend``。
    只在响应自己带 ``tokens`` 时抄这个字段，不把各项相加。
    失败：usage 不是映射时不动。
    """
    if not isinstance(body_usage, Mapping) or not body_usage:
        return
    raw = {str(key): value for key, value in body_usage.items()}
    usage["provider_usage"] = raw
    if any(key in raw for key in ("tokens", "prompt_tokens", "completion_tokens")):
        usage["usage_source"] = "backend"
    if "tokens" in raw:
        usage["tokens"] = raw["tokens"]


def _fault_usage(fault: object) -> dict[str, Any]:
    if not isinstance(fault, dict) or not fault.get("fault_injected"):
        return {}
    usage = {"fault_injected": True, "fault_kind": str(fault.get("fault_kind") or "")}
    if fault.get("mode"):
        usage["fault_mode"] = str(fault["mode"])
    if "delay_s" in fault:
        usage["delay_s"] = float(fault["delay_s"])
    return usage


def _reject_schema_mismatch(name: str, arguments: Mapping[str, Any], tools: list[ToolSpec]) -> None:
    spec = next((item for item in tools if item.name == name), None)
    if spec is None:
        return
    parameters = dict(spec.parameters or {})
    required = parameters.get("required") or []
    properties = parameters.get("properties") or {}
    for key in required:
        if key not in arguments:
            raise ValueError(f"schema mismatch: missing {key}")
        expected = properties.get(key, {}).get("type") if isinstance(properties, dict) else None
        if expected == "integer" and not isinstance(arguments[key], int):
            raise ValueError(f"schema mismatch: {key}")


def _openai_tool(spec: ToolSpec) -> dict[str, Any]:
    parameters = dict(spec.parameters) if spec.parameters else {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": parameters,
        },
    }
