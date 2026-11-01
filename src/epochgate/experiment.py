"""同一外生种子上比较策略，并写出可复现的 manifest。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from epochgate.backend import FakeBackend
from epochgate.change_stream import ChangeStream
from epochgate.config import ExperimentConfig, load_config
from epochgate.events import EventLog
from epochgate.faults import assert_triggers_match, load_scenario
from epochgate.http_backend import HttpToolBackend
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment, plan_exogenous
from epochgate.manifest import build_manifest, cell_id, find_cell, load_manifest, write_manifest
from epochgate.metrics import bootstrap_ci, score_trace
from epochgate.models import InferenceRequest, Task, ToolSpec
from epochgate.runtime import Runtime
from epochgate.scheduler import (
    EdfFixedScheduler,
    EvdfScheduler,
    EventRefreshScheduler,
    FcfsEnqueueScheduler,
    FcfsRecheckScheduler,
    JointScheduler,
    NearAdmissionScheduler,
    NearComboScheduler,
    StrongComboScheduler,
)

MAIN_POLICIES = ("b0", "b1", "b2", "b3", "j0", "j1")
ABLATION_POLICIES = ("event", "j1-fair-off")


def make_tasks(now: float) -> list[Task]:
    """构造三笔预约。截止时间不同，用来区分 FCFS 和最早截止。

    输入：起点 monotonic 时间。
    输出：任务列表。``t0`` 先到但截止更晚，``t1`` 截止最早。
    副作用：无。
    失败：无。
    """
    return shared_tasks(now)


def shared_tasks(now: float, *, loose_long: bool = False) -> list[Task]:
    """所有策略共用的到达流。

    输入：起点，以及是否改成「短任务严格、长任务宽松」的饥饿负载。
    输出：任务列表。
    副作用：无。
    失败：无。
    """
    if loose_long:
        return [
            Task(
                task_id="long",
                arrival_mono=now,
                deadline_mono=now + 80.0,
                goal="reserve one unit",
                task_class="loose",
            ),
            Task(
                task_id="short",
                arrival_mono=now,
                deadline_mono=now + 20.0,
                goal="reserve one unit",
                task_class="strict",
            ),
        ]
    return [
        Task(
            task_id="t0",
            arrival_mono=now,
            deadline_mono=now + 50.0,
            goal="reserve one unit",
            task_class="strict",
        ),
        Task(
            task_id="t1",
            arrival_mono=now + 1.0,
            deadline_mono=now + 10.0,
            goal="reserve one unit",
            task_class="loose",
        ),
        Task(
            task_id="t2",
            arrival_mono=now + 2.0,
            deadline_mono=now + 40.0,
            goal="reserve one unit",
            task_class="strict",
        ),
    ]


def build_scheduler(policy: str, config: ExperimentConfig, ids: IdFactory):
    """按策略名构造调度器。公平权重只传给 B3、J0、J1。

    输入：策略名、冻结配置、id 工厂。
    输出：调度器实例。
    副作用：无。
    失败：未知策略名抛出 ``ValueError``。
    """
    if policy == "b0":
        return FcfsEnqueueScheduler(lease_ttl_s=config.lease_ttl_s, ids=ids)
    if policy == "b1":
        return EdfFixedScheduler(lease_ttl_s=config.lease_ttl_s, ids=ids)
    if policy == "b2":
        return FcfsRecheckScheduler(lease_ttl_s=config.lease_ttl_s, ids=ids)
    if policy == "near":
        return NearAdmissionScheduler(lease_ttl_s=config.lease_ttl_s, ids=ids)
    if policy == "evdf":
        return EvdfScheduler(lease_ttl_s=config.lease_ttl_s, ids=ids)
    if policy == "b3":
        return StrongComboScheduler(
            lease_ttl_s=config.lease_ttl_s,
            ids=ids,
            weight=config.fair_weight,
            fair_every_n=config.fair_every_n,
        )
    if policy == "j0":
        return NearComboScheduler(
            lease_ttl_s=config.lease_ttl_s,
            ids=ids,
            weight=config.fair_weight,
            fair_every_n=config.fair_every_n,
        )
    if policy == "j1":
        return JointScheduler(
            lease_ttl_s=config.lease_ttl_s,
            ids=ids,
            weight=config.fair_weight,
            fair_every_n=config.fair_every_n,
            scoring=True,
        )
    if policy == "j1-fair-off":
        tuned = ExperimentConfig(**{**config.__dict__, "fair_every_n": 0, "fair_weight": 0.0})
        return JointScheduler(
            lease_ttl_s=tuned.lease_ttl_s,
            ids=ids,
            weight=0.0,
            fair_every_n=0,
            scoring=True,
        )
    if policy == "event":
        return EventRefreshScheduler(lease_ttl_s=config.lease_ttl_s, ids=ids)
    raise ValueError(f"unknown policy: {policy}")


async def run_cell(
    *,
    seed: int,
    policy: str,
    jsonl_path: str | Path,
    config: ExperimentConfig | None = None,
    refresh_budget: int | None = None,
    min_refresh_interval_s: float | None = None,
    stock: int | None = None,
    max_inflight: int | None = None,
    schedule: list[tuple[float, int]] | None = None,
    change_mode: str | None = None,
    observe_delay_s: float = 0.0,
    supports_cancel: bool = True,
    tasks: list[Task] | None = None,
    window_s: float | None = None,
    fair_every_n: int | None = None,
    service_prior_s: float | None = None,
) -> dict[str, Any]:
    """在同一份外生计划和到达流上跑一种策略。

    输入：种子、策略名、输出路径，以及可选的冻结配置覆盖。
    输出：``score_trace`` 的结果，并带上 ``observe_calls``。
    副作用：写 JSONL。外生增量按轮询逐步生效。
    失败：未知策略名抛出 ``ValueError``。运行中断时仍写出已有事件。
    """
    chosen = config or load_config()
    if policy == "j1-fair-off":
        chosen = ExperimentConfig(**{**chosen.__dict__, "fair_every_n": 0, "fair_weight": 0.0})
    if fair_every_n is not None or service_prior_s is not None:
        chosen = ExperimentConfig(
            **{
                **chosen.__dict__,
                "fair_every_n": chosen.fair_every_n if fair_every_n is None else fair_every_n,
                "service_prior_s": chosen.service_prior_s if service_prior_s is None else service_prior_s,
            }
        )
    ids = IdFactory(seed)
    environment = InventoryEnvironment(
        stock=chosen.stock if stock is None else stock,
        seed=seed,
        schedule=plan_exogenous(seed, chosen.exogenous_n) if schedule is None else schedule,
        ids=ids,
        pace="step",
        observe_delay_s=observe_delay_s,
    )
    wrapped = ChangeStream(environment, mode=change_mode, seed=seed)
    runtime = Runtime(
        environment=wrapped,
        backend=FakeBackend(
            qty=1,
            max_inflight=chosen.max_inflight if max_inflight is None else max_inflight,
            supports_cancel=supports_cancel,
            ids=ids,
        ),
        scheduler=build_scheduler(policy, chosen, ids),
        ids=ids,
        run_id=f"{policy}-seed-{seed}",
        inline_dispatch=True,
        refresh_budget=chosen.refresh_budget if refresh_budget is None else refresh_budget,
        min_refresh_interval_s=(
            chosen.min_refresh_interval_s if min_refresh_interval_s is None else min_refresh_interval_s
        ),
        window_s=chosen.window_s if window_s is None else window_s,
        fair_every_n=chosen.fair_every_n,
        fair_weight=chosen.fair_weight,
        kappa=chosen.kappa,
        epsilon=chosen.epsilon,
        observe_cost_s=chosen.observe_cost_s,
        risk_lambda=chosen.risk_lambda,
        service_prior_s=chosen.service_prior_s,
        lease_strict_s=chosen.lease_strict_s,
        lease_loose_s=chosen.lease_loose_s,
        max_steps=160,
    )
    try:
        await runtime.run(shared_tasks(time.monotonic()) if tasks is None else tasks)
    finally:
        runtime.events.export_jsonl(jsonl_path, run_id=runtime.run_id)
    scored = score_trace(jsonl_path)
    scored["observe_calls"] = environment.observe_calls
    scored["performance_claim"] = 0
    return scored


async def run_once(*, seed: int, policy: str, jsonl_path: str | Path) -> dict[str, Any]:
    """用统一负载跑一种策略。保留这个名字，供原来的重放测试调用。

    输入：种子、策略名、输出路径。
    输出：``score_trace`` 的结果。
    副作用：写 JSONL。
    失败：未知策略名抛出 ``ValueError``。
    """
    return await run_cell(seed=seed, policy=policy, jsonl_path=jsonl_path)


async def run_freshness_cell(
    *,
    seed: int,
    policy: str,
    jsonl_path: str | Path,
    refresh_budget: int = 8,
    min_refresh_interval_s: float = 0.05,
) -> dict[str, Any]:
    """在统一外生负载上跑一种策略。``refresh_budget`` 覆盖冻结配置。

    输入：种子、策略名、输出路径、刷新预算和最小刷新间隔。
    输出：``score_trace`` 的结果，并带上 ``observe_calls``。
    副作用：写 JSONL。外生增量按轮询逐步生效。
    失败：未知策略名抛出 ``ValueError``。运行中断时仍会把已经发生的事件写出去。
    """
    return await run_cell(
        seed=seed,
        policy=policy,
        jsonl_path=jsonl_path,
        refresh_budget=refresh_budget,
        min_refresh_interval_s=min_refresh_interval_s,
    )


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
    """同一种子跑 B0–B3、J0、J1，以及 event 和关闭公平份额的 J1。

    输入：种子、输出目录、可选故障场景和仓库根。
    输出：各 cell 的指标。摘要里带 bootstrap 区间。
    副作用：写出 JSONL 和 ``manifest.json``。
    失败：目录不可写，或故障 cell 没有触发时向上抛。
    """
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, dict[str, Any]] = {}
    cells: list[dict[str, Any]] = []
    for policy in (*MAIN_POLICIES, *ABLATION_POLICIES, "near"):
        identifier = cell_id(policy, seed, None)
        summaries[policy] = await run_cell(
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
                "exogenous_seed": seed,
                "fault_injected_count": 0,
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
                "exogenous_seed": seed,
                "fault_injected_count": int(summaries["chaos"].get("fault_count") or 0),
            }
        )
    policy_rows = [summaries[name] for name in MAIN_POLICIES if name in summaries]
    summaries["bootstrap"] = bootstrap_ci(
        policy_rows,
        ["effective_throughput", "stale_rejects", "wait_p50", "wait_p95", "wait_p99"],
        seed=seed,
    )
    write_manifest(destination / "manifest.json", build_manifest(cells, root=root, exogenous_seed=seed))
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
    return await run_cell(
        seed=int(chosen["seed"]),
        policy=str(chosen["policy"]),
        jsonl_path=destination / f"{cell}.jsonl",
        refresh_budget=int(chosen["refresh_budget"]) if chosen.get("refresh_budget") is not None else None,
        min_refresh_interval_s=(
            float(chosen["min_refresh_interval_s"]) if chosen.get("min_refresh_interval_s") is not None else None
        ),
    )


def trace_signature(path: str | Path) -> list[tuple[str, str | None, str | None]]:
    """抽出事件类型、原因和任务 id，用来比对两次重放。

    输入：JSONL 路径。
    输出：三元组列表。不含绝对时间。
    副作用：只读文件。
    失败：某行不是 JSON 时向上抛。
    """
    import json

    rows: list[tuple[str, str | None, str | None]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        identity = event.get("identity") or {}
        rows.append((event["event_type"], event.get("reason"), identity.get("task_id")))
    return rows


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
