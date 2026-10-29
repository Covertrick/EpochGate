"""http-smoke 的真实检查。本地清单不访问模型；模型段才走 HTTP 和故障场景。"""

from __future__ import annotations

import asyncio
import inspect
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from epochgate.backend import FakeBackend
from epochgate.env_app import create_app
from epochgate.environment import FakeEnvironment
from epochgate.events import EventLog
from epochgate.faults import FaultPlan, load_scenario
from epochgate.http_backend import HttpToolBackend
from epochgate.manifest import build_manifest, cell_id, write_manifest
from epochgate.metrics import _gini, _percentile
from epochgate.models import CandidateAction, InferenceRequest, Task, TaskStatus, ToolSpec
from epochgate.runtime import Runtime
from epochgate.scheduler import NearAdmissionScheduler
from epochgate.sqlite_inventory import InventoryClosed, SqliteInventory


def evaluate_cell(plan: FaultPlan, fault_count: int) -> dict[str, Any]:
    """判断这个 cell 能不能当作故障已经发生的证据。

    输入：故障计划和 trace 里的 ``fault_injected`` 条数。
    输出：``cell_valid`` 和原因。
    副作用：无。
    失败：无。概率大于 0 却没有故障时，结果是无效，不抛异常。
    """
    if plan.any_probability() and plan.request_count > 0 and fault_count == 0:
        return {
            "cell_valid": False,
            "reason": "positive probability but zero fault_injected",
        }
    if fault_count != len(plan.fired):
        return {
            "cell_valid": False,
            "reason": f"trigger mismatch trace={fault_count} fired={len(plan.fired)}",
        }
    return {"cell_valid": True, "reason": "ok"}


async def run_mechanism_checks(
    db_dir: str | Path,
    *,
    env_url: str | None = None,
    service_prior_s: float = 0.0,
) -> dict[str, Any]:
    """跑清单里不依赖模型的检查。

    输入：临时数据库目录、可选的已启动库存地址、用于不可达拒绝的先验秒数。
    输出：每项 ``passed`` 和说明。
    副作用：写临时 SQLite。``env_url`` 存在时会对那个服务提交一次预约。
    失败：单项失败记入结果，不中断其余检查。
    """
    destination = Path(db_dir)
    destination.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    checks.append(await _check_wal_inserts(destination / "wal.sqlite"))
    checks.append(await _check_close_rejects_writes(destination / "closed.sqlite"))
    checks.append(await _check_no_oversell(destination / "oversell.sqlite"))
    checks.append(await _check_idempotent_action(destination / "idem.sqlite"))
    checks.append(_check_default_commit_has_no_window())
    checks.append(await _check_window_only_when_configured(destination / "window.sqlite"))
    checks.append(await _check_lease_durations())
    checks.append(await _check_prior_zero_does_not_reject())
    checks.append(await _check_unreachable_rejects(service_prior_s))
    if env_url:
        checks.append(await _check_live_env(env_url))
    else:
        checks.append(await _check_asgi_env(destination / "asgi.sqlite"))
    return {"checks": checks, "passed": all(item["passed"] for item in checks)}


