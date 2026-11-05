"""运行时闭环：环境状态变化驱动三态决策，Gateway 决定能不能提交。

谓词是库存阈值，不是语言模型的语义判断。vLLM 路径上的取消回执只表示客户端已经关掉流。
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import httpx

from epochgate.decision_policy import DecisionInput, RuntimeAction, decide
from epochgate.events import EventLog
from epochgate.gateway import ActionGateway
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.models import AdmissionLease, BackendHandle, CancelReceipt, CandidateAction, InferenceRequest, Task
from epochgate.observation import ObservationStore
from epochgate.registry import Registry
from epochgate.scripted_backend import ScriptShot, ScriptedValidityBackend

SCENARIOS = ("predicate-invalid", "predicate-valid", "candidate-formed", "version-race")
BATCHES = 3
TASKS_PER_BATCH = 20


class Generation(Protocol):
    async def start(self, request: InferenceRequest) -> BackendHandle: ...

    async def cancel(self, handle: BackendHandle) -> CancelReceipt: ...

    async def finish(self, handle: BackendHandle) -> dict[str, Any]: ...

    async def resource_state(self) -> dict[str, Any]: ...


class ScriptedGeneration:
    """单测和离线闭环用的剧本后端。取消回执来自本地循环，不是 GPU。"""

    def __init__(self) -> None:
        self._backend = ScriptedValidityBackend([ScriptShot(token_count=4, stop_on_cancel=True, emit_tool=False)])
        self._tasks: dict[str, Any] = {}

    async def start(self, request: InferenceRequest) -> BackendHandle:
        handle = await self._backend.submit(request)
        agen = self._backend.events(handle).__aiter__()

        async def _drain() -> int:
            count = 0
            async for _event in agen:
                count += 1
            return count

        import asyncio

        self._tasks[handle.handle_id] = asyncio.create_task(_drain())
        return handle

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        receipt = await self._backend.cancel(handle)
        task = self._tasks.pop(handle.handle_id, None)
        if task is not None:
            await task
        return receipt

    async def finish(self, handle: BackendHandle) -> dict[str, Any]:
        task = self._tasks.pop(handle.handle_id, None)
        tokens = await task if task is not None else 0
        return {"finished": True, "events": tokens}

    async def resource_state(self) -> dict[str, Any]:
        return {"engine": "scripted", "provider_reclaim_observed": False}


class VllmGeneration:
    """对本机 vLLM 发短补全。断流只记客户端关闭，不记成供应商回收。"""

    def __init__(self, base_url: str = "http://127.0.0.1:8000", model: str = "Qwen/Qwen2.5-1.5B-Instruct") -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=60.0, trust_env=False)
        self._streams: dict[str, tuple[Any, int]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def start(self, request: InferenceRequest) -> BackendHandle:
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": request.goal}],
            "max_tokens": 8,
            "temperature": 0,
            "stream": True,
        }
        stream_cm = self._client.stream("POST", f"{self._base}/v1/chat/completions", json=payload)
        response = await stream_cm.__aenter__()
        if response.status_code != 200:
            body = await response.aread()
            await stream_cm.__aexit__(None, None, None)
            raise RuntimeError(f"vLLM HTTP {response.status_code}: {body[:200]!r}")
        handle = BackendHandle(handle_id=request.attempt_id, attempt_id=request.attempt_id)
        lines = response.aiter_lines()
        chunks = 0
        async for line in lines:
            if line.startswith("data:"):
                chunks = 1
                break
        self._streams[handle.handle_id] = (stream_cm, response, lines, chunks)
        return handle

    async def _close(self, handle: BackendHandle) -> tuple[Any, Any, Any, int]:
        stream_cm, response, lines, chunks = self._streams.pop(handle.handle_id)
        await stream_cm.__aexit__(None, None, None)
        return stream_cm, response, lines, chunks

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        _stream_cm, _response, _lines, chunks = await self._close(handle)
        return CancelReceipt(
            handle_id=handle.handle_id,
            acknowledged=False,
            evidence=f"client stream closed after {chunks} chunks; provider abort ack was not observed",
            grade="UNKNOWN",
            reclaimed=False,
            connection_closed=True,
        )

    async def finish(self, handle: BackendHandle) -> dict[str, Any]:
        stream_cm, _response, lines, chunks = self._streams.pop(handle.handle_id)
        async for line in lines:
            if line.startswith("data:"):
                chunks += 1
        await stream_cm.__aexit__(None, None, None)
        return {"finished": True, "chunks": chunks}

    async def resource_state(self) -> dict[str, Any]:
        try:
            response = await self._client.get(f"{self._base}/metrics", timeout=2.0)
            running = None
            for line in response.text.splitlines():
                if line.startswith("vllm:num_requests_running"):
                    running = float(line.split()[-1])
            return {"engine": "vllm", "num_requests_running": running, "provider_reclaim_observed": False}
        except httpx.HTTPError as exc:
            return {"engine": "vllm", "metrics_error": type(exc).__name__, "provider_reclaim_observed": False}


def _inputs(scenario: str, index: int) -> DecisionInput:
    if scenario == "predicate-invalid":
        return DecisionInput(False, False, 8.0, 4.0, remaining_service_error=0.2, restart_service_error=0.2, safety_buffer=0.1)
    if scenario == "predicate-valid":
        if index % 2 == 0:
            return DecisionInput(True, False, 2.0, 9.0, remaining_service_error=0.1, restart_service_error=0.1, safety_buffer=0.1)
        return DecisionInput(True, False, 5.0, 5.0, remaining_service_error=0.2, restart_service_error=0.2, safety_buffer=0.1)
    return DecisionInput(True, True, 8.0, 3.0, remaining_service_error=0.1, restart_service_error=0.1, safety_buffer=0.1)


async def run_attempt(generation: Generation, *, scenario: str, batch: int, index: int) -> dict[str, Any]:
    """一次 attempt：先改环境，再决策，再按动作取消、提交或继续。"""
    ids = IdFactory(batch * 1000 + index + 1)
    env = InventoryEnvironment(stock=5, schedule=[], seed=index + 1, ids=ids)
    log = EventLog()
    registry = Registry(log, run_id=f"b{batch}-{scenario}-{index}", ids=ids)
    observations = ObservationStore(ids=ids)
    gateway = ActionGateway(env, registry, observations, log, run_id=registry._run_id)
    now = time.monotonic()
    task = Task(task_id="t", arrival_mono=now, deadline_mono=now + 60, goal="reserve one unit")
    registry.register_task(task)
    attempt = registry.begin_attempt(task.task_id)
    if scenario == "predicate-invalid":
        env.apply_exogenous(-5)
    observed = await env.observe(env.default_refresh())
    obs = observations.record(observed.observations[0], task_id=task.task_id)
    predicate_valid = env.stock >= 1
    decision_input = _inputs(scenario, index)
    if decision_input.predicate_valid != predicate_valid and scenario == "predicate-invalid":
        decision_input = DecisionInput(
            False,
            decision_input.candidate_formed,
            decision_input.remaining_service_estimate,
            decision_input.restart_service_estimate,
            decision_input.remaining_service_error,
            decision_input.restart_service_error,
            decision_input.safety_buffer,
        )
    if decision_input.predicate_valid != predicate_valid and scenario != "predicate-invalid":
        raise RuntimeError(f"stock predicate drifted for {scenario}: stock={env.stock}")
    decision = decide(decision_input)
    request = InferenceRequest(
        attempt_id=attempt.attempt_id,
        task_id=task.task_id,
        plan_epoch=attempt.plan_epoch,
        obs_revision=attempt.obs_revision,
        goal="Reply with the single word ready.",
        observation_ids=[obs.observation_id],
    )
    handle = await generation.start(request)
    plan_epoch, obs_revision = registry.current_epoch(task.task_id)
    candidate = CandidateAction(
        action_id=f"act-{batch}-{index}",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        plan_epoch=plan_epoch,
        obs_revision=obs_revision,
        action_type="reserve",
        parameters={"qty": 1},
        observation_ids=[obs.observation_id],
    )
    lease = AdmissionLease(
        lease_id=f"lease-{batch}-{index}",
        attempt_id=attempt.attempt_id,
        earliest_start_mono=now,
        expires_at_mono=now + 60,
    )
    gateway_result: dict[str, Any] | None = None
    cancel_receipt: dict[str, Any] | None = None
    generation_end: dict[str, Any] | None = None
    if decision.action is RuntimeAction.ABORT_AND_REPLAN:
        cancel_receipt = (await generation.cancel(handle)).model_dump()
        registry.supersede_attempt(attempt.attempt_id, reason="predicate-invalid")
        rejected = await gateway.submit(candidate, lease)
        gateway_result = {"accepted": rejected.accepted, "reasons": list(rejected.reasons)}
    elif decision.action is RuntimeAction.ACCEPT_IF_VALID and scenario == "version-race":
        registry.advance_plan_epoch(task.task_id, reason="version-race")
        rejected = await gateway.submit(candidate, lease)
        gateway_result = {"accepted": rejected.accepted, "reasons": list(rejected.reasons)}
        generation_end = await generation.finish(handle)
    elif decision.action is RuntimeAction.ACCEPT_IF_VALID:
        accepted = await gateway.submit(candidate, lease)
        gateway_result = {"accepted": accepted.accepted, "reasons": list(accepted.reasons)}
        generation_end = await generation.finish(handle)
    else:
        generation_end = await generation.finish(handle)
    return {
        "batch": batch,
        "index": index,
        "scenario": scenario,
        "predicate_valid": predicate_valid,
        "stock": env.stock,
        "decision": decision.action.value,
        "reason": decision.reason,
        "delta": decision.delta,
        "margin": decision.margin,
        "estimate_error": {
            "remaining": decision_input.remaining_service_error,
            "restart": decision_input.restart_service_error,
            "safety_buffer": decision_input.safety_buffer,
        },
        "gateway": gateway_result,
        "cancel_receipt": cancel_receipt,
        "generation": generation_end,
        "resource_state": await generation.resource_state(),
    }


def summarize(rows: list[dict[str, Any]], *, engine: str) -> dict[str, Any]:
    actions = sorted({row["decision"] for row in rows})
    stale_accepted = sum(1 for row in rows if row["scenario"] == "version-race" and row["gateway"] and row["gateway"]["accepted"])
    old_accepted = sum(1 for row in rows if row["scenario"] == "predicate-invalid" and row["gateway"] and row["gateway"]["accepted"])
    abort_rows = [row for row in rows if row["decision"] == RuntimeAction.ABORT_AND_REPLAN.value]
    receipts = sum(1 for row in abort_rows if row["cancel_receipt"] and row["cancel_receipt"]["connection_closed"])
    return {
        "engine": engine,
        "attempts": len(rows),
        "actions": actions,
        "scenarios": sorted({row["scenario"] for row in rows}),
        "stale_accepted_commit": stale_accepted,
        "old_attempt_accepted": old_accepted,
        "abort_attempts": len(abort_rows),
        "cancel_receipts_closed": receipts,
        "provider_reclaim_observed": False,
        "predicate": "inventory stock >= 1",
    }


async def run_matrix(generation: Generation, *, batches: int = BATCHES, tasks: int = TASKS_PER_BATCH) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        for batch in range(batches):
            for index in range(tasks):
                row = await run_attempt(generation, scenario=scenario, batch=batch, index=index)
                rows.append(row)
                print(f"{len(rows)} {scenario} b{batch} {row['decision']}", flush=True)
    return rows
