"""P1.6：阶段 E 配对统计分析与进入 F 阶段四分支决策脚本。

严格执行：
- 以 pair_id（E1/E2）或配对 batch（E3）为单位做配对 bootstrap（10,000 次重采样），绝不把单个 delta 事件或同 batch 内任务当作独立样本。
- 同时报告：(1) 计划配对的触发率/未触发率/失败率；(2) 成功触发且两臂有效的配对差；(3) 全部计划任务上的整体完成情况。
- 供应商 completion_tokens 仅作诊断列（B 断流后为 UNKNOWN），不计算供应商词元节省率。
- 输出 6 份汇总文件（JSON + CSV）及 decision_for_stage_f.json。
"""

from __future__ import annotations

import csv
import json
import random
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PHASE_E = ROOT / "results" / "real" / "phase_e"
DEV_DIR = PHASE_E / "dev"
TEST_DIR = PHASE_E / "test"
SUM_DIR = PHASE_E / "summary"

BOOTSTRAP_ITERS = 10000
BOOTSTRAP_SEED = 20261102


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def write_json_and_csv(stem: str, payload: dict[str, Any], table_rows: list[dict[str, Any]]) -> None:
    SUM_DIR.mkdir(parents=True, exist_ok=True)
    json_path = SUM_DIR / f"{stem}.json"
    csv_path = SUM_DIR / f"{stem}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if table_rows:
        fieldnames: list[str] = []
        for row in table_rows:
            for k in row.keys():
                if k not in fieldnames:
                    fieldnames.append(k)
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in table_rows:
                writer.writerow(row)
    else:
        csv_path.write_text("", encoding="utf-8")


def paired_bootstrap_ci(values: list[float], *, stat: str = "mean") -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "ci95_low": None, "ci95_high": None, "n": 0}
    n = len(values)
    mean_v = statistics.mean(values)
    med_v = statistics.median(values)
    if n == 1:
        v = round(values[0], 4)
        return {"mean": v, "median": v, "ci95_low": v, "ci95_high": v, "n": 1}
    rng = random.Random(BOOTSTRAP_SEED)
    samples: list[float] = []
    for _ in range(BOOTSTRAP_ITERS):
        draw = [values[rng.randrange(n)] for _ in range(n)]
        samples.append(statistics.mean(draw) if stat == "mean" else statistics.median(draw))
    samples.sort()
    low_idx = int(0.025 * BOOTSTRAP_ITERS)
    high_idx = int(0.975 * BOOTSTRAP_ITERS)
    return {
        "mean": round(mean_v, 4),
        "median": round(med_v, 4),
        "stdev": round(statistics.stdev(values), 4),
        "ci95_low": round(samples[low_idx], 4),
        "ci95_high": round(samples[high_idx], 4),
        "n": n,
    }


def group_pairs(rows: list[dict[str, Any]], *, task_id: str = "t0") -> dict[str, dict[str, dict[str, Any]]]:
    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r.get("task_id") != task_id:
            continue
        by_pair.setdefault(r["pair_id"], {})[r["arm"]] = r
    return by_pair