async def run_model_smoke(
    *,
    base_url: str,
    api_key: str,
    model: str,
    scenario: str | Path,
    seed: int,
    jsonl_path: str | Path,
    requests: int = 6,
) -> dict[str, Any]:
    """用真实模型地址和故障 transport 发若干次请求。

    输入：模型地址、密钥、模型名、场景、种子、trace 路径、请求数。
    输出：cell 是否有效、故障种类，以及可用于先验的服务秒数。
    副作用：访问模型；命中延迟时睡眠；写 JSONL。密钥不写入 trace。
    失败：网络错误记入 trace，不把 cell 标成有效。
    """
    plan = load_scenario(scenario, seed=seed)
    backend = HttpToolBackend(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_s=90.0,
        trust_env=True,
        fault_plan=plan,
    )
    log = EventLog()
    request = _model_request()
    samples: list[float] = []
    for index in range(requests):
        handle = await backend.submit(request)
        log.emit(
            run_id="smoke",
            event_type="backend_request",
            identity={"task_id": "smoke", "attempt_id": f"a{index}"},
        )
        started = time.perf_counter()
        fault: dict[str, Any] = {}
        async for event in backend.events(handle):
            if event.usage.get("fault_injected"):
                fault = dict(event.usage)
                log.emit(
                    run_id="smoke",
                    event_type="fault_injected",
                    identity={"task_id": "smoke", "attempt_id": f"a{index}"},
                    reason=str(event.usage.get("fault_kind") or ""),
                    usage=dict(event.usage),
                )
        elapsed = time.perf_counter() - started
        sample = _service_sample(elapsed, fault)
        if sample is not None:
            samples.append(sample)
            log.emit(
                run_id="smoke",
                event_type="service_sample",
                identity={"task_id": "smoke", "attempt_id": f"a{index}"},
                reason=f"{sample:.6f}",
                usage={"service_s": sample},
            )
    log.export_jsonl(jsonl_path, run_id="smoke")
    fault_count = sum(
        1 for event in log.iter_events(run_id="smoke") if event.event_type == "fault_injected"
    )
    verdict = evaluate_cell(plan, fault_count)
    stats = _latency_stats(samples)
    return {
        **verdict,
        "fault_count": fault_count,
        "fault_kinds": [str(item["fault_kind"]) for item in plan.fired],
        "requests": requests,
        "samples": samples,
        **stats,
    }


def service_stats(samples: list[float]) -> dict[str, float]:
    """计算服务时间的最大值和分位数。

    输入：秒数列表。
    输出：max、p50、p95、p99、gini。空列表全为 0。
    副作用：无。
    失败：无。
    """
    return _latency_stats(samples)


async def _check_wal_inserts(path: Path) -> dict[str, Any]:
    store = SqliteInventory(path)
    await store.setup()

    async def write_batch(worker: int) -> None:
        for index in range(25):
            await store.append_record(f"w{worker}-{index}", f"w{worker}")

    try:
        await asyncio.gather(*(write_batch(worker) for worker in range(8)))
        count, distinct = await store.record_stats()
        passed = count == 200 and distinct == 200
        detail = f"count={count} distinct={distinct}"
    except Exception as exc:
        passed = False
        detail = str(exc)
    return {"name": "sqlite_wal_200", "passed": passed, "detail": detail}


async def _check_close_rejects_writes(path: Path) -> dict[str, Any]:
    store = SqliteInventory(path, stock=2)
    await store.setup()
    await store.close()
    await store.close()
    try:
        await store.append_record("after-close", "w")
        return {"name": "close_rejects_write", "passed": False, "detail": "write succeeded after close"}
    except InventoryClosed:
        return {"name": "close_rejects_write", "passed": True, "detail": "InventoryClosed"}


async def _check_no_oversell(path: Path) -> dict[str, Any]:
    store = SqliteInventory(path, stock=1)
    await store.setup()
    _qty, version = await store.read_stock()
    results = await asyncio.gather(
        *(
            store.commit_action(_reserve(f"act-{index}"), {"source_version": version})
            for index in range(8)
        )
    )
    accepted = sum(result.accepted for result in results)
    qty, _version = await store.read_stock()
    passed = accepted == 1 and qty == 0
    return {"name": "no_oversell", "passed": passed, "detail": f"accepted={accepted} stock={qty}"}


async def _check_idempotent_action(path: Path) -> dict[str, Any]:
    store = SqliteInventory(path, stock=4)
    await store.setup()
    _qty, version = await store.read_stock()
    results = await asyncio.gather(
        *(store.commit_action(_reserve("same-action"), {"source_version": version}) for _ in range(20))
    )
    ids = {result.commit_id for result in results if result.accepted}
    qty, _version = await store.read_stock()
    passed = len(ids) == 1 and qty == 3 and all(result.accepted for result in results)
    return {"name": "idempotent_action", "passed": passed, "detail": f"commit_ids={len(ids)} stock={qty}"}


