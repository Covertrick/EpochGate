"""OpenAI 兼容 SSE 的纯解析。不发网络。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from epochgate.http_backend import tool_call_from_parts
from epochgate.ids import IdFactory
from epochgate.models import ToolCall, ToolSpec


def parse_sse_data(line: str) -> Mapping[str, Any] | None:
    """解析一行 SSE。

    输入：原始行，可以带 ``data:`` 前缀。
    输出：JSON 对象。``[DONE]``、空行和注释返回 None。
    副作用：无。
    失败：``data:`` 后面不是 JSON 对象时抛出 ``json.JSONDecodeError`` 或 ``ValueError``。
    """
    stripped = line.strip()
    if not stripped or stripped.startswith(":"):
        return None
    if stripped.startswith("data:"):
        payload = stripped[5:].lstrip()
    else:
        return None
    if payload == "[DONE]":
        return None
    body = json.loads(payload)
    if not isinstance(body, Mapping):
        raise ValueError("sse data must be a JSON object")
    return body


class ToolCallAssembler:
    """把流式 ``tool_calls`` 碎片拼成一次调用。半截参数留在内部。"""

    def __init__(self) -> None:
        self._slots: dict[int, dict[str, str]] = {}

    def feed_delta(self, delta: Mapping[str, Any]) -> list[tuple[str, str]]:
        """吃掉一个 delta，返回非空增量。

        输入：``choices[0].delta``。
        输出：``(token_delta 或 tool_call_delta, 文本)`` 列表。空内容不计入。
        副作用：把函数名和参数碎片追加到对应下标。
        失败：无。缺字段时跳过。
        """
        emitted: list[tuple[str, str]] = []
        content = delta.get("content")
        if isinstance(content, str) and content:
            emitted.append(("token_delta", content))
        raw_calls = delta.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            return emitted
        for item in raw_calls:
            if not isinstance(item, Mapping):
                continue
            index = int(item.get("index") or 0)
            slot = self._slots.setdefault(index, {"id": "", "name": "", "arguments": ""})
            call_id = item.get("id")
            if isinstance(call_id, str) and call_id:
                slot["id"] = call_id
            function = item.get("function") or {}
            if not isinstance(function, Mapping):
                continue
            name = function.get("name")
            if isinstance(name, str) and name:
                slot["name"] += name
                emitted.append(("tool_call_delta", name))
            arguments = function.get("arguments")
            if isinstance(arguments, str) and arguments:
                slot["arguments"] += arguments
                emitted.append(("tool_call_delta", arguments))
        return emitted

    def finish(self, tools: list[ToolSpec], ids: IdFactory) -> ToolCall:
        """在流结束时交出第一条完整工具调用。

        输入：工具目录和 id 工厂。
        输出：参数已通过 schema 检查的 ``ToolCall``。
        副作用：无。
        失败：没有工具调用，或参数还不是 JSON 对象时抛出 ``ValueError``。
        """
        if not self._slots:
            raise ValueError("response has no tool_calls")
        slot = self._slots[min(self._slots)]
        raw = slot["arguments"] or "{}"
        parsed = json.loads(raw or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments must be an object")
        return tool_call_from_parts(
            call_id=slot["id"],
            name=slot["name"],
            arguments=parsed,
            tools=tools,
            fallback_id=ids("call"),
        )


def consume_sse_text(
    text: str,
    tools: list[ToolSpec],
    ids: IdFactory | None = None,
) -> tuple[list[tuple[str, str]], ToolCall, dict[str, Any]]:
    """用一段完整 SSE 文本拼出增量和工具调用。

    输入：多行 SSE 文本和工具目录。
    输出：增量列表、完整工具调用、供应商 usage（没有则为空）。
    副作用：无。
    失败：半截 JSON 或 schema 不符时抛出 ``ValueError`` 或 ``json.JSONDecodeError``。
    """
    factory = ids or IdFactory(None)
    assembler = ToolCallAssembler()
    deltas: list[tuple[str, str]] = []
    provider: dict[str, Any] = {}
    for line in text.splitlines():
        body = parse_sse_data(line)
        if body is None:
            continue
        usage = body.get("usage")
        if isinstance(usage, Mapping) and usage:
            provider = {str(key): value for key, value in usage.items()}
        choices = body.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        if isinstance(delta, Mapping):
            deltas.extend(assembler.feed_delta(delta))
    return deltas, assembler.finish(tools, factory), provider
