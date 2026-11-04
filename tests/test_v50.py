"""v5.0 角色、统计、黑盒残余禁令、脚本残余后端。"""

from __future__ import annotations

import asyncio

import pytest

from epochgate.residual_backend import ResidualTokenBackend
from epochgate.v50_metrics import (
    extract_role_row,
    linear_percentile,
    local_e2e_separated,
    sign_tau,
    summarize_bystanders,
)
from epochgate.v50_runner import arrival_offset, build_task_plan, run_v50_trace


def test_arrival_offset_shared_across_arms() -> None:
    assert arrival_offset(6000, "target", 0) == 0.0
    a = arrival_offset(6000, "bystander", 3)
    b = arrival_offset(6000, "bystander", 3)
    assert a == b
    assert 0 <= a <= 12


def test_build_task_plan_roles() -> None:
    plan = build_task_plan(6001, bystanders_b=8)
    roles = [p["workflow_role"] for p in plan]
    assert roles.count("target") == 1
    assert roles.count("bystander") == 8


def test_percentile_and_sign_tau() -> None:
    xs = [float(i) for i in range(8)]
    p = linear_percentile(xs, 0.95)
    assert p is not None and p > xs[5]
    assert sign_tau(0.01, 0.05) == 0
    assert sign_tau(0.2, 0.05) == 1
    assert local_e2e_separated(0.2, -0.2, 0.05)
    assert not local_e2e_separated(0.01, 0.01, 0.05)


def test_summarize_requires_b_ge_2() -> None:
    rows = [{"workflow_role": "bystander", "W_slot_s": 1.0, "T_e2e_s": 2.0}]
    s = summarize_bystanders(rows, tau=0.05)
    assert s["W_slot_p95_s"] is None


@pytest.mark.asyncio
async def test_ds_forbids_g2_residual() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        await run_v50_trace(
            seed=1,
            arm="G2-residual",
            backend_kind="ds",
            cell_id="x",
            pair_id="x",
            batch_id="x",
        )


@pytest.mark.asyncio
async def test_scripted_g0_vs_g2_local_runs() -> None:
    g0 = await run_v50_trace(
        seed=6000,
        arm="G0",
        backend_kind="scripted",
        cell_id="t",
        pair_id="t",
        batch_id="t-g0",
        bystanders_b=8,
        scripted_max_tokens=4,
        scripted_token_interval_s=0.0,
    )
    g2 = await run_v50_trace(
        seed=6000,
        arm="G2-local",
        backend_kind="scripted",
        cell_id="t",
        pair_id="t",
        batch_id="t-g2",
        bystanders_b=8,
        scripted_max_tokens=4,
        scripted_token_interval_s=0.0,
    )
    assert g0["summary"]["bystander_count"] == 8
    assert g2["summary"]["bystander_count"] == 8
    assert all(r["backend_stop_status"] in {"stopped", "delayed", "unknown"} for r in g0["rows"])


@pytest.mark.asyncio
async def test_residual_backend_holds_token() -> None:
    backend = ResidualTokenBackend(
        residual_hold_s=0.05,
        token_interval_s=0.05,
        max_tokens=20,
        backend_slots=1,
        stop_on_cancel=True,
    )
    from epochgate.models import InferenceRequest

    handle = await backend.submit(
        InferenceRequest(
            attempt_id="a1",
            task_id="t1",
            plan_epoch=0,
            obs_revision=0,
            goal="test",
            tools=[],
        )
    )

    async def _consume() -> None:
        async for _ev in backend.events(handle):
            pass

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.08)
    receipt = await backend.cancel(handle)
    assert receipt.acknowledged
    await task
    assert any(x.startswith("residual:") for x in backend.trace)


def test_extract_forces_unknown_path() -> None:
    class Ev:
        def __init__(self, et: str, tid: str, mono: float) -> None:
            self.event_type = et
            self.identity = {"task_id": tid, "attempt_id": "a"}
            self.mono_time = mono

    events = [Ev("slot_acquired", "t0", 1.0), Ev("task_completed", "t0", 2.0)]
    row = extract_role_row(
        events=events,
        task_id="t0",
        workflow_role="bystander",
        event_id="e",
        arrival_mono=0.5,
        backend_stop_status="unknown",
    )
    assert row["backend_stop_status"] == "unknown"
    assert row["W_slot_s"] == pytest.approx(0.5)
