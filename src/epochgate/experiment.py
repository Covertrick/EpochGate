"""同一外生种子上比较策略，并写出可复现的 manifest。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from epochgate.backend import FakeBackend
from epochgate.events import EventLog
from epochgate.faults import assert_triggers_match, load_scenario
from epochgate.http_backend import HttpToolBackend
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.manifest import build_manifest, cell_id, find_cell, load_manifest, write_manifest
from epochgate.metrics import score_trace
from epochgate.models import InferenceRequest, Task, ToolSpec
from epochgate.runtime import Runtime
from epochgate.scheduler import (
    EdfFixedScheduler,
    EvdfScheduler,
    FcfsEnqueueScheduler,
    FcfsRecheckScheduler,
    NearAdmissionScheduler,
)


def make_tasks(now: float) -> list[Task]:
    """构造三笔预约。截止时间不同，用来区分 FCFS 和最早截止。

    输入：起点 monotonic 时间。
    输出：任务列表。
    副作用：无。
    失败：无。
    """
    return [
        Task(
            task_id="t0",
            arrival_mono=now,
            deadline_mono=now + 50.0,
            goal="reserve one unit",
        ),
        Task(
            task_id="t1",
            arrival_mono=now + 1.0,
            deadline_mono=now + 10.0,
            goal="reserve one unit",
        ),
        Task(
            task_id="t2",
            arrival_mono=now + 2.0,
            deadline_mono=now + 40.0,
            goal="reserve one unit",
        ),
    ]


async def run_once(*, seed: int, policy: str, jsonl_path: str | Path) -> dict[str, Any]:
    """用给定种子跑一种策略，并把 trace 写成 JSONL。

    输入：种子、``b0`` / ``b1`` / ``b2`` / ``near`` / ``evdf``、输出路径。
    输出：``score_trace`` 的结果。
    副作用：写 JSONL；在进程内库存上提交动作。
    失败：未知策略名抛出 ``ValueError``。
    """
    ids = IdFactory(seed)
    schedulers = {
        "b0": FcfsEnqueueScheduler,
        "b1": EdfFixedScheduler,
        "b2": FcfsRecheckScheduler,
        "near": NearAdmissionScheduler,
        "evdf": EvdfScheduler,
    }
    if policy not in schedulers:
        raise ValueError(f"unknown policy: {policy}")
    scheduler = schedulers[policy](ids=ids)
    environment = InventoryEnvironment(stock=1, seed=seed, schedule=[], ids=ids)
    backend = FakeBackend(qty=1, max_inflight=1, ids=ids)
    runtime = Runtime(
        environment=environment,
        backend=backend,
        scheduler=scheduler,
        ids=ids,
        run_id=f"{policy}-seed-{seed}",
        inline_dispatch=True,
    )
    await runtime.run(make_tasks(time.monotonic()))
    runtime.events.export_jsonl(jsonl_path, run_id=runtime.run_id)
    return score_trace(jsonl_path)


async def run_chaos_cell(
    *,
    seed: int,
    scenario: str | Path,
    jsonl_path: str | Path,
    requests: int = 4,
) -> dict[str, Any]:
    """用本地上游跑故障场景，并核对故障确实写进了 trace。

    输入：种子、场景文件、输出路径、请求次数。
    输出：``score_trace`` 的结果。
    副作用：写 JSONL。命中延迟时会睡眠。
    失败：有正概率却没有 ``fault_injected``，或序列与计划不一致时抛出 ``RuntimeError``。
    """
    plan = load_scenario(scenario, seed=seed)
    backend = HttpToolBackend(
        base_url="http://fault.local/v1",
        api_key="local",
        model="local",
        trust_env=False,
        transport=_ReadyTransport(),
        fault_plan=plan,
    )
    log = EventLog()
    request = InferenceRequest(
        attempt_id="chaos",
        task_id="chaos",
        plan_epoch=0,
        obs_revision=0,
        goal="reserve",
        tools=[
            ToolSpec(
                name="reserve",
                role="effect",
                parameters={
                    "type": "object",
                    "properties": {"qty": {"type": "integer"}},
                    "required": ["qty"],
                },
            )
        ],
    )
    for index in range(requests):
        handle = await backend.submit(request)
        log.emit(
            run_id="chaos",
            event_type="backend_request",
            identity={"task_id": "chaos", "attempt_id": f"a{index}"},
        )
        async for event in backend.events(handle):
            if event.usage.get("fault_injected"):
                log.emit(
                    run_id="chaos",
                    event_type="fault_injected",
                    identity={"task_id": "chaos"},
                    reason=str(event.usage.get("fault_kind") or ""),
                    usage=dict(event.usage),
                )
    assert_triggers_match(plan, list(log.iter_events(run_id="chaos")))
    log.export_jsonl(jsonl_path, run_id="chaos")
    return score_trace(jsonl_path)


async def run_experiment(
    *,
    seed: int,
    out_dir: str | Path,
    scenario: str | None = None,
    root: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """同一种子各跑 B0、B1、B2 和临近准入，并写 manifest。

    输入：种子、输出目录、可选故障场景和仓库根。
    输出：各 cell 的指标。
    副作用：写出 JSONL 和 ``manifest.json``。
    失败：目录不可写，或故障 cell 没有触发时向上抛。
    """
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, dict[str, Any]] = {}
    cells: list[dict[str, Any]] = []
    for policy in ("b0", "b1", "b2", "near"):
        identifier = cell_id(policy, seed, None)
        summaries[policy] = await run_once(
            seed=seed,
            policy=policy,
            jsonl_path=destination / f"{policy}-seed-{seed}.jsonl",
        )
        cells.append(
            {
                "cell_id": identifier,
                "kind": "policy",
                "policy": policy,
                "seed": seed,
                "scenario": None,
                "backend": "fake",
            }
        )
    if scenario is not None:
        identifier = cell_id("chaos", seed, scenario)
        summaries["chaos"] = await run_chaos_cell(
            seed=seed,
            scenario=scenario,
            jsonl_path=destination / f"chaos-seed-{seed}.jsonl",
        )
        cells.append(
            {
                "cell_id": identifier,
                "kind": "chaos",
                "policy": "chaos",
                "seed": seed,
                "scenario": str(scenario),
                "backend": "fault-transport",
                "requests": 4,
            }
        )
    write_manifest(destination / "manifest.json", build_manifest(cells, root=root))
    return summaries


async def reproduce_cell(
    *,
    manifest_path: str | Path,
    cell: str,
    out_dir: str | Path,
) -> dict[str, Any]:
    """按 manifest 重跑一个 cell。

    输入：manifest 路径、cell id、输出目录。
    输出：该 cell 的指标。
    副作用：再写一份 JSONL。
    失败：未知 cell，或故障没有触发时向上抛。
    """
    manifest = load_manifest(manifest_path)
    chosen = find_cell(manifest, cell)
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    if chosen.get("kind") == "checks":
        from epochgate.smoke import run_mechanism_checks

        report = await run_mechanism_checks(
            destination / "checks",
            service_prior_s=float(chosen.get("service_prior_s") or 0),
        )
        passed = sum(1 for item in report["checks"] if item["passed"])
        return {
            "tasks": len(report["checks"]),
            "on_time_success": passed,
            "fault_injection_rate": 0.0,
            "checks_passed": report["passed"],
        }
    if chosen.get("kind") == "smoke":
        import os

        from epochgate.smoke import run_model_smoke

        base_url = os.environ.get("EPOCHGATE_BASE_URL") or str(chosen.get("base_url") or "")
        api_key = os.environ.get("EPOCHGATE_API_KEY") or ""
        model_name = os.environ.get("EPOCHGATE_MODEL") or str(chosen.get("model") or "")
        if not base_url or not api_key or not model_name:
            raise RuntimeError("smoke reproduce needs EPOCHGATE_BASE_URL, EPOCHGATE_API_KEY and EPOCHGATE_MODEL")
        report = await run_model_smoke(
            base_url=base_url,
            api_key=api_key,
            model=model_name,
            scenario=str(chosen["scenario"]),
            seed=int(chosen["seed"]),
            jsonl_path=destination / f"{cell}.jsonl",
            requests=int(chosen.get("requests") or 6),
        )
        requests = int(report["requests"])
        return {
            "tasks": requests,
            "on_time_success": int(bool(report["cell_valid"])),
            "fault_injection_rate": (report["fault_count"] / requests) if requests else 0.0,
            "cell_valid": report["cell_valid"],
        }
    if chosen.get("kind") == "chaos":
        return await run_chaos_cell(
            seed=int(chosen["seed"]),
            scenario=str(chosen["scenario"]),
            jsonl_path=destination / f"{cell}.jsonl",
            requests=int(chosen.get("requests") or 4),
        )
    return await run_once(
        seed=int(chosen["seed"]),
        policy=str(chosen["policy"]),
        jsonl_path=destination / f"{cell}.jsonl",
    )


class _ReadyTransport(httpx.AsyncBaseTransport):
    """本地上游：总是返回一次合法的预约工具调用。"""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "ok",
                                "function": {"name": "reserve", "arguments": '{"qty": 1}'},
                            }
                        ]
                    }
                }
            ]
        }
        return httpx.Response(200, json=body)
