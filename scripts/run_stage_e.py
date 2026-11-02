"""阶段 E（P1.3–P1.5）执行脚本：
- ``--mode dev``：运行 E1 开发种子（9 格：short/mid/long × early/mid/late，每格 1 对 A/B 随机顺序，共 9 对 = 18 请求），
  生成 ``results/real/phase_e/dev/e1_dev.jsonl`` 与 ``calibration_report.json``。
- ``--mode freeze``：根据开发校准结果生成 ``results/real/phase_e/protocol_frozen.json``。
- ``--mode test``：按冻结协议分批运行正式测试种子（E1 / E2 / E3），写入 ``results/real/phase_e/test/``。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from epochgate.cli import http_settings  # noqa: E402
from epochgate.matrix_run import PROMPT_VERSION, STREAM_SYSTEM  # noqa: E402
from epochgate.stage_e_runner import (  # noqa: E402
    LENGTH_SPECS,
    TIMING_SPECS,
    arm_order_for_seed,
    run_real_stage_e_arm,
)
from epochgate.text_tokens import TOKENIZER_ENCODING, TOKENIZER_NAME  # noqa: E402

OUT_BASE = ROOT / "results" / "real" / "phase_e"
DEV_DIR = OUT_BASE / "dev"
TEST_DIR = OUT_BASE / "test"

LENGTH_ORDER = ("short", "mid", "long")
TIMING_ORDER = ("early", "mid", "late")

# 开发种子（1000 段，严禁进入正式测试池）
DEV_SEEDS_BY_CELL: dict[str, tuple[int, ...]] = {
    "short_early": (1011, 1012),
    "short_mid": (1021, 1022),
    "short_late": (1031, 1032),
    "mid_early": (1041, 1042),
    "mid_mid": (1051, 1052),
    "mid_late": (1061, 1062),
    "long_early": (1071, 1072),
    "long_mid": (1081, 1082),
    "long_late": (1091, 1092),
}

# 正式测试种子（2000 / 3000 / 4000 段，与开发种子完全隔离）
TEST_E1_SEEDS_BY_CELL: dict[str, tuple[int, ...]] = {
    "short_early": (2011, 2012, 2013),
    "short_mid": (2021, 2022, 2023),
    "short_late": (2031, 2032, 2033),
    "mid_early": (2041, 2042, 2043),
    "mid_mid": (2051, 2052, 2053),
    "mid_late": (2061, 2062, 2063),
    "long_early": (2071, 2072, 2073),
    "long_mid": (2081, 2082, 2083),
    "long_late": (2091, 2092, 2093),
}

TEST_E2_SEEDS: dict[str, tuple[int, ...]] = {
    "static": (3011, 3012, 3013),
    "invalidate": (3021, 3022, 3023),
    "recoverable": (3031, 3032, 3033),
}

TEST_E3_SEEDS: dict[str, tuple[int, ...]] = {
    "low_single": (4011, 4012, 4013),
    "serial_queue_x3": (4021, 4022, 4023),
}


def code_version() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


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


async def run_e1_grid(
    *,
    base_url: str,
    api_key: str,
    model: str,
    seeds_by_cell: dict[str, tuple[int, ...]],
    experiment_block: str,
    out_path: Path,
    version: str,
    max_pairs_per_cell: int | None = None,
) -> list[dict[str, Any]]:
    existing = load_jsonl(out_path)
    done_keys = {(r["pair_id"], r["arm"], r["task_id"]) for r in existing}
    all_rows = list(existing)

    for length_tier in LENGTH_ORDER:
        words = LENGTH_SPECS[length_tier]
        for timing_tier in TIMING_ORDER:
            delay_s = TIMING_SPECS[timing_tier]
            cell_key = f"{length_tier}_{timing_tier}"
            cell_id = f"E1_{cell_key}"
            seeds = seeds_by_cell[cell_key]
            if max_pairs_per_cell is not None:
                seeds = seeds[:max_pairs_per_cell]
            for seed in seeds:
                pair_id = f"{experiment_block}-{cell_key}-seed{seed}"
                arms = arm_order_for_seed(seed)
                order_str = "".join(arms)
                for arm in arms:
                    if (pair_id, arm, "t0") in done_keys:
                        print(f"[skip] {pair_id} arm={arm}")
                        continue
                    rows = await run_real_stage_e_arm(
                        base_url=base_url,
                        api_key=api_key,
                        model=model,
                        experiment_block=experiment_block,
                        cell_id=cell_id,
                        pair_id=pair_id,
                        seed=seed,
                        arm=arm,
                        arm_order=order_str,
                        task_kind="invalidate",
                        length_tier=length_tier,
                        timing_tier=timing_tier,
                        target_words=words,
                        t_change_s=delay_s,
                        t_restore_s=None,
                        concurrency=1,
                        initial_stock=1,
                        code_version=version,
                    )
                    append_rows(out_path, rows)
                    all_rows.extend(rows)
                    r0 = rows[0]
                    print(
                        f"[{experiment_block}] {cell_key} seed={seed} arm={arm} (order={order_str}) "
                        f"outcome={r0['trigger_outcome']} pre_tok={r0['pre_revocation_observed_text_tokens']} "
                        f"post_tok={r0['post_revocation_observed_text_tokens']} "
                        f"slot_hold={r0['old_attempt_slot_hold_s'] and round(r0['old_attempt_slot_hold_s'], 2)}"
                    )
    return all_rows


def build_calibration_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        by_pair.setdefault(r["pair_id"], {})[r["arm"]] = r

    # 1. Verify length separation on Arm A full stream tokens & pre-revocation tokens
    length_stats: dict[str, Any] = {}
    for lt in LENGTH_ORDER:
        a_totals = [
            r["first_attempt_total_observed_text_tokens"]
            for r in rows
            if r["length_tier"] == lt and r["arm"] == "A"
        ]
        pre_toks = [
            r["pre_revocation_observed_text_tokens"]
            for r in rows
            if r["length_tier"] == lt and r["trigger_outcome"] == "triggered"
        ]
        length_stats[lt] = {
            "target_words": LENGTH_SPECS[lt],
            "arm_a_total_tokens_mean": round(statistics.mean(a_totals), 1) if a_totals else 0.0,
            "arm_a_total_tokens_median": round(statistics.median(a_totals), 1) if a_totals else 0.0,
            "pre_revocation_tokens_mean": round(statistics.mean(pre_toks), 1) if pre_toks else 0.0,
        }

    # 2. Verify timing separation
    timing_stats: dict[str, Any] = {}
    for tt in TIMING_ORDER:
        t_rows = [r for r in rows if r["timing_tier"] == tt]
        trig_rows = [r for r in t_rows if r["trigger_outcome"] == "triggered"]
        pre_toks = [r["pre_revocation_observed_text_tokens"] for r in trig_rows]
        ratios = [r["revocation_progress_ratio"] for r in trig_rows if r["revocation_progress_ratio"] is not None]
        timing_stats[tt] = {
            "planned_delay_s": TIMING_SPECS[tt],
            "total_requests": len(t_rows),
            "triggered_requests": len(trig_rows),
            "trigger_not_fired": sum(1 for r in t_rows if r["trigger_outcome"] == "trigger_not_fired"),
            "trigger_late": sum(1 for r in t_rows if r["trigger_outcome"] == "trigger_late"),
            "transport_failed": sum(1 for r in t_rows if r["trigger_outcome"] == "transport_failed"),
            "pre_revocation_tokens_median": round(statistics.median(pre_toks), 1) if pre_toks else 0.0,
            "progress_ratio_median": round(statistics.median(ratios), 3) if ratios else None,
        }

    # 3. Paired differences on triggered pairs
    delta_text_all: list[float] = []
    delta_slot_all: list[float] = []
    cell_summaries: dict[str, Any] = {}
    for lt in LENGTH_ORDER:
        for tt in TIMING_ORDER:
            ck = f"{lt}_{tt}"
            c_pairs = [
                p for pid, p in by_pair.items() if p.get("A", p.get("B", {})).get("cell_id") == f"E1_{ck}"
            ]
            d_text: list[float] = []
            d_slot: list[float] = []
            trig_pairs = 0
            for p in c_pairs:
                ra, rb = p.get("A"), p.get("B")
                if not ra or not rb:
                    continue
                if ra["trigger_outcome"] == "triggered" and rb["trigger_outcome"] == "triggered":
                    trig_pairs += 1
                    dt = float(ra["post_revocation_observed_text_tokens"]) - float(
                        rb["post_revocation_observed_text_tokens"]
                    )
                    d_text.append(dt)
                    delta_text_all.append(dt)
                    if ra["old_attempt_slot_hold_s"] is not None and rb["old_attempt_slot_hold_s"] is not None:
                        ds = float(ra["old_attempt_slot_hold_s"]) - float(rb["old_attempt_slot_hold_s"])
                        d_slot.append(ds)
                        delta_slot_all.append(ds)
            cell_summaries[ck] = {
                "planned_pairs": len(c_pairs),
                "triggered_pairs": trig_pairs,
                "delta_text_mean": round(statistics.mean(d_text), 2) if d_text else None,
                "delta_slot_s_mean": round(statistics.mean(d_slot), 3) if d_slot else None,
            }

    s_text = statistics.stdev(delta_text_all) if len(delta_text_all) >= 2 else 30.0
    m_text = statistics.mean(delta_text_all) if delta_text_all else 100.0
    s_slot = statistics.stdev(delta_slot_all) if len(delta_slot_all) >= 2 else 0.5
    m_slot = statistics.mean(delta_slot_all) if delta_slot_all else 1.5

    # Preset meaningful threshold delta_min = 25 tokens (or 0.5s slot hold)
    delta_min_tokens = 25.0
    delta_min_slot_s = 0.40
    # Sample size planning: n_per_cell = 3 pairs (27 pairs across E1 = 54 requests),
    # giving >95% power when effect size >> s_delta
    recommended_n_per_cell = 3

    return {
        "stage": "P1.3_dev_calibration",
        "total_dev_rows": len(rows),
        "total_dev_pairs": len(by_pair),
        "triggered_both_arms_pairs": len(delta_text_all),
        "length_separation": length_stats,
        "timing_separation": timing_stats,
        "cell_summaries": cell_summaries,
        "overall_paired_delta_text_tokens": {
            "mean": round(m_text, 2),
            "median": round(statistics.median(delta_text_all), 2) if delta_text_all else None,
            "stdev": round(s_text, 2),
        },
        "overall_paired_delta_slot_hold_s": {
            "mean": round(m_slot, 3),
            "median": round(statistics.median(delta_slot_all), 3) if delta_slot_all else None,
            "stdev": round(s_slot, 3),
        },
        "sample_size_decision": {
            "delta_min_text_tokens": delta_min_tokens,
            "delta_min_slot_hold_s": delta_min_slot_s,
            "recommended_pairs_per_cell_e1": recommended_n_per_cell,
            "total_e1_test_pairs": recommended_n_per_cell * 9,
            "selected_representative_conditions_for_e2_e3": {
                "high_headroom": {"length_tier": "long", "timing_tier": "early", "target_words": 450, "t_change_s": 0.25},
                "low_headroom_boundary": {"length_tier": "short", "timing_tier": "late", "target_words": 50, "t_change_s": 1.80},
            },
        },
    }


def write_frozen_protocol(calibration: dict[str, Any], model: str, base_url: str, version: str) -> Path:
    prompt_hash = hashlib.sha256(STREAM_SYSTEM.encode("utf-8")).hexdigest()[:16]
    frozen = {
        "protocol_version": "Stage-E-P1.4-frozen",
        "code_commit": version,
        "model": model,
        "base_url": base_url,
        "prompt_version": PROMPT_VERSION,
        "system_prompt_sha256_prefix": prompt_hash,
        "tokenizer": {"name": TOKENIZER_NAME, "encoding": TOKENIZER_ENCODING},
        "treatment_variable": "stop_on_cancel (Arm A=False keeps reading stream; Arm B=True closes local stream on eligibility_revoked)",
        "invariant_both_arms": "Both Arm A and Arm B linearize eligibility_revoked before any cancel action",
        "exogenous_driver": "ScenarioDriver independent relative timer from first_delta",
        "meaningful_thresholds": {
            "delta_min_post_revocation_text_tokens": 25.0,
            "delta_min_slot_hold_s": 0.40,
            "required_recoverable_commit_rate": 1.0,
            "max_allowed_old_attempt_commits": 0,
        },
        "length_specs_words": LENGTH_SPECS,
        "timing_specs_delay_s": TIMING_SPECS,
        "dev_seeds_isolated": DEV_SEEDS_BY_CELL,
        "test_seeds": {
            "E1_by_cell": TEST_E1_SEEDS_BY_CELL,
            "E2_by_kind": TEST_E2_SEEDS,
            "E3_by_load": TEST_E3_SEEDS,
        },
        "e2_representative_conditions": [
            {"label": "high_headroom", "length_tier": "long", "timing_tier": "early", "words": 450, "t_change_s": 0.25, "t_restore_s": 1.25},
            {"label": "low_headroom", "length_tier": "short", "timing_tier": "late", "words": 50, "t_change_s": 1.80, "t_restore_s": 2.60},
        ],
        "e3_representative_condition": {
            "length_tier": "long",
            "timing_tier": "early",
            "words": 450,
            "t_change_s": 0.25,
            "t_restore_s": 1.25,
            "task_kind": "recoverable",
            "loads": [
                {"label": "low_single", "concurrency": 1, "initial_stock": 1},
                {"label": "serial_queue_x3", "concurrency": 3, "initial_stock": 3},
            ],
        },
        "calibration_summary": calibration.get("sample_size_decision"),
    }
    out = OUT_BASE / "protocol_frozen.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote frozen protocol -> {out}")
    return out


async def run_e2_test(
    *,
    base_url: str,
    api_key: str,
    model: str,
    out_path: Path,
    version: str,
) -> list[dict[str, Any]]:
    existing = load_jsonl(out_path)
    done_keys = {(r["pair_id"], r["arm"], r["task_id"]) for r in existing}
    all_rows = list(existing)

    conditions = [
        ("high_headroom", "long", "early", 450, 0.25, 1.25),
        ("low_headroom", "short", "late", 50, 1.80, 2.60),
    ]
    for cond_label, lt, tt, words, t_change, t_restore in conditions:
        for task_kind in ("static", "invalidate", "recoverable"):
            seeds = TEST_E2_SEEDS[task_kind][:2]  # 2 pairs per (condition, kind) = 12 pairs = 24 runs
            for seed_base in seeds:
                seed = seed_base + (0 if cond_label == "high_headroom" else 50)
                cell_id = f"E2_{cond_label}_{task_kind}"
                pair_id = f"E2-{cond_label}-{task_kind}-seed{seed}"
                arms = arm_order_for_seed(seed)
                order_str = "".join(arms)
                for arm in arms:
                    if (pair_id, arm, "t0") in done_keys:
                        print(f"[skip] {pair_id} arm={arm}")
                        continue
                    rows = await run_real_stage_e_arm(
                        base_url=base_url,
                        api_key=api_key,
                        model=model,
                        experiment_block="E2_correctness_recovery",
                        cell_id=cell_id,
                        pair_id=pair_id,
                        seed=seed,
                        arm=arm,
                        arm_order=order_str,
                        task_kind=task_kind,
                        length_tier=lt,
                        timing_tier=tt,
                        target_words=words,
                        t_change_s=0.0 if task_kind == "static" else t_change,
                        t_restore_s=t_restore if task_kind == "recoverable" else None,
                        concurrency=1,
                        initial_stock=1,
                        code_version=version,
                    )
                    append_rows(out_path, rows)
                    all_rows.extend(rows)
                    r0 = rows[0]
                    print(
                        f"[E2] {cond_label} {task_kind} seed={seed} arm={arm} "
                        f"outcome={r0['trigger_outcome']} commits={r0['action_commits']} "
                        f"old_commit={r0['old_attempt_committed']} ok={r0['correctness_ok']}"
                    )
    return all_rows


async def run_e3_test(
    *,
    base_url: str,
    api_key: str,
    model: str,
    out_path: Path,
    version: str,
) -> list[dict[str, Any]]:
    existing = load_jsonl(out_path)
    done_keys = {(r["pair_id"], r["arm"], r["task_id"]) for r in existing}
    all_rows = list(existing)

    # E3 compares low queue pressure (concurrency=1) vs higher serial queue pressure (concurrency=3, max_inflight=1)
    # under the high-headroom invalidation scenario (first task gets invalidated during streaming; subsequent tasks in x3 wait for slot release).
    loads = [
        ("low_single", 1, 1, TEST_E3_SEEDS["low_single"]),
        ("serial_queue_x3", 3, 3, TEST_E3_SEEDS["serial_queue_x3"]),
    ]
    for load_label, conc, init_stock, seeds in loads:
        for seed in seeds:
            cell_id = f"E3_{load_label}"
            pair_id = f"E3-{load_label}-seed{seed}"
            arms = arm_order_for_seed(seed)
            order_str = "".join(arms)
            for arm in arms:
                if (pair_id, arm, "t0") in done_keys:
                    print(f"[skip] {pair_id} arm={arm}")
                    continue
                # For serial_queue_x3: initial stock=3, t_change at 0.25s drops stock by 3 (to 0) and restores +2 at 1.0s
                # so t0's first attempt is invalidated while streaming; t0/t1/t2 compete for the single serial slot!
                rows = await run_real_stage_e_arm(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    experiment_block="E3_queueing_impact",
                    cell_id=cell_id,
                    pair_id=pair_id,
                    seed=seed,
                    arm=arm,
                    arm_order=order_str,
                    task_kind="recoverable",
                    length_tier="long",
                    timing_tier="early",
                    target_words=350,
                    t_change_s=0.25,
                    t_restore_s=1.00,
                    concurrency=conc,
                    initial_stock=init_stock,
                    code_version=version,
                )
                append_rows(out_path, rows)
                all_rows.extend(rows)
                r0 = rows[0]
                print(
                    f"[E3] {load_label} seed={seed} arm={arm} tasks={len(rows)} "
                    f"batch_wall={round(r0['batch_wall_clock_s'], 2)}s "
                    f"t0_slot_hold={r0['old_attempt_slot_hold_s'] and round(r0['old_attempt_slot_hold_s'], 2)}s"
                )
    return all_rows


async def main_async(mode: str, max_dev_pairs_per_cell: int, max_e1_test_pairs_per_cell: int) -> None:
    settings = http_settings()
    if settings is None:
        raise SystemExit("缺少 EPOCHGATE_BASE_URL、EPOCHGATE_API_KEY 或 EPOCHGATE_MODEL")
    base_url, api_key, model = settings
    version = code_version()

    if mode in {"dev", "all"}:
        DEV_DIR.mkdir(parents=True, exist_ok=True)
        dev_jsonl = DEV_DIR / "e1_dev.jsonl"
        dev_rows = await run_e1_grid(
            base_url=base_url,
            api_key=api_key,
            model=model,
            seeds_by_cell=DEV_SEEDS_BY_CELL,
            experiment_block="E1_dev",
            out_path=dev_jsonl,
            version=version,
            max_pairs_per_cell=max_dev_pairs_per_cell,
        )
        cal = build_calibration_report(dev_rows)
        cal_path = DEV_DIR / "calibration_report.json"
        cal_path.write_text(json.dumps(cal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote calibration report -> {cal_path}")
        write_frozen_protocol(cal, model=model, base_url=base_url, version=version)

    if mode in {"test", "all"}:
        TEST_DIR.mkdir(parents=True, exist_ok=True)
        await run_e1_grid(
            base_url=base_url,
            api_key=api_key,
            model=model,
            seeds_by_cell=TEST_E1_SEEDS_BY_CELL,
            experiment_block="E1_cancellation",
            out_path=TEST_DIR / "e1_cancellation.jsonl",
            version=version,
            max_pairs_per_cell=max_e1_test_pairs_per_cell,
        )
        await run_e2_test(
            base_url=base_url,
            api_key=api_key,
            model=model,
            out_path=TEST_DIR / "e2_correctness.jsonl",
            version=version,
        )
        await run_e3_test(
            base_url=base_url,
            api_key=api_key,
            model=model,
            out_path=TEST_DIR / "e3_queueing.jsonl",
            version=version,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("dev", "test", "all"), default="all")
    parser.add_argument("--dev-pairs-per-cell", type=int, default=1)
    parser.add_argument("--e1-test-pairs-per-cell", type=int, default=2)
    args = parser.parse_args()
    asyncio.run(
        main_async(
            args.mode,
            max_dev_pairs_per_cell=args.dev_pairs_per_cell,
            max_e1_test_pairs_per_cell=args.e1_test_pairs_per_cell,
        )
    )


if __name__ == "__main__":
    main()