def analyze_e1(e1_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    by_pair = group_pairs(e1_rows, task_id="t0")
    pair_rows: list[dict[str, Any]] = []
    cell_map: dict[str, list[dict[str, Any]]] = {}

    all_delta_text: list[float] = []
    all_delta_events: list[float] = []
    all_delta_slot: list[float] = []

    for pair_id, arms in sorted(by_pair.items()):
        ra, rb = arms.get("A"), arms.get("B")
        if not ra or not rb:
            continue
        both_triggered = ra["trigger_outcome"] == "triggered" and rb["trigger_outcome"] == "triggered"
        d_text = (
            float(ra["post_revocation_observed_text_tokens"]) - float(rb["post_revocation_observed_text_tokens"])
            if both_triggered
            else None
        )
        d_events = (
            float(ra["post_revocation_delta_events"]) - float(rb["post_revocation_delta_events"])
            if both_triggered
            else None
        )
        d_slot = (
            float(ra["old_attempt_slot_hold_s"]) - float(rb["old_attempt_slot_hold_s"])
            if (both_triggered and ra["old_attempt_slot_hold_s"] is not None and rb["old_attempt_slot_hold_s"] is not None)
            else None
        )
        if d_text is not None:
            all_delta_text.append(d_text)
        if d_events is not None:
            all_delta_events.append(d_events)
        if d_slot is not None:
            all_delta_slot.append(d_slot)

        pr = {
            "experiment_block": "E1_cancellation",
            "pair_id": pair_id,
            "cell_id": ra["cell_id"],
            "length_tier": ra["length_tier"],
            "timing_tier": ra["timing_tier"],
            "seed": ra["seed"],
            "arm_order": ra["arm_order"],
            "A_trigger_outcome": ra["trigger_outcome"],
            "B_trigger_outcome": rb["trigger_outcome"],
            "both_triggered": both_triggered,
            "A_pre_revocation_tokens": ra["pre_revocation_observed_text_tokens"],
            "B_pre_revocation_tokens": rb["pre_revocation_observed_text_tokens"],
            "A_post_revocation_text_tokens": ra["post_revocation_observed_text_tokens"],
            "B_post_revocation_text_tokens": rb["post_revocation_observed_text_tokens"],
            "delta_post_revocation_text_tokens": d_text,
            "A_post_revocation_delta_events": ra["post_revocation_delta_events"],
            "B_post_revocation_delta_events": rb["post_revocation_delta_events"],
            "delta_post_revocation_delta_events": d_events,
            "A_post_revocation_tool_events": ra["post_revocation_tool_call_delta_events"],
            "B_post_revocation_tool_events": rb["post_revocation_tool_call_delta_events"],
            "A_slot_hold_s": round(ra["old_attempt_slot_hold_s"], 4) if ra["old_attempt_slot_hold_s"] is not None else None,
            "B_slot_hold_s": round(rb["old_attempt_slot_hold_s"], 4) if rb["old_attempt_slot_hold_s"] is not None else None,
            "delta_slot_hold_s": round(d_slot, 4) if d_slot is not None else None,
            "B_cancel_local_latency_s": round(rb["cancel_local_latency_s"], 4) if rb["cancel_local_latency_s"] is not None else None,
            "A_old_attempt_committed": ra["old_attempt_committed"],
            "B_old_attempt_committed": rb["old_attempt_committed"],
            "A_provider_completion_tokens": ra["provider_completion_tokens"],
            "B_provider_completion_tokens": rb["provider_completion_tokens"],
        }
        pair_rows.append(pr)
        cell_map.setdefault(ra["cell_id"], []).append(pr)

    cell_table: list[dict[str, Any]] = []
    for cell_id, items in sorted(cell_map.items()):
        c_dtext = [x["delta_post_revocation_text_tokens"] for x in items if x["delta_post_revocation_text_tokens"] is not None]
        c_dslot = [x["delta_slot_hold_s"] for x in items if x["delta_slot_hold_s"] is not None]
        cell_table.append(
            {
                "cell_id": cell_id,
                "length_tier": items[0]["length_tier"],
                "timing_tier": items[0]["timing_tier"],
                "planned_pairs": len(items),
                "both_triggered_pairs": sum(1 for x in items if x["both_triggered"]),
                "delta_text_tokens_mean": round(statistics.mean(c_dtext), 2) if c_dtext else None,
                "delta_text_tokens_median": round(statistics.median(c_dtext), 2) if c_dtext else None,
                "delta_slot_hold_s_mean": round(statistics.mean(c_dslot), 3) if c_dslot else None,
                "any_old_attempt_committed": any(x["A_old_attempt_committed"] or x["B_old_attempt_committed"] for x in items),
            }
        )

    summary = {
        "experiment": "E1_cancellation_effect",
        "total_planned_pairs": len(pair_rows),
        "both_triggered_pairs": sum(1 for x in pair_rows if x["both_triggered"]),
        "trigger_rate_pair_level": round(
            sum(1 for x in pair_rows if x["both_triggered"]) / len(pair_rows), 4
        )
        if pair_rows
        else 0.0,
        "primary_rq1_delta_post_revocation_text_tokens_mean_ci": paired_bootstrap_ci(all_delta_text, stat="mean"),
        "primary_rq1_delta_post_revocation_text_tokens_median_ci": paired_bootstrap_ci(all_delta_text, stat="median"),
        "auxiliary_delta_post_revocation_events_mean_ci": paired_bootstrap_ci(all_delta_events, stat="mean"),
        "system_rq2_delta_slot_hold_s_mean_ci": paired_bootstrap_ci(all_delta_slot, stat="mean"),
        "cells": cell_table,
    }
    return summary, cell_table, pair_rows


def analyze_e2(e2_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    by_pair = group_pairs(e2_rows, task_id="t0")
    pair_rows: list[dict[str, Any]] = []
    by_kind: dict[str, list[dict[str, Any]]] = {}

    for pair_id, arms in sorted(by_pair.items()):
        ra, rb = arms.get("A"), arms.get("B")
        if not ra or not rb:
            continue
        both_trig = ra["trigger_outcome"] in {"triggered", "static_control"} and rb["trigger_outcome"] in {
            "triggered",
            "static_control",
        }
        d_text = float(ra["post_revocation_observed_text_tokens"]) - float(rb["post_revocation_observed_text_tokens"])
        d_slot = (
            float(ra["old_attempt_slot_hold_s"]) - float(rb["old_attempt_slot_hold_s"])
            if (ra["old_attempt_slot_hold_s"] is not None and rb["old_attempt_slot_hold_s"] is not None)
            else None
        )
        d_rec = (
            float(ra["recovery_latency_s"]) - float(rb["recovery_latency_s"])
            if (ra["recovery_latency_s"] is not None and rb["recovery_latency_s"] is not None)
            else None
        )
        pr = {
            "experiment_block": "E2_correctness_recovery",
            "pair_id": pair_id,
            "cell_id": ra["cell_id"],
            "task_kind": ra["task_kind"],
            "length_tier": ra["length_tier"],
            "timing_tier": ra["timing_tier"],
            "seed": ra["seed"],
            "arm_order": ra["arm_order"],
            "A_trigger_outcome": ra["trigger_outcome"],
            "B_trigger_outcome": rb["trigger_outcome"],
            "both_triggered": both_trig,
            "A_action_commits": ra["action_commits"],
            "B_action_commits": rb["action_commits"],
            "A_old_attempt_committed": ra["old_attempt_committed"],
            "B_old_attempt_committed": rb["old_attempt_committed"],
            "A_side_effect_once": ra["side_effect_once"],
            "B_side_effect_once": rb["side_effect_once"],
            "A_correctness_ok": ra["correctness_ok"],
            "B_correctness_ok": rb["correctness_ok"],
            "A_post_revocation_text_tokens": ra["post_revocation_observed_text_tokens"],
            "B_post_revocation_text_tokens": rb["post_revocation_observed_text_tokens"],
            "delta_post_revocation_text_tokens": d_text,
            "A_slot_hold_s": round(ra["old_attempt_slot_hold_s"], 4) if ra["old_attempt_slot_hold_s"] is not None else None,
            "B_slot_hold_s": round(rb["old_attempt_slot_hold_s"], 4) if rb["old_attempt_slot_hold_s"] is not None else None,
            "delta_slot_hold_s": round(d_slot, 4) if d_slot is not None else None,
            "A_recovery_latency_s": round(ra["recovery_latency_s"], 4) if ra["recovery_latency_s"] is not None else None,
            "B_recovery_latency_s": round(rb["recovery_latency_s"], 4) if rb["recovery_latency_s"] is not None else None,
            "delta_recovery_latency_s": round(d_rec, 4) if d_rec is not None else None,
            "A_e2e_s": round(ra["end_to_end_latency_s"], 4),
            "B_e2e_s": round(rb["end_to_end_latency_s"], 4),
        }
        pair_rows.append(pr)
        by_kind.setdefault(ra["task_kind"], []).append(pr)

    kind_table: list[dict[str, Any]] = []
    for kind, items in sorted(by_kind.items()):
        kind_table.append(
            {
                "task_kind": kind,
                "planned_pairs": len(items),
                "both_triggered_pairs": sum(1 for x in items if x["both_triggered"]),
                "A_correctness_pass_count": sum(1 for x in items if x["A_correctness_ok"]),
                "B_correctness_pass_count": sum(1 for x in items if x["B_correctness_ok"]),
                "any_old_attempt_committed": any(x["A_old_attempt_committed"] or x["B_old_attempt_committed"] for x in items),
                "A_commits_total": sum(x["A_action_commits"] for x in items),
                "B_commits_total": sum(x["B_action_commits"] for x in items),
            }
        )

    summary = {
        "experiment": "E2_task_correctness_and_recoverability",
        "total_pairs": len(pair_rows),
        "by_task_kind": kind_table,
        "any_old_attempt_committed_across_all_e2": any(
            x["A_old_attempt_committed"] or x["B_old_attempt_committed"] for x in pair_rows
        ),
        "pairs": pair_rows,
    }
    return summary, kind_table, pair_rows


def analyze_e3(e3_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    # Cluster by pair_id (batch level), NOT treating t0/t1/t2 as independent seeds
    batches: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for r in e3_rows:
        batches.setdefault(r["pair_id"], {}).setdefault(r["arm"], []).append(r)

    batch_rows: list[dict[str, Any]] = []
    for pair_id, arms in sorted(batches.items()):
        rows_a = sorted(arms.get("A", []), key=lambda x: x["task_id"])
        rows_b = sorted(arms.get("B", []), key=lambda x: x["task_id"])
        if not rows_a or not rows_b:
            continue
        t0_a, t0_b = rows_a[0], rows_b[0]
        follow_a = rows_a[1:]
        follow_b = rows_b[1:]

        follow_wait_a = statistics.mean([x["first_queue_wait_s"] for x in follow_a]) if follow_a else 0.0
        follow_wait_b = statistics.mean([x["first_queue_wait_s"] for x in follow_b]) if follow_b else 0.0
        commits_a = sum(x["action_commits"] for x in rows_a)
        commits_b = sum(x["action_commits"] for x in rows_b)
        wall_a = t0_a["batch_wall_clock_s"]
        wall_b = t0_b["batch_wall_clock_s"]
        slot_a = t0_a["old_attempt_slot_hold_s"]
        slot_b = t0_b["old_attempt_slot_hold_s"]

        batch_rows.append(
            {
                "experiment_block": "E3_queueing_impact",
                "pair_id": pair_id,
                "cell_id": t0_a["cell_id"],
                "queue_pressure_label": t0_a["queue_pressure_label"],
                "concurrency": t0_a["concurrency"],
                "seed": t0_a["seed"],
                "arm_order": t0_a["arm_order"],
                "A_t0_trigger_outcome": t0_a["trigger_outcome"],
                "B_t0_trigger_outcome": t0_b["trigger_outcome"],
                "A_t0_slot_hold_s": round(slot_a, 4) if slot_a is not None else None,
                "B_t0_slot_hold_s": round(slot_b, 4) if slot_b is not None else None,
                "delta_t0_slot_hold_s": round(slot_a - slot_b, 4) if (slot_a is not None and slot_b is not None) else None,
                "A_followup_mean_queue_wait_s": round(follow_wait_a, 4) if follow_a else None,
                "B_followup_mean_queue_wait_s": round(follow_wait_b, 4) if follow_b else None,
                "delta_followup_queue_wait_s": round(follow_wait_a - follow_wait_b, 4) if follow_a else None,
                "A_batch_commits": commits_a,
                "B_batch_commits": commits_b,
                "A_batch_wall_s": round(wall_a, 4),
                "B_batch_wall_s": round(wall_b, 4),
                "delta_batch_wall_s": round(wall_a - wall_b, 4),
            }
        )

    x3_rows = [b for b in batch_rows if b["concurrency"] == 3]
    d_followup = [b["delta_followup_queue_wait_s"] for b in x3_rows if b["delta_followup_queue_wait_s"] is not None]
    d_t0_slot = [b["delta_t0_slot_hold_s"] for b in batch_rows if b["delta_t0_slot_hold_s"] is not None]
    d_batch_wall = [b["delta_batch_wall_s"] for b in batch_rows if b["delta_batch_wall_s"] is not None]

    summary = {
        "experiment": "E3_serial_queueing_impact",
        "note": "max_inflight=1 serial execution slot queue pressure (NOT parallel multi-connection inference); P99 omitted due to small batch count, reporting batch distributions & medians",
        "total_paired_batches": len(batch_rows),
        "t0_slot_hold_reduction_s_ci": paired_bootstrap_ci(d_t0_slot, stat="mean"),
        "x3_followup_task_queue_wait_reduction_s_ci": paired_bootstrap_ci(d_followup, stat="mean"),
        "batch_wall_clock_delta_s_ci": paired_bootstrap_ci(d_batch_wall, stat="mean"),
        "batches": batch_rows,
    }
    return summary, batch_rows, batch_rows


def analyze_failures_and_nontriggers(all_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counts: dict[str, int] = {}
    flagged: list[dict[str, Any]] = []
    for r in all_rows:
        outcome = r["trigger_outcome"]
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome not in {"triggered", "static_control"} or r.get("backend_failures"):
            flagged.append(
                {
                    "experiment_block": r["experiment_block"],
                    "pair_id": r["pair_id"],
                    "arm": r["arm"],
                    "task_id": r["task_id"],
                    "seed": r["seed"],
                    "trigger_outcome": outcome,
                    "pre_revocation_tokens": r["pre_revocation_observed_text_tokens"],
                    "post_revocation_tokens": r["post_revocation_observed_text_tokens"],
                    "backend_failures": "; ".join(r.get("backend_failures") or []),
                    "error": r.get("error") or "",
                }
            )
    summary = {
        "total_task_rows": len(all_rows),
        "outcome_counts": counts,
        "flagged_rows_count": len(flagged),
        "flagged_rows": flagged,
    }
    return summary, flagged


def decide_stage_f(
    e1_sum: dict[str, Any],
    e2_sum: dict[str, Any],
    e3_sum: dict[str, Any],
) -> dict[str, Any]:
    rq1_ci = e1_sum["primary_rq1_delta_post_revocation_text_tokens_mean_ci"]
    rq2_ci = e1_sum["system_rq2_delta_slot_hold_s_mean_ci"]
    any_old_commit = e2_sum["any_old_attempt_committed_across_all_e2"]

    rec_items = [x for x in e2_sum["pairs"] if x["task_kind"] == "recoverable" and x["both_triggered"]]
    rec_b_ok = sum(1 for x in rec_items if x["B_correctness_ok"])
    rec_a_ok = sum(1 for x in rec_items if x["A_correctness_ok"])

    rq1_clear = (rq1_ci["mean"] or 0.0) >= 25.0 and (rq1_ci["ci95_low"] or 0.0) > 0.0
    rq2_slot_released_earlier = (rq2_ci["mean"] or 0.0) >= 0.40 and (rq2_ci["ci95_low"] or 0.0) > 0.0
    rq3_no_old_commit = not any_old_commit
    rq3_recoverable_preserved = len(rec_items) > 0 and rec_b_ok >= rec_a_ok and rec_b_ok == len(rec_items)

    if not rq3_no_old_commit or (len(rec_items) > 0 and rec_b_ok < rec_a_ok):
        branch = "Branch_D_fix_correctness_first"
        enter_f = False
    elif rq1_clear and rq3_recoverable_preserved and rq2_slot_released_earlier:
        branch = "Branch_A_enter_F_continue_research"
        enter_f = True
    elif rq1_clear and rq3_recoverable_preserved:
        branch = "Branch_B_enter_F_narrow_boundary_focus"
        enter_f = True
    else:
        branch = "Branch_C_stop_expansion"
        enter_f = False

    return {
        "decision_branch": branch,
        "can_enter_stage_f": enter_f,
        "criteria_evaluation": {
            "RQ1_post_revocation_text_tokens_reduced": {
                "passed": rq1_clear,
                "mean_delta_tokens": rq1_ci["mean"],
                "median_delta_tokens": rq1_ci["median"],
                "ci95": [rq1_ci["ci95_low"], rq1_ci["ci95_high"]],
                "threshold": 25.0,
            },
            "RQ2_local_slot_released_earlier": {
                "passed": rq2_slot_released_earlier,
                "mean_delta_slot_hold_s": rq2_ci["mean"],
                "ci95_s": [rq2_ci["ci95_low"], rq2_ci["ci95_high"]],
                "e3_followup_queue_wait_delta_ci_s": e3_sum["x3_followup_task_queue_wait_reduction_s_ci"],
                "threshold_s": 0.40,
            },
            "RQ3_correctness_and_recoverability_preserved": {
                "passed": rq3_no_old_commit and rq3_recoverable_preserved,
                "any_old_attempt_committed": any_old_commit,
                "triggered_recoverable_pairs": len(rec_items),
                "arm_a_recoverable_success": rec_a_ok,
                "arm_b_recoverable_success": rec_b_ok,
            },
        },
        "recommended_stage_f_scope": [
            "F1_benefit_boundaries: short tasks & late revocation where trigger_not_fired / trigger_late dominate",
            "F2_observation_cost: higher observation latency / refresh cost vs cancellation slot savings",
            "F3_backend_boundaries: non-cancellable / non-streaming capability boundary control",
            "F4_load_boundaries: bursty arrivals & serial slot queueing fairness",
            "F5_ablation_1: no early local stream stop but keep Gateway eligibility latch (Arm A vs Arm B)",
            "F6_ablation_2: dependency-filtered refresh vs blind dirty refresh",
        ],
        "explicitly_deferred": [
            "J1 joint scoring scheduler is NOT entered in F until F boundary & ablation studies finish",
        ],
    }


def main() -> None:
    e1_rows = load_jsonl(TEST_DIR / "e1_cancellation.jsonl")
    e2_rows = load_jsonl(TEST_DIR / "e2_correctness.jsonl")
    e3_rows = load_jsonl(TEST_DIR / "e3_queueing.jsonl")
    dev_rows = load_jsonl(DEV_DIR / "e1_dev.jsonl")

    e1_sum, e1_cells, e1_pairs = analyze_e1(e1_rows)
    e2_sum, e2_kinds, e2_pairs = analyze_e2(e2_rows)
    e3_sum, e3_batches, _ = analyze_e3(e3_rows)

    all_paired = e1_pairs + e2_pairs + e3_batches
    fail_sum, fail_rows = analyze_failures_and_nontriggers(dev_rows + e1_rows + e2_rows + e3_rows)
    f_decision = decide_stage_f(e1_sum, e2_sum, e3_sum)

    write_json_and_csv("e1_cancellation_effect", e1_sum, e1_cells)
    write_json_and_csv("e2_task_correctness", e2_sum, e2_kinds)
    write_json_and_csv("e3_queueing_impact", e3_sum, e3_batches)
    write_json_and_csv(
        "paired_differences",
        {
            "e1_rq1_delta_text_tokens": e1_sum["primary_rq1_delta_post_revocation_text_tokens_mean_ci"],
            "e1_rq2_delta_slot_hold_s": e1_sum["system_rq2_delta_slot_hold_s_mean_ci"],
            "e3_delta_t0_slot_hold_s": e3_sum["t0_slot_hold_reduction_s_ci"],
            "e3_delta_followup_queue_wait_s": e3_sum["x3_followup_task_queue_wait_reduction_s_ci"],
            "pairs": all_paired,
        },
        all_paired,
    )
    write_json_and_csv("failures_and_nontriggers", fail_sum, fail_rows)
    (SUM_DIR / "decision_for_stage_f.json").write_text(
        json.dumps(f_decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "stage": "P1.6_summary_manifest",
        "files": [
            "results/real/phase_e/p1_0_freeze_manifest.json",
            "results/real/phase_e/protocol_frozen.json",
            "results/real/phase_e/dev/e1_dev.jsonl",
            "results/real/phase_e/dev/calibration_report.json",
            "results/real/phase_e/test/e1_cancellation.jsonl",
            "results/real/phase_e/test/e2_correctness.jsonl",
            "results/real/phase_e/test/e3_queueing.jsonl",
            "results/real/phase_e/summary/e1_cancellation_effect.json",
            "results/real/phase_e/summary/e2_task_correctness.json",
            "results/real/phase_e/summary/e3_queueing_impact.json",
            "results/real/phase_e/summary/paired_differences.json",
            "results/real/phase_e/summary/failures_and_nontriggers.json",
            "results/real/phase_e/summary/decision_for_stage_f.json",
        ],
        "decision_branch": f_decision["decision_branch"],
        "can_enter_stage_f": f_decision["can_enter_stage_f"],
    }
    (SUM_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Stage E analysis complete: branch={f_decision['decision_branch']} can_enter_f={f_decision['can_enter_stage_f']}")


if __name__ == "__main__":
    main()
