"""阶段 F（v1.2）离线合同测试：
- F2 observe_delay_scope（initial_only / refresh_only / all_observations）隔离验证；
- F4 FifoAdmissionScheduler（初始任务 FIFO 入队、被取消重规划进入队尾、A/B 重规划同优先级）；
- F3 六维后端取消能力合同（绝不将 stop_on_cancel=False 等同于后端不支持取消；
  验证流式可停读、流式策略对照、不支持取消后端均受 Gateway eligibility gate 保护）。
"""

from __future__ import annotations

import time
import pytest

from epochgate.backend import FakeBackend
from epochgate.http_backend import HttpToolBackend
from epochgate.http_streaming import HttpStreamingBackend
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.models import Task, TaskStatus
from epochgate.runtime import Runtime
from epochgate.scenario_driver import ScenarioDriver, make_scenario_spec
from epochgate.scheduler import FifoAdmissionScheduler
from epochgate.scripted_backend import ScriptShot, ScriptedValidityBackend
from epochgate.stage_f_runner import (
    ProvenanceMeta,
    classify_pair_trigger_status,
    enrich_row_for_stage_f,
)
from epochgate.stage_e_runner import extract_stage_e_row


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,exp_init,exp_ref",
    [
        ("initial_only", True, False),
        ("refresh_only", False, True),
        ("all_observations", True, True),
    ],
)
async def test_f2_observe_delay_scope_isolation(scope: str, exp_init: bool, exp_ref: bool) -> None:
    env = InventoryEnvironment(
        stock=2,
        schedule=[],
        seed=1,
        pace="step",
        observe_delay_s=0.08,
        observe_delay_scope=scope,
    )
    recipe = env.default_refresh()
    await env.observe(recipe)  # call 1: initial
    await env.observe(recipe)  # call 2: refresh
    assert len(env.observe_log) == 2
    c1, c2 = env.observe_log[0], env.observe_log[1]
    assert c1["observation_phase"] == "initial"
    assert c2["observation_phase"] == "refresh"
    if exp_init:
        assert c1["actual_observe_latency_s"] >= 0.06
        assert c1["configured_observe_delay_s"] == 0.08
    else:
        assert c1["actual_observe_latency_s"] < 0.04
        assert c1["configured_observe_delay_s"] == 0.0
    if exp_ref:
        assert c2["actual_observe_latency_s"] >= 0.06
        assert c2["configured_observe_delay_s"] == 0.08
    else:
        assert c2["actual_observe_latency_s"] < 0.04
        assert c2["configured_observe_delay_s"] == 0.0