def _check_default_commit_has_no_window() -> dict[str, Any]:
    source = inspect.getsource(SqliteInventory.commit_action)
    sleep_at = source.find("await asyncio.sleep")
    guard_at = source.find("if window_s > 0:")
    passed = guard_at != -1 and sleep_at > guard_at and "window_s: float = 0.0" in source
    return {
        "name": "default_commit_has_no_window",
        "passed": passed,
        "detail": "sleep is only inside window_s > 0; default is 0",
    }


async def _check_window_only_when_configured(path: Path) -> dict[str, Any]:
    store = SqliteInventory(path, stock=2)
    await store.setup()
    _qty, version = await store.read_stock()
    results = await asyncio.gather(
        store.commit_action(_reserve("left"), {"source_version": version}, window_s=0.05),
        store.commit_action(_reserve("right"), {"source_version": version}, window_s=0.05),
    )
    conflicts = [result for result in results if not result.accepted]
    passed = sum(result.accepted for result in results) == 1 and any(
        "version conflict" in result.reasons for result in conflicts
    )
    return {"name": "window_opens_conflict", "passed": passed, "detail": f"accepted={sum(r.accepted for r in results)}"}


async def _check_lease_durations() -> dict[str, Any]:
    from epochgate.models import AttemptState, AttemptView, BackendCapacityView, SchedulerSnapshot, TaskView

    now = time.monotonic()
    snapshot = SchedulerSnapshot(
        now_mono=now,
        run_id="lease",
        tasks=[
            TaskView(
                task_id=name,
                status=TaskStatus.RUNNING,
                goal="g",
                plan_epoch=0,
                obs_revision=1,
                arrival_mono=now,
                deadline_mono=now + 100,
                task_class=name,
            )
            for name in ("strict", "loose")
        ],
        attempts=[
            AttemptView(
                attempt_id=f"a-{name}",
                task_id=name,
                plan_epoch=0,
                obs_revision=1,
                state=AttemptState.QUEUED,
                eligible=True,
                has_observations=True,
            )
            for name in ("strict", "loose")
        ],
        observations=[],
        capacity=BackendCapacityView(free_slots=2),
        estimated_service_s=0,
    )
    admits = [item for item in NearAdmissionScheduler().decide(snapshot) if item.kind == "admit" and item.lease]
    durations = {
        item.lease.quota_class: round(item.lease.expires_at_mono - now, 6)
        for item in admits
        if item.lease is not None
    }
    passed = durations.get("strict") == 5.0 and durations.get("loose") == 30.0
    return {"name": "lease_ttl", "passed": passed, "detail": str(durations)}


async def _check_prior_zero_does_not_reject() -> dict[str, Any]:
    now = time.monotonic()
    runtime = Runtime(
        environment=FakeEnvironment(stock=3),
        backend=FakeBackend(qty=1),
        scheduler=NearAdmissionScheduler(),
        service_prior_s=0.0,
    )
    result = await runtime.run_task(
        Task(task_id="no-prior", arrival_mono=now, deadline_mono=now + 30, goal="reserve one unit")
    )
    rejected = [
        event
        for event in runtime.events.iter_events(run_id=runtime.run_id)
        if event.event_type == "admission_rejected"
    ]
    passed = result.status == TaskStatus.SUCCEEDED and not rejected
    return {"name": "prior_zero_admits", "passed": passed, "detail": result.status.value}


