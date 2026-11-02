"""阶段 F（v1.2）分阶段执行脚本。

支持按阶段独立执行（绝不一次性混跑未冻结阶段）：
- ``--phase f1-dev``      ：运行 F1-M（7 格失效机制）与 F1-R（3 格可恢复）开发种子，输出每格方差与触发率校准，并生成冻结协议 ``f1_protocol_frozen.json``；
- ``--phase f1-confirm``  ：按冻结协议运行 F1-M 与 F1-R 确认性测试种子；
- ``--phase f2-dev``      ：运行 F2（refresh_only 观察成本隔离）开发种子，校准实际观察延迟分布并生成 ``f2_protocol_frozen.json``；
- ``--phase f2-confirm``  ：运行 F2 确认性测试种子（F2-0 / F2-low / F2-high）；
- ``--phase f4-dev``      ：运行 F4（FIFO 串行队列公平性合同）开发种子并生成 ``f4_protocol_frozen.json``；
- ``--phase f4-confirm``  ：运行 F4 确认性测试种子（F4-a 平稳混合 / F4-b 突发队头阻塞 / F4-c 长短连续混排）；
- ``--phase f3-confirm``  ：运行 F3 非流式真实 API 能力边界对照。

原始数据只追加不覆盖，不删除任何失败或未触发行。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from epochgate.cli import http_settings  # noqa: E402
from epochgate.stage_e_runner import arm_order_for_seed  # noqa: E402
from epochgate.stage_f_runner import (  # noqa: E402
    STARVATION_OVERTAKEN_LIMIT,
    STARVATION_WAIT_THRESHOLD_S,
    build_provenance_meta,
    classify_pair_trigger_status,
    run_f3_nonstreaming_boundary_probe,
    run_stage_f_arm,
)

OUT_F = ROOT / "results" / "real" / "phase_f"
DEV_DIR = OUT_F / "dev"
CONF_DIR = OUT_F / "confirmatory"
PROTO_DIR = OUT_F / "protocols"

# ============================================================================
# 1. F1 实验单元定义（严格区分 F1-M 7 格失效机制 vs F1-R 3 格可恢复集成）
# ============================================================================
F1_M_CELLS: tuple[dict[str, Any], ...] = (
    {"cell_id": "F1M_short_early", "length_tier": "short", "timing_tier": "early", "words": 50, "t_change_s": 0.25},
    {"cell_id": "F1M_short_mid", "length_tier": "short", "timing_tier": "mid", "words": 50, "t_change_s": 0.80},
    {"cell_id": "F1M_short_late", "length_tier": "short", "timing_tier": "late", "words": 50, "t_change_s": 1.80},
    {"cell_id": "F1M_mid_mid", "length_tier": "mid", "timing_tier": "mid", "words": 200, "t_change_s": 0.80},
    {"cell_id": "F1M_long_early", "length_tier": "long", "timing_tier": "early", "words": 450, "t_change_s": 0.25},
    {"cell_id": "F1M_long_mid", "length_tier": "long", "timing_tier": "mid", "words": 450, "t_change_s": 0.80},
    {"cell_id": "F1M_long_late", "length_tier": "long", "timing_tier": "late", "words": 450, "t_change_s": 1.80},
)

F1_R_CELLS: tuple[dict[str, Any], ...] = (
    {"cell_id": "F1R_long_early", "length_tier": "long", "timing_tier": "early", "words": 450, "t_change_s": 0.25, "t_restore_s": 1.25},
    {"cell_id": "F1R_mid_mid", "length_tier": "mid", "timing_tier": "mid", "words": 200, "t_change_s": 0.80, "t_restore_s": 1.80},
    {"cell_id": "F1R_short_early", "length_tier": "short", "timing_tier": "early", "words": 50, "t_change_s": 0.25, "t_restore_s": 1.25},
)

# 开发种子（510x–570x / 580x）与确认性测试种子（515x–575x / 585x）严格分离
F1_M_DEV_SEEDS: dict[str, tuple[int, ...]] = {
    "F1M_short_early": (5101,),
    "F1M_short_mid": (5201,),
    "F1M_short_late": (5301,),
    "F1M_mid_mid": (5401,),
    "F1M_long_early": (5501,),
    "F1M_long_mid": (5601,),
    "F1M_long_late": (5701,),
}

F1_M_CONFIRM_SEEDS: dict[str, tuple[int, ...]] = {
    "F1M_short_early": (5151, 5152),
    "F1M_short_mid": (5251, 5252),
    "F1M_short_late": (5351, 5352),
    "F1M_mid_mid": (5451, 5452),
    "F1M_long_early": (5551, 5552),
    "F1M_long_mid": (5651, 5652),
    "F1M_long_late": (5751, 5752),
}

F1_R_DEV_SEEDS: dict[str, tuple[int, ...]] = {
    "F1R_long_early": (5801,),
    "F1R_mid_mid": (5811,),
    "F1R_short_early": (5821,),
}

F1_R_CONFIRM_SEEDS: dict[str, tuple[int, ...]] = {
    "F1R_long_early": (5851, 5852, 5853, 5854),
    "F1R_mid_mid": (5861, 5862, 5863, 5864),
    "F1R_short_early": (5871, 5872),
}

# ============================================================================
# 2. F2 观察成本梯度定义（默认 refresh_only 作用域隔离）
# ============================================================================
F2_CONDITIONS: tuple[dict[str, Any], ...] = (
    {"cond_id": "long_early", "length_tier": "long", "timing_tier": "early", "words": 450, "t_change_s": 0.25, "t_restore_s": 1.25},
    {"cond_id": "mid_mid", "length_tier": "mid", "timing_tier": "mid", "words": 200, "t_change_s": 0.80, "t_restore_s": 1.80},
    {"cond_id": "short_early", "length_tier": "short", "timing_tier": "early", "words": 50, "t_change_s": 0.25, "t_restore_s": 1.25},
)

F2_DELAY_LEVELS: tuple[tuple[str, float], ...] = (
    ("F2_0_base", 0.0),
    ("F2_low_0.5s", 0.50),
    ("F2_high_2.0s", 2.00),
)

F2_DEV_SEEDS: dict[str, int] = {
    "long_early:F2_0_base": 6101,
    "long_early:F2_low_0.5s": 6102,
    "long_early:F2_high_2.0s": 6103,
    "mid_mid:F2_0_base": 6201,
    "mid_mid:F2_low_0.5s": 6202,
    "mid_mid:F2_high_2.0s": 6203,
    "short_early:F2_0_base": 6301,
    "short_early:F2_low_0.5s": 6302,
    "short_early:F2_high_2.0s": 6303,
}

F2_CONFIRM_SEEDS: dict[str, tuple[int, ...]] = {
    "long_early:F2_0_base": (6151, 6152),
    "long_early:F2_low_0.5s": (6161, 6162),
    "long_early:F2_high_2.0s": (6171, 6172),
    "mid_mid:F2_0_base": (6251, 6252),
    "mid_mid:F2_low_0.5s": (6261, 6262),
    "mid_mid:F2_high_2.0s": (6271, 6272),
    "short_early:F2_0_base": (6351,),
    "short_early:F2_low_0.5s": (6361,),
    "short_early:F2_high_2.0s": (6371,),
}

# ============================================================================
# 3. F4 队列与公平性场景定义（严格 FIFO，replan_to_tail=True）
# ============================================================================
F4_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario_id": "F4a_steady_mixed",
        "description": "平稳到达长短混合 (t0=long@0.0s, t1=short@0.4s, t2=mid@0.8s)",
        "t_change_s": 0.25,
        "t_restore_s": 1.00,
        "initial_stock": 3,
        "restore_delta": 3,
        "tasks": [
            {"task_id": "t0", "words": 400, "length_tier": "long", "arrival_offset_s": 0.0},
            {"task_id": "t1", "words": 60, "length_tier": "short", "arrival_offset_s": 0.40},
            {"task_id": "t2", "words": 180, "length_tier": "mid", "arrival_offset_s": 0.80},
        ],
    },
    {
        "scenario_id": "F4b_burst_hol",
        "description": "突发到达 1长+2短 队头阻塞测试 (t0=long@0.0s, t1=short@0.01s, t2=short@0.02s)",
        "t_change_s": 0.25,
        "t_restore_s": 1.00,
        "initial_stock": 3,
        "restore_delta": 3,
        "tasks": [
            {"task_id": "t0", "words": 450, "length_tier": "long", "arrival_offset_s": 0.0},
            {"task_id": "t1", "words": 60, "length_tier": "short", "arrival_offset_s": 0.01},
            {"task_id": "t2", "words": 60, "length_tier": "short", "arrival_offset_s": 0.02},
        ],
    },
    {
        "scenario_id": "F4c_interleaved_4tasks",
        "description": "长短连续混排 4 任务饥饿检查 (t0=long, t1=short, t2=short, t3=long)",
        "t_change_s": 0.25,
        "t_restore_s": 1.00,
        "initial_stock": 4,
        "restore_delta": 4,
        "tasks": [
            {"task_id": "t0", "words": 400, "length_tier": "long", "arrival_offset_s": 0.0},
            {"task_id": "t1", "words": 60, "length_tier": "short", "arrival_offset_s": 0.01},
            {"task_id": "t2", "words": 60, "length_tier": "short", "arrival_offset_s": 0.02},
            {"task_id": "t3", "words": 350, "length_tier": "long", "arrival_offset_s": 0.03},
        ],
    },
)

F4_DEV_SEEDS: dict[str, tuple[int, ...]] = {
    "F4a_steady_mixed": (7101,),
    "F4b_burst_hol": (7201,),
    "F4c_interleaved_4tasks": (7301,),
}

F4_CONFIRM_SEEDS: dict[str, tuple[int, ...]] = {
    "F4a_steady_mixed": (7151, 7152),
    "F4b_burst_hol": (7251, 7252),
    "F4c_interleaved_4tasks": (7351, 7352),
}


def append_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


async def run_f1_split(
    *,
    meta,
    api_key: str,
    seed_split: str,
    m_seeds: dict[str, tuple[int, ...]],
    r_seeds: dict[str, tuple[int, ...]],
    m_out: Path,
    r_out: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    m_existing = load_jsonl(m_out)
    m_done = {(r["pair_id"], r["arm"], r["task_id"]) for r in m_existing}
    m_all = list(m_existing)

    for cell in F1_M_CELLS:
        cid = cell["cell_id"]
        for seed in m_seeds[cid]:
            pair_id = f"{cid}-{seed_split}-seed{seed}"
            arms = arm_order_for_seed(seed)
            order_str = "".join(arms)
            for arm in arms:
                if (pair_id, arm, "t0") in m_done:
                    print(f"[skip] {pair_id} arm={arm}")
                    continue
                rows = await run_stage_f_arm(
                    meta=meta,
                    api_key=api_key,
                    seed_split=seed_split,
                    experiment_block="F1_M_invalidation_mechanism",
                    cell_id=cid,
                    pair_id=pair_id,
                    batch_id=pair_id,
                    seed=seed,
                    arm=arm,
                    arm_order=order_str,
                    task_kind="invalidate",
                    length_tier=cell["length_tier"],
                    timing_tier=cell["timing_tier"],
                    target_words=cell["words"],
                    t_change_s=cell["t_change_s"],
                    t_restore_s=None,
                    initial_stock=1,
                )
                append_rows(m_out, rows)
                m_all.extend(rows)
                r0 = rows[0]
                print(
                    f"[F1-M {seed_split}] {cid} seed={seed} arm={arm} outcome={r0['trigger_outcome']} "
                    f"pre={r0['pre_revocation_observed_text_tokens']} post={r0['post_revocation_observed_text_tokens']} "
                    f"slot={r0['old_attempt_slot_hold_s'] and round(r0['old_attempt_slot_hold_s'], 2)}s"
                )

    r_existing = load_jsonl(r_out)
    r_done = {(r["pair_id"], r["arm"], r["task_id"]) for r in r_existing}
    r_all = list(r_existing)

    for cell in F1_R_CELLS:
        cid = cell["cell_id"]
        for seed in r_seeds[cid]:
            pair_id = f"{cid}-{seed_split}-seed{seed}"
            arms = arm_order_for_seed(seed)
            order_str = "".join(arms)
            for arm in arms:
                if (pair_id, arm, "t0") in r_done:
                    print(f"[skip] {pair_id} arm={arm}")
                    continue
                rows = await run_stage_f_arm(
                    meta=meta,
                    api_key=api_key,
                    seed_split=seed_split,
                    experiment_block="F1_R_recoverable_integration",
                    cell_id=cid,
                    pair_id=pair_id,
                    batch_id=pair_id,
                    seed=seed,
                    arm=arm,
                    arm_order=order_str,
                    task_kind="recoverable",
                    length_tier=cell["length_tier"],
                    timing_tier=cell["timing_tier"],
                    target_words=cell["words"],
                    t_change_s=cell["t_change_s"],
                    t_restore_s=cell["t_restore_s"],
                    initial_stock=1,
                    restore_delta=1,
                )
                append_rows(r_out, rows)
                r_all.extend(rows)
                r0 = rows[0]
                print(
                    f"[F1-R {seed_split}] {cid} seed={seed} arm={arm} outcome={r0['trigger_outcome']} "
                    f"commits={r0['action_commits']} rec_ok={r0['successful_recovery']} "
                    f"rec_lat={r0['recovery_latency_s'] and round(r0['recovery_latency_s'], 2)}s"
                )
    return m_all, r_all


def freeze_f1_protocol(meta, m_dev: list[dict[str, Any]], r_dev: list[dict[str, Any]]) -> Path:
    PROTO_DIR.mkdir(parents=True, exist_ok=True)
    by_cell: dict[str, list[dict[str, Any]]] = {}
    for r in m_dev:
        by_cell.setdefault(r["cell_id"], []).append(r)

    cell_calibration: dict[str, Any] = {}
    for cid, items in sorted(by_cell.items()):
        trig = [x for x in items if x["trigger_outcome"] == "triggered"]
        cell_calibration[cid] = {
            "dev_requests": len(items),
            "dev_triggered_requests": len(trig),
            "dev_trigger_prob": round(len(trig) / len(items), 3) if items else 0.0,
        }

    proto = {
        "protocol_id": "F1_frozen_protocol_v1.2",
        "provenance": meta.__dict__,
        "structure": {
            "F1_M_cells_count": len(F1_M_CELLS),
            "F1_M_cells": list(F1_M_CELLS),
            "F1_R_cells_count": len(F1_R_CELLS),
            "F1_R_cells": list(F1_R_CELLS),
        },
        "multiple_comparison_plan": {
            "primary_confirmatory_cells": ["F1M_long_early", "F1M_long_mid", "F1M_mid_mid"],
            "correction_method": "Holm-Bonferroni across primary confirmatory cells",
            "exploratory_boundary_cells": ["F1M_short_early", "F1M_short_mid", "F1M_short_late", "F1M_long_late"],
        },
        "dev_calibration": cell_calibration,
        "confirmatory_seeds_locked": {
            "F1_M": F1_M_CONFIRM_SEEDS,
            "F1_R": F1_R_CONFIRM_SEEDS,
        },
    }
    out = PROTO_DIR / "f1_protocol_frozen.json"
    out.write_text(json.dumps(proto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return out


async def run_f2_split(
    *,
    meta,
    api_key: str,
    seed_split: str,
    seeds_map: dict[str, Any],
    out_path: Path,
) -> list[dict[str, Any]]:
    existing = load_jsonl(out_path)
    done = {(r["pair_id"], r["arm"], r["task_id"]) for r in existing}
    all_rows = list(existing)

    for cond in F2_CONDITIONS:
        cid = cond["cond_id"]
        for d_label, d_val in F2_DELAY_LEVELS:
            key = f"{cid}:{d_label}"
            raw_seeds = seeds_map[key]
            seed_tuple = (raw_seeds,) if isinstance(raw_seeds, int) else tuple(raw_seeds)
            for seed in seed_tuple:
                cell_id = f"F2_{cid}_{d_label}"
                pair_id = f"F2-{cid}-{d_label}-{seed_split}-seed{seed}"
                arms = arm_order_for_seed(seed)
                order_str = "".join(arms)
                for arm in arms:
                    if (pair_id, arm, "t0") in done:
                        print(f"[skip] {pair_id} arm={arm}")
                        continue
                    rows = await run_stage_f_arm(
                        meta=meta,
                        api_key=api_key,
                        seed_split=seed_split,
                        experiment_block="F2_observation_cost_refresh_only",
                        cell_id=cell_id,
                        pair_id=pair_id,
                        batch_id=pair_id,
                        seed=seed,
                        arm=arm,
                        arm_order=order_str,
                        task_kind="recoverable",
                        length_tier=cond["length_tier"],
                        timing_tier=cond["timing_tier"],
                        target_words=cond["words"],
                        t_change_s=cond["t_change_s"],
                        t_restore_s=cond["t_restore_s"],
                        initial_stock=1,
                        restore_delta=1,
                        observe_delay_s=d_val,
                        observe_delay_scope="refresh_only",
                    )
                    append_rows(out_path, rows)
                    all_rows.extend(rows)
                    r0 = rows[0]
                    print(
                        f"[F2 {seed_split}] {cid} {d_label} seed={seed} arm={arm} "
                        f"init_obs={r0['actual_initial_observe_latency_s'] and round(r0['actual_initial_observe_latency_s'], 4)}s "
                        f"ref_obs={r0['actual_refresh_observe_latency_s'] and round(r0['actual_refresh_observe_latency_s'], 3)}s "
                        f"wall={round(r0['batch_wall_clock_s'], 2)}s commits={r0['action_commits']}"
                    )
    return all_rows


def freeze_f2_protocol(meta, f2_dev: list[dict[str, Any]]) -> Path:
    PROTO_DIR.mkdir(parents=True, exist_ok=True)
    init_lats = [r["actual_initial_observe_latency_s"] for r in f2_dev if r["actual_initial_observe_latency_s"] is not None]
    proto = {
        "protocol_id": "F2_frozen_protocol_v1.2",
        "provenance": meta.__dict__,
        "observe_delay_scope": "refresh_only",
        "baseline_initial_observe_latency_s_median": round(statistics.median(init_lats), 6) if init_lats else 0.0,
        "delay_levels": list(F2_DELAY_LEVELS),
        "primary_net_benefit_metrics": ["delta_batch_wall_clock_s", "delta_recovery_latency_s"],
        "mechanism_explanation_formula": "G = P(trigger)*E[delta_slot | trigger] - C_extra (explanatory only, does not replace batch latency)",
        "confirmatory_seeds_locked": F2_CONFIRM_SEEDS,
    }
    out = PROTO_DIR / "f2_protocol_frozen.json"
    out.write_text(json.dumps(proto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return out


async def run_f4_split(
    *,
    meta,
    api_key: str,
    seed_split: str,
    seeds_map: dict[str, tuple[int, ...]],
    out_path: Path,
) -> list[dict[str, Any]]:
    existing = load_jsonl(out_path)
    done = {(r["pair_id"], r["arm"], r["task_id"]) for r in existing}
    all_rows = list(existing)

    for scen in F4_SCENARIOS:
        sid = scen["scenario_id"]
        for seed in seeds_map[sid]:
            batch_id = f"{sid}-{seed_split}-seed{seed}"
            arms = arm_order_for_seed(seed)
            order_str = "".join(arms)
            for arm in arms:
                if (batch_id, arm, "t0") in done:
                    print(f"[skip] {batch_id} arm={arm}")
                    continue
                rows = await run_stage_f_arm(
                    meta=meta,
                    api_key=api_key,
                    seed_split=seed_split,
                    experiment_block="F4_fifo_serial_queue",
                    cell_id=sid,
                    pair_id=batch_id,
                    batch_id=batch_id,
                    seed=seed,
                    arm=arm,
                    arm_order=order_str,
                    task_kind="recoverable",
                    length_tier="mixed",
                    timing_tier="early",
                    target_words=400,
                    t_change_s=scen["t_change_s"],
                    t_restore_s=scen["t_restore_s"],
                    initial_stock=scen["initial_stock"],
                    restore_delta=scen["restore_delta"],
                    observe_delay_s=0.0,
                    observe_delay_scope="refresh_only",
                    task_plan=scen["tasks"],
                    use_fifo_scheduler=True,
                )
                append_rows(out_path, rows)
                all_rows.extend(rows)
                r0 = rows[0]
                waits = [round(x["first_queue_wait_s"], 2) for x in rows]
                commits = sum(x["action_commits"] for x in rows)
                starved = sum(1 for x in rows if x["starvation_detected"])
                print(
                    f"[F4 {seed_split}] {sid} seed={seed} arm={arm} waits={waits} "
                    f"wall={round(r0['batch_wall_clock_s'], 2)}s commits={commits}/{len(rows)} starved={starved}"
                )
    return all_rows


def freeze_f4_protocol(meta, f4_dev: list[dict[str, Any]]) -> Path:
    PROTO_DIR.mkdir(parents=True, exist_ok=True)
    proto = {
        "protocol_id": "F4_frozen_protocol_v1.2",
        "provenance": meta.__dict__,
        "queue_contract": {
            "scheduler": "FifoAdmissionScheduler",
            "max_inflight": 1,
            "initial_enqueue_rule": "Strictly ordered by (task.arrival_mono, attempt_id)",
            "replan_enqueue_rule": "Cancelled/rejected attempt replan enters tail of ready queue via attempt.created_mono (identical priority for Arm A and Arm B)",
            "slot_acquisition_rule": "Single serial local slot acquired only after previous attempt emits slot_released",
            "starvation_wait_threshold_s": STARVATION_WAIT_THRESHOLD_S,
            "starvation_overtaken_limit": STARVATION_OVERTAKEN_LIMIT,
        },
        "scenarios": list(F4_SCENARIOS),
        "confirmatory_seeds_locked": F4_CONFIRM_SEEDS,
    }
    out = PROTO_DIR / "f4_protocol_frozen.json"
    out.write_text(json.dumps(proto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return out


async def main_async(phase: str) -> None:
    settings = http_settings()
    if settings is None:
        raise SystemExit("缺少 EPOCHGATE_BASE_URL、EPOCHGATE_API_KEY 或 EPOCHGATE_MODEL")
    base_url, api_key, model = settings
    meta = build_provenance_meta(model=model, base_url=base_url)

    if phase == "f1-dev":
        m_dev, r_dev = await run_f1_split(
            meta=meta,
            api_key=api_key,
            seed_split="dev",
            m_seeds=F1_M_DEV_SEEDS,
            r_seeds=F1_R_DEV_SEEDS,
            m_out=DEV_DIR / "f1_m_dev.jsonl",
            r_out=DEV_DIR / "f1_r_dev.jsonl",
        )
        freeze_f1_protocol(meta, m_dev, r_dev)
    elif phase == "f1-confirm":
        if not (PROTO_DIR / "f1_protocol_frozen.json").is_file():
            raise SystemExit("必须先运行 --phase f1-dev 并冻结 f1_protocol_frozen.json")
        await run_f1_split(
            meta=meta,
            api_key=api_key,
            seed_split="confirmatory",
            m_seeds=F1_M_CONFIRM_SEEDS,
            r_seeds=F1_R_CONFIRM_SEEDS,
            m_out=CONF_DIR / "f1_m_confirmatory.jsonl",
            r_out=CONF_DIR / "f1_r_confirmatory.jsonl",
        )
    elif phase == "f2-dev":
        f2_dev = await run_f2_split(
            meta=meta,
            api_key=api_key,
            seed_split="dev",
            seeds_map=F2_DEV_SEEDS,
            out_path=DEV_DIR / "f2_dev.jsonl",
        )
        freeze_f2_protocol(meta, f2_dev)
    elif phase == "f2-confirm":
        if not (PROTO_DIR / "f2_protocol_frozen.json").is_file():
            raise SystemExit("必须先运行 --phase f2-dev 并冻结 f2_protocol_frozen.json")
        await run_f2_split(
            meta=meta,
            api_key=api_key,
            seed_split="confirmatory",
            seeds_map=F2_CONFIRM_SEEDS,
            out_path=CONF_DIR / "f2_confirmatory.jsonl",
        )
    elif phase == "f4-dev":
        f4_dev = await run_f4_split(
            meta=meta,
            api_key=api_key,
            seed_split="dev",
            seeds_map=F4_DEV_SEEDS,
            out_path=DEV_DIR / "f4_dev.jsonl",
        )
        freeze_f4_protocol(meta, f4_dev)
    elif phase == "f4-confirm":
        if not (PROTO_DIR / "f4_protocol_frozen.json").is_file():
            raise SystemExit("必须先运行 --phase f4-dev 并冻结 f4_protocol_frozen.json")
        await run_f4_split(
            meta=meta,
            api_key=api_key,
            seed_split="confirmatory",
            seeds_map=F4_CONFIRM_SEEDS,
            out_path=CONF_DIR / "f4_confirmatory.jsonl",
        )
    elif phase == "f3-confirm":
        out = CONF_DIR / "f3_nonstreaming_boundary.jsonl"
        existing = load_jsonl(out)
        done_seeds = {int(r["seed"]) for r in existing}
        for seed in (8101, 8102):
            if seed in done_seeds:
                print(f"[skip] F3 nonstream seed={seed}")
                continue
            row = await run_f3_nonstreaming_boundary_probe(meta=meta, api_key=api_key, seed=seed)
            append_rows(out, [row])
            print(
                f"[F3 nonstream] seed={seed} commits={row['action_commits']} "
                f"rejects={row['action_rejects']} gate_held={row['gateway_eligibility_gate_held']}"
            )
    else:
        raise ValueError(f"unknown phase: {phase}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        required=True,
        choices=(
            "f1-dev",
            "f1-confirm",
            "f2-dev",
            "f2-confirm",
            "f4-dev",
            "f4-confirm",
            "f3-confirm",
        ),
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.phase))


if __name__ == "__main__":
    main()
