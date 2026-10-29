"""可播种的故障策略。Transport 拦截发往模型的 HTTP 请求。"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


class FaultInjectedError(httpx.TransportError):
    """连接被故障策略直接断开。仍然带上故障种类，供 trace 核对。"""

    def __init__(self, fault: dict[str, Any]) -> None:
        super().__init__(str(fault.get("fault_kind") or "disconnect"))
        self.fault = fault


@dataclass
class FaultSpec:
    """一条来自场景文件的故障。"""

    kind: str
    probability: float
    min_ms: int = 0
    max_ms: int = 0
    modes: list[str] = field(default_factory=list)


@dataclass
class FaultPlan:
    """按种子依次掷骰。同一种子下，请求序列对应的故障序列相同。"""

    faults: list[FaultSpec]
    seed: int
    fired: list[dict[str, Any]] = field(default_factory=list)
    request_count: int = 0

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def any_probability(self) -> bool:
        """是否存在可能触发的故障。输入：无。输出：布尔。副作用：无。失败：无。"""
        return any(item.probability > 0 for item in self.faults)

    def roll(self) -> dict[str, Any] | None:
        """为下一次请求决定要不要注入故障。

        输入：无。使用构造时的种子。
        输出：故障记录；未命中时为 None。
        副作用：推进随机序列，并在命中时追加 ``fired``。
        失败：场景里的种类无法识别时抛出 ``ValueError``。
        """
        self.request_count += 1
        for spec in self.faults:
            if self._rng.random() >= spec.probability:
                continue
            fault = self._materialize(spec)
            self.fired.append(fault)
            return fault
        return None

    def _materialize(self, spec: FaultSpec) -> dict[str, Any]:
        fault: dict[str, Any] = {"fault_injected": True, "fault_kind": spec.kind}
        if spec.kind == "latency_spike":
            low = min(spec.min_ms, spec.max_ms)
            high = max(spec.min_ms, spec.max_ms)
            delay_ms = self._rng.randint(low, high) if high > 0 else 0
            fault["delay_s"] = delay_ms / 1000.0
            return fault
        if spec.kind == "value_fault":
            modes = spec.modes or ["truncated_json", "empty_tool_calls", "bad_arguments"]
            fault["mode"] = modes[self._rng.randrange(len(modes))]
            return fault
        if spec.kind == "crash":
            modes = spec.modes or ["http_500", "disconnect"]
            fault["mode"] = modes[self._rng.randrange(len(modes))]
            return fault
        raise ValueError(f"unknown fault kind: {spec.kind}")


def load_scenario(path: str | Path, *, seed: int | None = None) -> FaultPlan:
    """从 YAML 读取故障场景。

    输入：场景路径。``seed`` 覆盖文件中的种子，便于单 cell 复现。
    输出：``FaultPlan``。
    副作用：只读文件。
    失败：文件缺失、不是映射，或故障项缺字段时抛出 ``ValueError``。
    """
    raw = _parse_scenario(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("scenario must be a mapping")
    file_seed = int(raw.get("seed", 0))
    specs: list[FaultSpec] = []
    for item in raw.get("faults") or []:
        if not isinstance(item, dict) or "kind" not in item:
            raise ValueError("each fault needs a kind")
        modes = item.get("modes") or []
        if isinstance(modes, str):
            modes = [modes]
        specs.append(
            FaultSpec(
                kind=str(item["kind"]),
                probability=float(item.get("probability", 0)),
                min_ms=int(item.get("min_ms", 0)),
                max_ms=int(item.get("max_ms", 0)),
                modes=[str(mode) for mode in modes],
            )
        )
    return FaultPlan(faults=specs, seed=file_seed if seed is None else seed)


class FaultTransport(httpx.AsyncBaseTransport):
    """包住上游 transport。故障在发出真实响应之前或之时注入。"""

    def __init__(self, plan: FaultPlan, upstream: httpx.AsyncBaseTransport) -> None:
        self.plan = plan
        self._upstream = upstream

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """按计划改写或转发这一次 HTTP 请求。

        输入：httpx 请求。
        输出：可能被改写的响应。
        副作用：命中 ``latency_spike`` 时睡眠；命中断开时抛出。
        失败：``disconnect`` 抛出 ``FaultInjectedError``。
        """
        fault = self.plan.roll()
        if fault is None:
            return await self._upstream.handle_async_request(request)
        kind = str(fault["fault_kind"])
        if kind == "latency_spike":
            await asyncio.sleep(float(fault.get("delay_s") or 0))
            response = await self._upstream.handle_async_request(request)
            await response.aread()
            return _with_fault(response, fault)
        if kind == "crash":
            if fault.get("mode") == "disconnect":
                raise FaultInjectedError(fault)
            return httpx.Response(500, content=b"fault", extensions={"epochgate_fault": fault})
        if kind == "value_fault":
            return _value_response(str(fault.get("mode") or "truncated_json"), fault)
        raise ValueError(f"unknown fault kind: {kind}")


def _parse_scenario(text: str) -> dict[str, Any]:
    """读取本仓库使用的场景子集：标量、故障列表、modes 列表。"""
    root: dict[str, Any] = {"faults": []}
    current: dict[str, Any] | None = None
    in_modes = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("seed:"):
            root["seed"] = _scalar(stripped.split(":", 1)[1].strip())
            in_modes = False
            continue
        if stripped == "faults:":
            in_modes = False
            continue
        if stripped.startswith("- kind:"):
            current = {"kind": _scalar(stripped.split(":", 1)[1].strip())}
            root["faults"].append(current)
            in_modes = False
            continue
        if current is None:
            raise ValueError(f"unexpected scenario line: {stripped}")
        if stripped == "modes:":
            current["modes"] = []
            in_modes = True
            continue
        if in_modes and stripped.startswith("- "):
            current["modes"].append(str(_scalar(stripped[2:].strip())))
            continue
        if ":" not in stripped:
            raise ValueError(f"unexpected scenario line: {stripped}")
        key, value = stripped.split(":", 1)
        current[key.strip()] = _scalar(value.strip())
        in_modes = False
    return root


def _scalar(value: str) -> Any:
    if value == "":
        return None
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def assert_triggers_match(plan: FaultPlan, events: list[Any]) -> None:
    """核对 trace 里的故障与计划实际掷出的故障一致。

    输入：计划，以及带 ``event_type`` 的事件。
    输出：无。
    副作用：无。
    失败：有正概率却一条故障都没记上，或种类序列不一致时抛出 ``RuntimeError``。
    """
    traced = [
        str(_usage(event).get("fault_kind") or _reason(event))
        for event in events
        if _event_type(event) == "fault_injected"
    ]
    fired = [str(item["fault_kind"]) for item in plan.fired]
    if plan.any_probability() and plan.request_count and not traced:
        raise RuntimeError("fault cell invalid: fault_injected was not recorded")
    if traced != fired:
        raise RuntimeError(f"fault trigger mismatch trace={traced} fired={fired}")


def _usage(event: Any) -> dict:
    if isinstance(event, dict):
        usage = event.get("usage") or {}
    else:
        usage = getattr(event, "usage", {}) or {}
    return dict(usage)


def _reason(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("reason") or "")
    return str(getattr(event, "reason", "") or "")


def _event_type(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("event_type") or "")
    return str(getattr(event, "event_type", "") or "")


def _with_fault(response: httpx.Response, fault: dict[str, Any]) -> httpx.Response:
    # aread() 已经按 content-encoding 解开正文。头还留着 gzip 时，客户端会再解一次并失败。
    headers = response.headers.copy()
    headers.pop("content-encoding", None)
    headers.pop("content-length", None)
    extensions = dict(response.extensions)
    extensions["epochgate_fault"] = fault
    return httpx.Response(
        status_code=response.status_code,
        headers=headers,
        content=response.content,
        extensions=extensions,
    )


def _value_response(mode: str, fault: dict[str, Any]) -> httpx.Response:
    if mode == "truncated_json":
        return httpx.Response(
            200,
            content=b'{"choices":[',
            headers={"content-type": "application/json"},
            extensions={"epochgate_fault": fault},
        )
    if mode == "empty_tool_calls":
        body = {"choices": [{"message": {"tool_calls": []}}]}
    elif mode == "bad_arguments":
        body = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "fault-call",
                                "function": {"name": "reserve", "arguments": '{"note": 1}'},
                            }
                        ]
                    }
                }
            ]
        }
    else:
        raise ValueError(f"unknown value fault mode: {mode}")
    return httpx.Response(200, json=body, extensions={"epochgate_fault": fault})