async def _check_unreachable_rejects(service_prior_s: float) -> dict[str, Any]:
    prior = service_prior_s if service_prior_s > 0 else 5.0
    now = time.monotonic()
    runtime = Runtime(
        environment=FakeEnvironment(stock=3),
        backend=FakeBackend(qty=1, max_inflight=1),
        scheduler=NearAdmissionScheduler(),
        service_prior_s=prior,
    )
    results = await runtime.run(
        [
            Task(
                task_id="late",
                arrival_mono=now,
                deadline_mono=now + min(0.5, prior / 2),
                goal="reserve one unit",
                task_class="strict",
            ),
            Task(
                task_id="ok",
                arrival_mono=now,
                deadline_mono=now + prior + 30,
                goal="reserve one unit",
                task_class="loose",
            ),
        ]
    )
    by_id = {task.task_id: task.status for task in results}
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    late_rejected = any(
        event.event_type == "admission_rejected" and event.identity.get("task_id") == "late" for event in events
    )
    late_dispatched = any(
        event.event_type == "backend_request" and event.identity.get("task_id") == "late" for event in events
    )
    passed = by_id["late"] == TaskStatus.REJECTED and late_rejected and not late_dispatched and by_id["ok"] == TaskStatus.SUCCEEDED
    return {
        "name": "unreachable_rejected",
        "passed": passed,
        "detail": f"prior={prior} late={by_id['late'].value} ok={by_id['ok'].value}",
    }


async def _check_asgi_env(path: Path) -> dict[str, Any]:
    store = SqliteInventory(path, stock=2)
    await store.setup()
    app = create_app(store)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://inventory") as client:
        return await _commit_once(client, "asgi_env")


async def _check_live_env(env_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=env_url, trust_env=False, timeout=10.0) as client:
        try:
            return await _commit_once(client, "live_env")
        except httpx.HTTPError as exc:
            return {"name": "live_env", "passed": False, "detail": str(exc)}


async def _commit_once(client: httpx.AsyncClient, name: str) -> dict[str, Any]:
    observed = await client.post("/observe", json={"read_tool": "read_inventory"})
    observed.raise_for_status()
    version = observed.json()["observations"][0]["source_version"]
    body = {
        "action_id": f"{name}-1",
        "task_id": name,
        "attempt_id": f"{name}-attempt",
        "plan_epoch": 0,
        "obs_revision": 1,
        "action_type": "reserve",
        "parameters": {"qty": 1},
        "observation_ids": [],
        "conditions": {"source_id": "inventory", "source_version": version},
        "window_s": 0.0,
    }
    first = await client.post("/commit", json=body)
    second = await client.post("/commit", json=body)
    first.raise_for_status()
    second.raise_for_status()
    first_body = first.json()
    second_body = second.json()
    passed = bool(first_body["accepted"]) and second_body["commit_id"] == first_body["commit_id"]
    return {"name": name, "passed": passed, "detail": f"commit_id={first_body['commit_id']}"}


def _reserve(action_id: str) -> CandidateAction:
    return CandidateAction(
        action_id=action_id,
        task_id="t",
        attempt_id="a",
        plan_epoch=0,
        obs_revision=0,
        action_type="reserve",
        parameters={"qty": 1},
    )


def _model_request() -> InferenceRequest:
    return InferenceRequest(
        attempt_id="smoke",
        task_id="smoke",
        plan_epoch=0,
        obs_revision=0,
        goal="call the reserve tool once",
        tools=[
            ToolSpec(
                name="reserve",
                role="effect",
                description="Reserve units of stock",
                parameters={
                    "type": "object",
                    "properties": {"qty": {"type": "integer"}},
                    "required": ["qty"],
                },
            )
        ],
        context={"observations": [], "messages": []},
    )


def _service_sample(elapsed: float, fault: dict[str, Any]) -> float | None:
    kind = str(fault.get("fault_kind") or "")
    if kind in {"value_fault", "crash"}:
        return None
    if kind == "latency_spike":
        return max(0.0, elapsed - float(fault.get("delay_s") or 0))
    return elapsed


def _latency_stats(samples: list[float]) -> dict[str, float]:
    return {
        "service_max": max(samples) if samples else 0.0,
        "service_p50": _percentile(samples, 0.50),
        "service_p95": _percentile(samples, 0.95),
        "service_p99": _percentile(samples, 0.99),
        "service_gini": _gini(samples),
    }


