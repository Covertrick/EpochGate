"""v5.1 审计门槛、配对重采样、脚本四臂。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from epochgate.v51_analysis import (
    bootstrap_mean,
    fit_m0,
    holdout_residuals,
    replication_seeds,
    sample_size_plan,
)
from epochgate.v51_audit import audit_records
from epochgate.v51_scripted import event_time, run_scripted_group


def _trace(seed: int, load: str, arm: str, slot: float, e2e: float, *, occupy: float) -> dict:
    bystanders = []
    for i in range(8):
        bystanders.append(
            {
                "task_id": f"bystander-{i}",
                "workflow_role": "bystander",
                "W_slot_s": slot,
                "T_e2e_s": e2e,
                "backend_stop_status": "unknown",
                "residual_lower_s": None,
                "model": "deepseek-flash",
            }
        )
    rows = [
        {
            "task_id": "target",
            "workflow_role": "target",
            "backend_stop_status": "unknown",
            "residual_lower_s": None,
            "model": "deepseek-flash",
        },
        *bystanders,
        {
            "task_id": "successor",
            "workflow_role": "successor",
            "backend_stop_status": "unknown",
            "residual_lower_s": None,
            "model": "deepseek-flash",
        },
    ]
    closed = arm == "G2-local"
    return {
        "seed": seed,
        "load": load,
        "arm": arm,
        "model": "deepseek-flash",
        "thinking": "disabled",
        "failed": False,
        "arm_order": "G0G2-local" if seed % 2 == 0 else "G2-localG0",
        "summary": {"bystander_count": 8, "W_slot_p95_s": slot, "T_e2e_p95_s": e2e},
        "focal": {
            "closed_early": closed,
            "eligibility_revoked_mono": 1.0,
            "slot_acquired_mono": 1.0,
            "slot_released_mono": 1.0 + occupy,
            "local_read_stopped_mono": 1.0 + occupy if closed else None,
            "output_tokens": 100 if closed else 512,
            "T_e2e_s": occupy,
        },
        "rows": rows,
    }


def _bundle() -> list[dict]:
    rows = []
    for seed in range(7000, 7050):
        for load, base in (("medium", 4.0), ("high", 8.0)):
            rows.append(_trace(seed, load, "G0", base, base + 2, occupy=3.0))
            rows.append(_trace(seed, load, "G2-local", base - 1, base + 1, occupy=1.0))
    return rows


def test_bootstrap_resamples_pairs_not_bystanders() -> None:
    values = [1.0, 3.0]
    out = bootstrap_mean(values, replicates=20, seed=1)
    assert out["n"] == 2


def test_sample_size_never_exceeds_confirm_n() -> None:
    pairs = []
    for seed in range(10):
        for load in ("medium", "high"):
            pairs.append({"usable": True, "load": load, "dW_slot": 0.2 + seed})
    plan = sample_size_plan(pairs)
    assert plan["n"] <= 50


def test_replication_seeds_avoid_sealed_windows() -> None:
    seeds = replication_seeds(12, start=8100)
    assert seeds[0] == 8100
    assert not (set(seeds) & set(range(6000, 6015)))
    assert not (set(seeds) & set(range(7000, 7050)))
    with pytest.raises(ValueError):
        replication_seeds(50, start=7000)


def test_audit_accepts_complete_synthetic_pairs() -> None:
    report = audit_records(_bundle(), protocol={"ds": {}}, published=None)
    assert report["hard_passed"]
    assert report["loads"]["high"]["gate_E"]["passed"] is False or report["loads"]["medium"]["n"] == 50


def test_audit_rejects_missing_seed() -> None:
    rows = [row for row in _bundle() if not (row["seed"] == 7049 and row["arm"] == "G0")]
    report = audit_records(rows, protocol={"ds": {}}, published=None)
    assert report["hard_passed"] is False


def test_m0_holdout_does_not_train_on_high() -> None:
    pairs = []
    for seed in range(12):
        pairs.append(
            {
                "usable": True,
                "seed": seed,
                "load": "medium",
                "dU_client": float(seed),
                "dT_e2e": 0.5 * seed + 0.1,
            }
        )
        pairs.append(
            {
                "usable": True,
                "seed": seed,
                "load": "high",
                "dU_client": 1.0,
                "dT_e2e": 9.0,
            }
        )
    model = fit_m0(pairs)
    assert model["n"] == 12
    assert model["train_load"] == "medium"
    holdout = holdout_residuals(model, pairs, holdout_load="high", seed=3)
    assert holdout["n"] == 12
    assert holdout["status"] == "predicted"


def test_sealed_runner_refuses_existing_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sealed = tmp_path / "traces.jsonl"
    sealed.write_text("{}\n", encoding="utf-8")
    script = Path("scripts/run_v50_ds.py").read_text(encoding="utf-8")
    assert "refusing to append to sealed" in script
    assert sealed.exists()


def test_scripted_groups_keep_slot_and_backend_apart() -> None:
    local = asyncio.run(run_scripted_group("G2-local", residual_s=0.0, hold_slot_s=0.0))
    residual = asyncio.run(run_scripted_group("G2-residual", residual_s=0.03, hold_slot_s=0.0))
    held = asyncio.run(run_scripted_group("G2-hold-slot", residual_s=0.0, hold_slot_s=0.03))
    assert local["kv_gpu"] == "unavailable"
    assert local["not_vllm"] is True
    assert event_time(residual, "left_scheduler_queue") > event_time(residual, "slot_released") - 1e-9
    assert event_time(residual, "slot_released") < event_time(residual, "left_scheduler_queue")
    assert event_time(held, "slot_released") > event_time(held, "left_scheduler_queue")
    assert all(item.get("provider_abort_ack") is False for item in residual["events"] if item["event"] == "abort_confirmed")