@pytest.mark.asyncio
async def test_f4_fifo_scheduler_replan_goes_to_tail() -> None:
    """当 t0 的首 attempt 因外生变化取消并重规划时，若 t1 已在队列中就绪，t0 的新 attempt 排在 t1 之后（进入队尾）。"""
    ids = IdFactory(42)
    spec = make_scenario_spec(
        scenario_id="f4-fifo-unit",
        task_kind="recoverable",
        t_change_s=0.05,
        t_restore_s=0.08,
        initial_stock=2,
        restore_delta=2,
    )
    env = InventoryEnvironment(stock=2, schedule=[], seed=42, pace="step", ids=ids)
    driver = ScenarioDriver(spec=spec, env=env)
    shots = [
        # t0 first attempt (cancelled around 0.05s)
        ScriptShot(token_count=6, stop_on_cancel=True, emit_tool=True, token_interval_s=0.03),
        # t1 first attempt
        ScriptShot(token_count=2, stop_on_cancel=True, emit_tool=True, token_interval_s=0.01),
        # t0 replan attempt (dispatched after t1 because replan goes to tail)
        ScriptShot(token_count=2, stop_on_cancel=True, emit_tool=True, token_interval_s=0.01),
    ]
    backend = ScriptedValidityBackend(shots, on_first_delta=driver.start_anchor, ids=ids)
    runtime = Runtime(
        environment=env,
        backend=backend,
        scheduler=FifoAdmissionScheduler(replan_to_tail=True, ids=ids),
        ids=ids,
        run_id="fifo-replan-tail",
        max_steps=4000,
        seed=42,
        await_recovery_on_depletion=True,
    )
    now = time.monotonic()
    tasks = [
        Task(task_id="t0", arrival_mono=now, deadline_mono=now + 30.0, goal="reserve one unit"),
        Task(task_id="t1", arrival_mono=now + 0.005, deadline_mono=now + 30.0, goal="reserve one unit"),
    ]
    await runtime.run(tasks)
    driver.cancel_pending()
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    dispatched_tasks = [ev.identity.get("task_id") for ev in events if ev.event_type == "backend_request"]
    # Order of backend_request: t0 (initial) -> t1 (initial, already waiting) -> t0 (replan at tail)
    assert dispatched_tasks[0] == "t0"
    assert dispatched_tasks[1] == "t1"
    assert "t0" in dispatched_tasks[2:]
    assert runtime.registry.get_task("t0").status == TaskStatus.SUCCEEDED
    assert runtime.registry.get_task("t1").status == TaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_f3_six_dimensional_backend_capability_contract_and_gateway_protection() -> None:
    """验证 F3 六维取消能力描述：区分 stop_on_cancel=False（策略开关）与后端本身不支持取消，且所有后端均受 Gateway 门闩保护。"""
    stream_b = HttpStreamingBackend(base_url="https://example.invalid", api_key="k", model="m", stop_on_cancel=True)
    stream_a = HttpStreamingBackend(base_url="https://example.invalid", api_key="k", model="m", stop_on_cancel=False)
    http_tool = HttpToolBackend(base_url="https://example.invalid", api_key="k", model="m")
    non_cancel_fake = FakeBackend(supports_cancel=False, delay_s=0.1)

    cb = stream_b.capabilities()
    ca = stream_a.capabilities()
    ct = http_tool.capabilities()
    cf = non_cancel_fake.capabilities()

    # stop_on_cancel 只决定是否关闭本地读。aclose 不是供应商取消 API，也不是服务端 ACK。
    assert cb.supports_cancel is True and ca.supports_cancel is True
    assert cb.streaming_capability is True and ca.streaming_capability is True
    assert cb.local_read_abort is True and ca.local_read_abort is False
    assert cb.client_stream_close_issued is True and ca.client_stream_close_issued is False
    assert cb.provider_cancel_request is False and ca.provider_cancel_request is False
    assert cb.provider_cancel_api_invoked is False and ca.provider_cancel_api_invoked is False
    assert cb.provider_cancel_ack_received is False and ca.provider_cancel_ack_received is False
    assert cb.provider_cancel_ack_semantics == "UNKNOWN"
    assert ca.provider_cancel_ack_semantics == "UNKNOWN"
    assert cb.transport_disconnect_observed is False
    assert cb.server_generation_observability is False and cb.server_resource_reclaim_observability is False
    assert cb.server_generation_stopped_observed is False and cb.server_resource_reclaim_observed is False
    assert cf.provider_cancel_api_invoked is False
    assert cf.provider_cancel_ack_received is False

    # Non-streaming HttpToolBackend
    assert ct.streaming_capability is False
    assert ct.local_read_abort is False
    assert ct.provider_cancel_ack_semantics == "UNKNOWN"

    # Non-cancellable FakeBackend still protected by Gateway eligibility gate when stock drops
    env = InventoryEnvironment(stock=1, schedule=[], seed=7, pace="step")
    fired = {"done": False}

    def wipe_once() -> None:
        if not fired["done"]:
            fired["done"] = True
            env.apply_exogenous(-1)

    backend_nc = FakeBackend(supports_cancel=False, delay_s=0.15, max_inflight=1, on_start=wipe_once)
    runtime = Runtime(
        environment=env,
        backend=backend_nc,
        scheduler=FifoAdmissionScheduler(),
        max_steps=400,
    )
    now = time.monotonic()
    res = await runtime.run_task(Task(task_id="t-nc", arrival_mono=now, deadline_mono=now + 10.0, goal="reserve"))
    events = list(runtime.events.iter_events(run_id=runtime.run_id))
    assert not any(ev.event_type == "action_committed" for ev in events)
    assert env.stock == 0
    assert res.status == TaskStatus.REJECTED


def test_pair_trigger_status_and_strict_non_triggered_flags() -> None:
    assert classify_pair_trigger_status("triggered", "triggered") == "both_triggered"
    assert classify_pair_trigger_status("triggered", "trigger_not_fired") == "A_only_triggered"
    assert classify_pair_trigger_status("trigger_late", "triggered") == "B_only_triggered"
    assert classify_pair_trigger_status("trigger_not_fired", "trigger_not_fired") == "neither_triggered"