async def run_http_smoke(
    *,
    base_url: str,
    api_key: str,
    model: str,
    scenario: str | Path,
    seed: int,
    out_dir: str | Path,
    env_url: str | None = None,
    requests: int = 6,
    service_prior_s: float | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """跑本地清单和真实模型故障请求，并写出不含密钥的结果。

    输入：模型配置、场景、种子、输出目录、可选库存地址和先验。
    输出：检查结果、cell 是否有效、服务时间分位数。
    副作用：写 JSONL、manifest 和 ``service_prior.json``。可能访问模型和库存服务。
    失败：单项失败记在结果里。``ok`` 为假时调用方应非零退出。
    """
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="epochgate-smoke-") as scratch:
        checks = await run_mechanism_checks(Path(scratch), env_url=env_url, service_prior_s=0.0)
    model_report = await run_model_smoke(
        base_url=base_url,
        api_key=api_key,
        model=model,
        scenario=scenario,
        seed=seed,
        jsonl_path=destination / "trace.jsonl",
        requests=requests,
    )
    measured = float(model_report["service_p50"])
    prior = measured if service_prior_s is None else service_prior_s
    if prior > 0:
        extra = await _check_unreachable_rejects(prior)
        extra["name"] = "unreachable_rejected_with_prior"
        extra["detail"] = f"configured_prior={prior} {extra['detail']}"
        checks["checks"].append(extra)
        checks["passed"] = all(item["passed"] for item in checks["checks"])
    (destination / "service_prior.json").write_text(
        _dump(
            {
                "service_prior_s": prior,
                "measured_p50": measured,
                "service_max": model_report["service_max"],
                "service_p50": model_report["service_p50"],
                "service_p95": model_report["service_p95"],
                "service_p99": model_report["service_p99"],
                "service_gini": model_report["service_gini"],
                "note": "这些数字只说明本次请求的耗时分布，不是性能结论。",
            }
        ),
        encoding="utf-8",
    )
    checks_id = write_smoke_manifest(
        destination / "manifest.json",
        seed=seed,
        scenario=str(scenario),
        checks_passed=bool(checks["passed"]),
        cell_valid=bool(model_report["cell_valid"]),
        requests=requests,
        service_prior_s=prior,
        root=root,
    )
    report = {
        "ok": bool(checks["passed"]) and bool(model_report["cell_valid"]),
        "checks_passed": checks["passed"],
        "checks": checks["checks"],
        "cell_valid": model_report["cell_valid"],
        "cell_reason": model_report["reason"],
        "fault_count": model_report["fault_count"],
        "fault_kinds": model_report["fault_kinds"],
        "service_p50": measured,
        "service_prior_s": prior,
        "checks_cell_id": checks_id,
        "env_url": env_url,
        "api_key_recorded": "set",
    }
    (destination / "report.json").write_text(_dump(report), encoding="utf-8")
    return report


def _dump(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_smoke_manifest(
    path: str | Path,
    *,
    seed: int,
    scenario: str | None,
    checks_passed: bool,
    cell_valid: bool,
    requests: int,
    service_prior_s: float,
    root: str | Path | None = None,
) -> str:
    """写出只含配置、不含密钥的 smoke manifest。

    输入：路径、种子、场景、检查结果和先验。
    输出：本地检查 cell 的 id。
    副作用：覆盖 manifest 文件。
    失败：目录不可写时向上抛。
    """
    checks_id = cell_id("checks", seed, None)
    cells = [
        {
            "cell_id": checks_id,
            "kind": "checks",
            "policy": "checks",
            "seed": seed,
            "scenario": None,
            "backend": "sqlite",
            "cell_valid": checks_passed,
            "service_prior_s": service_prior_s,
        }
    ]
    if scenario is not None:
        cells.append(
            {
                "cell_id": cell_id("smoke", seed, scenario),
                "kind": "smoke",
                "policy": "smoke",
                "seed": seed,
                "scenario": str(scenario),
                "backend": "http",
                "requests": requests,
                "cell_valid": cell_valid,
                "service_prior_s": service_prior_s,
                "base_url": "from-env",
            }
        )
    write_manifest(path, build_manifest(cells, root=root))
    return checks_id
