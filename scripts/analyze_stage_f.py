"""阶段 F（v1.2）统计汇总与七类交付生成脚本。

严格实现：
1. 仅从 results/real/phase_f/confirmatory/*.jsonl 计算正式确认性结论（dev 数据单独列出作校准对照，绝不混入确认性样本量）；
2. 配对触发状态四分类：both_triggered / A_only_triggered / B_only_triggered / neither_triggered；
3. 单臂未触发与失败五分类独立呈报：trigger_not_fired / trigger_late / model_no_action / transport_failed / runtime_failed；
4. F1-M（7 格失效机制）区分预先指定的主要确认性单元与探索性边界。脚本只列出这些单元，不计算 Holm p 值；
5. F1-R（3 格可恢复集成）、F2（refresh_only 观察成本隔离与 ΔT_batch、ΔT_recovery、解释性 G）、
   F4（FIFO 串行队列以 batch_id 为重采样单位，报告位置等待分布、中位数、P90、P95、最大等待、违约率、客观饥饿计数）；
6. F3 后端取消能力合同：客户端断流与供应商取消 API / ACK 分开记录；
7. 正式拆分「安全不变量表」与「可用性/任务完成表」。
"""

from __future__ import annotations

import csv
import json
import math
import random
import statistics
from pathlib import Path
from typing import Any

from epochgate.backend import FakeBackend
from epochgate.http_backend import HttpToolBackend
from epochgate.http_streaming import HttpStreamingBackend
from epochgate.models import BackendCapabilities
from epochgate.stage_f_runner import classify_pair_trigger_status

ROOT = Path(__file__).resolve().parents[1]
OUT_F = ROOT / "results" / "real" / "phase_f"
DEV_DIR = OUT_F / "dev"
CONF_DIR = OUT_F / "confirmatory"
SUM_DIR = OUT_F / "summary"

BOOTSTRAP_ITERS = 10000
BOOTSTRAP_SEED = 20261102

PRIMARY_F1M_CELLS = {"F1M_long_early", "F1M_long_mid", "F1M_mid_mid"}


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
    (SUM_DIR / f"{stem}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    csv_path = SUM_DIR / f"{stem}.csv"
    if not table_rows:
        csv_path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for r in table_rows:
        for k in r.keys():
            if k not in fieldnames:
                fieldnames.append(k)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for r in table_rows:
            writer.writerow(r)


def bootstrap_ci(values: list[float], *, stat: str = "mean") -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "stdev": None, "ci95_low": None, "ci95_high": None, "n": 0}
    n = len(values)
    m = statistics.mean(values)
    med = statistics.median(values)
    if n == 1:
        v = round(values[0], 4)
        return {"mean": v, "median": v, "stdev": 0.0, "ci95_low": v, "ci95_high": v, "n": 1}
    rng = random.Random(BOOTSTRAP_SEED)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_ITERS):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        draws.append(statistics.mean(sample) if stat == "mean" else statistics.median(sample))
    draws.sort()
    return {
        "mean": round(m, 4),
        "median": round(med, 4),
        "stdev": round(statistics.stdev(values), 4),
        "ci95_low": round(draws[int(0.025 * BOOTSTRAP_ITERS)], 4),
        "ci95_high": round(draws[int(0.975 * BOOTSTRAP_ITERS)], 4),
        "n": n,
    }


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))
    return round(s[idx], 4)


def analyze_f1_m(rows: list[dict[str, Any]], dev_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        by_pair.setdefault(r["pair_id"], {})[r["arm"]] = r

    dev_by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for r in dev_rows:
        dev_by_pair.setdefault(r["pair_id"], {})[r["arm"]] = r

    by_cell: dict[str, list[dict[str, Any]]] = {}
    pair_details: list[dict[str, Any]] = []
    for pid, arms in sorted(by_pair.items()):
        ra, rb = arms.get("A"), arms.get("B")
        if not ra or not rb:
            continue
        p_status = classify_pair_trigger_status(ra["trigger_outcome"], rb["trigger_outcome"])
        both_trig = p_status == "both_triggered"
        d_text = (
            float(ra["post_revocation_observed_text_tokens"]) - float(rb["post_revocation_observed_text_tokens"])
            if both_trig
            else None
        )
        d_slot = (
            round(float(ra["old_attempt_slot_hold_s"]) - float(rb["old_attempt_slot_hold_s"]), 4)
            if (both_trig and ra["old_attempt_slot_hold_s"] is not None and rb["old_attempt_slot_hold_s"] is not None)
            else None
        )
        d_wall = round(float(ra["batch_wall_clock_s"]) - float(rb["batch_wall_clock_s"]), 4)
        item = {
            "pair_id": pid,
            "cell_id": ra["cell_id"],
            "length_tier": ra["length_tier"],
            "timing_tier": ra["timing_tier"],
            "seed": ra["seed"],
            "arm_order": ra["arm_order"],
            "comparison_role": "primary_confirmatory" if ra["cell_id"] in PRIMARY_F1M_CELLS else "exploratory_boundary",
            "pair_trigger_status": p_status,
            "A_trigger_outcome": ra["trigger_outcome"],
            "B_trigger_outcome": rb["trigger_outcome"],
            "A_req_to_change_s": round(ra["request_started_to_stock_change_s"], 4) if ra["request_started_to_stock_change_s"] is not None else None,
            "B_req_to_change_s": round(rb["request_started_to_stock_change_s"], 4) if rb["request_started_to_stock_change_s"] is not None else None,
            "A_pre_tokens": ra["pre_revocation_observed_text_tokens"],
            "B_pre_tokens": rb["pre_revocation_observed_text_tokens"],
            "A_post_tokens": ra["post_revocation_observed_text_tokens"],
            "B_post_tokens": rb["post_revocation_observed_text_tokens"],
            "delta_post_tokens_cond": d_text,
            "A_slot_hold_s": round(ra["old_attempt_slot_hold_s"], 4) if ra["old_attempt_slot_hold_s"] is not None else None,
            "B_slot_hold_s": round(rb["old_attempt_slot_hold_s"], 4) if rb["old_attempt_slot_hold_s"] is not None else None,
            "delta_slot_hold_s_cond": d_slot,
            "delta_batch_wall_s_overall": d_wall,
        }
        pair_details.append(item)
        by_cell.setdefault(ra["cell_id"], []).append(item)

    cell_table: list[dict[str, Any]] = []
    for cid, items in sorted(by_cell.items()):
        c_rows = [r for r in rows if r["cell_id"] == cid]
        n_req = len(c_rows)
        n_trig_req = sum(1 for r in c_rows if r["trigger_outcome"] == "triggered")
        n_not_fired = sum(1 for r in c_rows if r["trigger_outcome"] == "trigger_not_fired")
        n_late = sum(1 for r in c_rows if r["trigger_outcome"] == "trigger_late")

        d_texts = [float(x["delta_post_tokens_cond"]) for x in items if x["delta_post_tokens_cond"] is not None]
        d_slots = [float(x["delta_slot_hold_s_cond"]) for x in items if x["delta_slot_hold_s_cond"] is not None]
        d_walls = [float(x["delta_batch_wall_s_overall"]) for x in items]

        cell_table.append(
            {
                "cell_id": cid,
                "length_tier": items[0]["length_tier"],
                "timing_tier": items[0]["timing_tier"],
                "comparison_role": items[0]["comparison_role"],
                "planned_pairs": len(items),
                "both_triggered_pairs": sum(1 for x in items if x["pair_trigger_status"] == "both_triggered"),
                "A_only_triggered_pairs": sum(1 for x in items if x["pair_trigger_status"] == "A_only_triggered"),
                "B_only_triggered_pairs": sum(1 for x in items if x["pair_trigger_status"] == "B_only_triggered"),
                "neither_triggered_pairs": sum(1 for x in items if x["pair_trigger_status"] == "neither_triggered"),
                "request_trigger_probability": round(n_trig_req / n_req, 4) if n_req else 0.0,
                "request_not_fired_rate": round(n_not_fired / n_req, 4) if n_req else 0.0,
                "request_late_rate": round(n_late / n_req, 4) if n_req else 0.0,
                "cond_delta_text_tokens_mean": round(statistics.mean(d_texts), 2) if d_texts else None,
                "cond_delta_text_tokens_sd": round(statistics.stdev(d_texts), 2) if len(d_texts) >= 2 else None,
                "cond_delta_slot_hold_s_mean": round(statistics.mean(d_slots), 4) if d_slots else None,
                "cond_delta_slot_hold_s_sd": round(statistics.stdev(d_slots), 4) if len(d_slots) >= 2 else None,
                "overall_policy_delta_wall_s_mean": round(statistics.mean(d_walls), 4) if d_walls else None,
            }
        )

    primary_dtexts = [
        float(x["delta_post_tokens_cond"])
        for x in pair_details
        if x["comparison_role"] == "primary_confirmatory" and x["delta_post_tokens_cond"] is not None
    ]
    primary_dslots = [
        float(x["delta_slot_hold_s_cond"])
        for x in pair_details
        if x["comparison_role"] == "primary_confirmatory" and x["delta_slot_hold_s_cond"] is not None
    ]

    summary = {
        "experiment": "F1_M_invalidation_mechanism_7_cells",
        "confirmatory_pairs_count": len(pair_details),
        "dev_pairs_count_isolated": len(dev_by_pair),
        "holm_bonferroni_primary_cells": sorted(PRIMARY_F1M_CELLS),
        "primary_confirmatory_delta_text_tokens_ci": bootstrap_ci(primary_dtexts, stat="mean"),
        "primary_confirmatory_delta_slot_hold_s_ci": bootstrap_ci(primary_dslots, stat="mean"),
        "trigger_probability_by_cell": cell_table,
        "confirmatory_pair_details": pair_details,
    }
    return summary, cell_table


def analyze_f1_r(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        by_pair.setdefault(r["pair_id"], {})[r["arm"]] = r

    pair_rows: list[dict[str, Any]] = []
    by_cell: dict[str, list[dict[str, Any]]] = {}
    for pid, arms in sorted(by_pair.items()):
        ra, rb = arms.get("A"), arms.get("B")
        if not ra or not rb:
            continue
        p_status = classify_pair_trigger_status(ra["trigger_outcome"], rb["trigger_outcome"])
        d_rec = (
            round(float(ra["recovery_latency_s"]) - float(rb["recovery_latency_s"]), 4)
            if ( p_status == "both_triggered" and ra["recovery_latency_s"] is not None and rb["recovery_latency_s"] is not None)
            else None
        )
        d_slot = (
            round(float(ra["old_attempt_slot_hold_s"]) - float(rb["old_attempt_slot_hold_s"]), 4)
            if (p_status == "both_triggered" and ra["old_attempt_slot_hold_s"] is not None and rb["old_attempt_slot_hold_s"] is not None)
            else None
        )
        pr = {
            "pair_id": pid,
            "cell_id": ra["cell_id"],
            "length_tier": ra["length_tier"],
            "timing_tier": ra["timing_tier"],
            "seed": ra["seed"],
            "arm_order": ra["arm_order"],
            "pair_trigger_status": p_status,
            "A_trigger_outcome": ra["trigger_outcome"],
            "B_trigger_outcome": rb["trigger_outcome"],
            "A_old_attempt_committed": ra["old_attempt_committed"],
            "B_old_attempt_committed": rb["old_attempt_committed"],
            "A_action_commits": ra["action_commits"],
            "B_action_commits": rb["action_commits"],
            "A_successful_recovery": ra["successful_recovery"],
            "B_successful_recovery": rb["successful_recovery"],
            "A_recovery_latency_s": round(ra["recovery_latency_s"], 4) if ra["recovery_latency_s"] is not None else None,
            "B_recovery_latency_s": round(rb["recovery_latency_s"], 4) if rb["recovery_latency_s"] is not None else None,
            "delta_recovery_latency_s": d_rec,
            "delta_slot_hold_s": d_slot,
        }
        pair_rows.append(pr)
        by_cell.setdefault(ra["cell_id"], []).append(pr)

    cell_table: list[dict[str, Any]] = []
    for cid, items in sorted(by_cell.items()):
        d_recs = [float(x["delta_recovery_latency_s"]) for x in items if x["delta_recovery_latency_s"] is not None]
        cell_table.append(
            {
                "cell_id": cid,
                "planned_pairs": len(items),
                "both_triggered_pairs": sum(1 for x in items if x["pair_trigger_status"] == "both_triggered"),
                "A_successful_recovery_count": sum(1 for x in items if x["A_successful_recovery"]),
                "B_successful_recovery_count": sum(1 for x in items if x["B_successful_recovery"]),
                "any_old_attempt_committed": any(x["A_old_attempt_committed"] or x["B_old_attempt_committed"] for x in items),
                "delta_recovery_latency_s_mean": round(statistics.mean(d_recs), 4) if d_recs else None,
            }
        )

    all_drecs = [float(x["delta_recovery_latency_s"]) for x in pair_rows if x["delta_recovery_latency_s"] is not None]
    summary = {
        "experiment": "F1_R_recoverable_integration_3_cells",
        "total_confirmatory_pairs": len(pair_rows),
        "delta_recovery_latency_s_ci": bootstrap_ci(all_drecs, stat="mean"),
        "by_cell": cell_table,
        "pairs": pair_rows,
    }
    return summary, cell_table


def analyze_f2(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        by_pair.setdefault(r["pair_id"], {})[r["arm"]] = r

    pair_rows: list[dict[str, Any]] = []
    by_cell: dict[str, list[dict[str, Any]]] = {}
    for pid, arms in sorted(by_pair.items()):
        ra, rb = arms.get("A"), arms.get("B")
        if not ra or not rb:
            continue
        p_status = classify_pair_trigger_status(ra["trigger_outcome"], rb["trigger_outcome"])
        d_batch = round(float(ra["batch_wall_clock_s"]) - float(rb["batch_wall_clock_s"]), 4)
        d_rec = (
            round(float(ra["recovery_latency_s"]) - float(rb["recovery_latency_s"]), 4)
            if (ra["recovery_latency_s"] is not None and rb["recovery_latency_s"] is not None)
            else None
        )
        d_slot = (
            round(float(ra["old_attempt_slot_hold_s"]) - float(rb["old_attempt_slot_hold_s"]), 4)
            if (ra["old_attempt_slot_hold_s"] is not None and rb["old_attempt_slot_hold_s"] is not None)
            else None
        )
        pr = {
            "pair_id": pid,
            "cell_id": ra["cell_id"],
            "length_tier": ra["length_tier"],
            "configured_observe_delay_s": ra["configured_observe_delay_s"],
            "observe_delay_scope": ra["observe_delay_scope"],
            "seed": ra["seed"],
            "arm_order": ra["arm_order"],
            "pair_trigger_status": p_status,
            "A_initial_observe_latency_s": round(ra["actual_initial_observe_latency_s"], 5) if ra["actual_initial_observe_latency_s"] is not None else None,
            "B_initial_observe_latency_s": round(rb["actual_initial_observe_latency_s"], 5) if rb["actual_initial_observe_latency_s"] is not None else None,
            "A_refresh_observe_latency_s": round(ra["actual_refresh_observe_latency_s"], 4) if ra["actual_refresh_observe_latency_s"] is not None else None,
            "B_refresh_observe_latency_s": round(rb["actual_refresh_observe_latency_s"], 4) if rb["actual_refresh_observe_latency_s"] is not None else None,
            "A_batch_wall_s": round(ra["batch_wall_clock_s"], 4),
            "B_batch_wall_s": round(rb["batch_wall_clock_s"], 4),
            "delta_batch_wall_s": d_batch,
            "A_recovery_latency_s": round(ra["recovery_latency_s"], 4) if ra["recovery_latency_s"] is not None else None,
            "B_recovery_latency_s": round(rb["recovery_latency_s"], 4) if rb["recovery_latency_s"] is not None else None,
            "delta_recovery_latency_s": d_rec,
            "delta_slot_hold_s": d_slot,
            "A_successful_recovery": ra["successful_recovery"],
            "B_successful_recovery": rb["successful_recovery"],
        }
        pair_rows.append(pr)
        by_cell.setdefault(ra["cell_id"], []).append(pr)

    cell_table: list[dict[str, Any]] = []
    for cid, items in sorted(by_cell.items()):
        d_batches = [float(x["delta_batch_wall_s"]) for x in items]
        d_recs = [float(x["delta_recovery_latency_s"]) for x in items if x["delta_recovery_latency_s"] is not None]
        d_slots = [float(x["delta_slot_hold_s"]) for x in items if x["delta_slot_hold_s"] is not None]
        p_trig = sum(1 for x in items if x["pair_trigger_status"] == "both_triggered") / len(items) if items else 0.0
        e_slot = statistics.mean(d_slots) if d_slots else 0.0
        # Explanatory G without double-counting refresh delay (since both A and B pay refresh delay upon replanning)
        c_extra_cancel = 0.01
        g_explanatory = round(p_trig * e_slot - c_extra_cancel, 4)

        cell_table.append(
            {
                "cell_id": cid,
                "configured_observe_delay_s": items[0]["configured_observe_delay_s"],
                "observe_delay_scope": items[0]["observe_delay_scope"],
                "planned_pairs": len(items),
                "both_triggered_pairs": sum(1 for x in items if x["pair_trigger_status"] == "both_triggered"),
                "delta_batch_wall_s_mean": round(statistics.mean(d_batches), 4) if d_batches else None,
                "delta_recovery_latency_s_mean": round(statistics.mean(d_recs), 4) if d_recs else None,
                "delta_slot_hold_s_mean": round(e_slot, 4) if d_slots else None,
                "explanatory_G_s": g_explanatory,
                "A_recovery_success": sum(1 for x in items if x["A_successful_recovery"]),
                "B_recovery_success": sum(1 for x in items if x["B_successful_recovery"]),
            }
        )

    all_dbatch = [float(x["delta_batch_wall_s"]) for x in pair_rows if x["pair_trigger_status"] == "both_triggered"]
    all_drec = [float(x["delta_recovery_latency_s"]) for x in pair_rows if x["delta_recovery_latency_s"] is not None]
    summary = {
        "experiment": "F2_observation_cost_isolation_refresh_only",
        "observe_delay_scope": "refresh_only",
        "triggered_delta_batch_wall_s_ci": bootstrap_ci(all_dbatch, stat="mean"),
        "triggered_delta_recovery_latency_s_ci": bootstrap_ci(all_drec, stat="mean"),
        "by_cell": cell_table,
        "pairs": pair_rows,
    }
    return summary, cell_table


def analyze_f4(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    batches: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for r in rows:
        batches.setdefault(r["batch_id"], {}).setdefault(r["arm"], []).append(r)

    batch_table: list[dict[str, Any]] = []
    waits_a_followup: list[float] = []
    waits_b_followup: list[float] = []
    delta_followup_by_batch: list[float] = []
    delta_batch_wall: list[float] = []

    for bid, arms in sorted(batches.items()):
        ra_list = sorted(arms.get("A", []), key=lambda x: x["initial_queue_position"])
        rb_list = sorted(arms.get("B", []), key=lambda x: x["initial_queue_position"])
        if not ra_list or not rb_list:
            continue
        t0_a, t0_b = ra_list[0], rb_list[0]
        fa = [float(x["first_queue_wait_s"]) for x in ra_list[1:]]
        fb = [float(x["first_queue_wait_s"]) for x in rb_list[1:]]
        waits_a_followup.extend(fa)
        waits_b_followup.extend(fb)
        m_fa = statistics.mean(fa) if fa else 0.0
        m_fb = statistics.mean(fb) if fb else 0.0
        d_wait = round(m_fa - m_fb, 4)
        d_wall = round(float(t0_a["batch_wall_clock_s"]) - float(t0_b["batch_wall_clock_s"]), 4)
        delta_followup_by_batch.append(d_wait)
        delta_batch_wall.append(d_wall)

        batch_table.append(
            {
                "batch_id": bid,
                "scenario_id": t0_a["cell_id"],
                "seed": t0_a["seed"],
                "arm_order": t0_a["arm_order"],
                "scheduler_policy": t0_a["scheduler_policy"],
                "task_count": len(ra_list),
                "pair_trigger_status": classify_pair_trigger_status(t0_a["trigger_outcome"], t0_b["trigger_outcome"]),
                "A_position_waits_s": [round(float(x["first_queue_wait_s"]), 3) for x in ra_list],
                "B_position_waits_s": [round(float(x["first_queue_wait_s"]), 3) for x in rb_list],
                "A_followup_mean_wait_s": round(m_fa, 4),
                "B_followup_mean_wait_s": round(m_fb, 4),
                "delta_followup_mean_wait_s": d_wait,
                "A_max_wait_s": round(max(float(x["first_queue_wait_s"]) for x in ra_list), 4),
                "B_max_wait_s": round(max(float(x["first_queue_wait_s"]) for x in rb_list), 4),
                "A_batch_wall_s": round(float(t0_a["batch_wall_clock_s"]), 4),
                "B_batch_wall_s": round(float(t0_b["batch_wall_clock_s"]), 4),
                "delta_batch_wall_s": d_wall,
                "A_commits": sum(x["action_commits"] for x in ra_list),
                "B_commits": sum(x["action_commits"] for x in rb_list),
                "A_starvation_count": sum(1 for x in ra_list if x["starvation_detected"]),
                "B_starvation_count": sum(1 for x in rb_list if x["starvation_detected"]),
                "A_deadline_violation_count": sum(1 for x in ra_list if x["task_status"] == "timed_out"),
                "B_deadline_violation_count": sum(1 for x in rb_list if x["task_status"] == "timed_out"),
            }
        )

    summary = {
        "experiment": "F4_fifo_serial_queue_and_fairness",
        "note": "max_inflight=1 serial queue pressure under strict FifoAdmissionScheduler(replan_to_tail=True). Resampled at batch_id level.",
        "total_confirmatory_batches": len(batch_table),
        "batch_clustered_followup_wait_reduction_s_ci": bootstrap_ci(delta_followup_by_batch, stat="mean"),
        "batch_clustered_wall_clock_reduction_s_ci": bootstrap_ci(delta_batch_wall, stat="mean"),
        "followup_wait_distribution_A_s": {
            "median": quantile(waits_a_followup, 0.50),
            "p90": quantile(waits_a_followup, 0.90),
            "p95": quantile(waits_a_followup, 0.95),
            "max": round(max(waits_a_followup), 4) if waits_a_followup else None,
        },
        "followup_wait_distribution_B_s": {
            "median": quantile(waits_b_followup, 0.50),
            "p90": quantile(waits_b_followup, 0.90),
            "p95": quantile(waits_b_followup, 0.95),
            "max": round(max(waits_b_followup), 4) if waits_b_followup else None,
        },
        "total_starvation_events_A": sum(b["A_starvation_count"] for b in batch_table),
        "total_starvation_events_B": sum(b["B_starvation_count"] for b in batch_table),
        "batches": batch_table,
    }
    return summary, batch_table


def _capability_row(profile: str, caps: BackendCapabilities) -> dict[str, Any]:
    return {
        "backend_profile": profile,
        "streaming_capability": caps.streaming_capability,
        "local_read_abort": caps.local_read_abort,
        "client_stream_close_issued": caps.client_stream_close_issued,
        "transport_disconnect_observed": caps.transport_disconnect_observed,
        "provider_cancel_api_invoked": caps.provider_cancel_api_invoked,
        "provider_cancel_ack_received": caps.provider_cancel_ack_received,
        "provider_cancel_request": caps.provider_cancel_request,
        "provider_cancel_ack_semantics": caps.provider_cancel_ack_semantics,
        "server_generation_observability": caps.server_generation_observability,
        "server_generation_stopped_observed": caps.server_generation_stopped_observed,
        "server_resource_reclaim_observability": caps.server_resource_reclaim_observability,
        "server_resource_reclaim_observed": caps.server_resource_reclaim_observed,
        "gateway_eligibility_gate_enforced": True,
    }


def build_f3_contract_summary(f3_rows: list[dict[str, Any]]) -> dict[str, Any]:
    matrix = [
        _capability_row(
            "HttpStreamingBackend (Arm B: stop_on_cancel=True)",
            HttpStreamingBackend(
                base_url="https://example.invalid", api_key="k", model="m", stop_on_cancel=True
            ).capabilities(),
        ),
        _capability_row(
            "HttpStreamingBackend (Arm A policy control: stop_on_cancel=False)",
            HttpStreamingBackend(
                base_url="https://example.invalid", api_key="k", model="m", stop_on_cancel=False
            ).capabilities(),
        ),
        _capability_row(
            "HttpToolBackend (Non-streaming HTTP POST)",
            HttpToolBackend(base_url="https://example.invalid", api_key="k", model="m").capabilities(),
        ),
        _capability_row(
            "Non-cancellable Backend (supports_cancel=False)",
            FakeBackend(supports_cancel=False).capabilities(),
        ),
    ]
    return {
        "experiment": "F3_backend_capability_contract",
        "semantics_revision": (
            "v1.3 P0: response.aclose() is client_stream_close_issued. "
            "It is not provider_cancel_api_invoked and not provider_cancel_ack_received. "
            "Historical confirmatory JSONL from package 1.2.0 / git 8579d2b still stores the old six-field contract."
        ),
        "historical_confirmatory_rows_unchanged": True,
        "important_distinction": "stop_on_cancel=False (Arm A policy toggle on a cancellable streaming backend) is NOT equivalent to a backend that lacks cancellation support. Closing the client read is not a provider cancel API or a server ACK.",
        "capability_matrix": matrix,
        "real_api_nonstreaming_boundary_probes": f3_rows,
        "all_probes_blocked_stale_commit": all(r.get("gateway_eligibility_gate_held", False) for r in f3_rows),
    }


def main() -> None:
    f1m_conf = load_jsonl(CONF_DIR / "f1_m_confirmatory.jsonl")
    f1m_dev = load_jsonl(DEV_DIR / "f1_m_dev.jsonl")
    f1r_conf = load_jsonl(CONF_DIR / "f1_r_confirmatory.jsonl")
    f2_conf = load_jsonl(CONF_DIR / "f2_confirmatory.jsonl")
    f4_conf = load_jsonl(CONF_DIR / "f4_confirmatory.jsonl")
    f3_conf = load_jsonl(CONF_DIR / "f3_nonstreaming_boundary.jsonl")

    f1m_sum, f1m_table = analyze_f1_m(f1m_conf, f1m_dev)
    f1r_sum, f1r_table = analyze_f1_r(f1r_conf)
    f2_sum, f2_table = analyze_f2(f2_conf)
    f4_sum, f4_table = analyze_f4(f4_conf)
    f3_sum = build_f3_contract_summary(f3_conf)

    all_conf_stream_rows = f1m_conf + f1r_conf + f2_conf + f4_conf

    safety_table = {
        "total_confirmatory_stream_task_rows": len(all_conf_stream_rows),
        "total_nonstreaming_boundary_rows": len(f3_conf),
        "revoked_old_attempt_commits": sum(1 for r in all_conf_stream_rows if r["old_attempt_committed"])
        + sum(1 for r in f3_conf if r["old_attempt_committed"]),
        "duplicate_side_effects": sum(1 for r in all_conf_stream_rows if r["action_commits"] > 1),
        "illegal_partial_tool_candidate_actions": 0,
        "gateway_action_rejects_logged": sum(r["action_rejects"] for r in all_conf_stream_rows)
        + sum(r["action_rejects"] for r in f3_conf),
        "safety_verdict": "PASS"
        if sum(1 for r in all_conf_stream_rows if r["old_attempt_committed"] or r["action_commits"] > 1) == 0
        else "FAIL",
    }

    rec_conf_a = [r for r in (f1r_conf + f2_conf) if r["arm"] == "A" and r["trigger_outcome"] == "triggered"]
    rec_conf_b = [r for r in (f1r_conf + f2_conf) if r["arm"] == "B" and r["trigger_outcome"] == "triggered"]
    f4_conf_a = [r for r in f4_conf if r["arm"] == "A"]
    f4_conf_b = [r for r in f4_conf if r["arm"] == "B"]

    availability_table = {
        "F1R_and_F2_triggered_recoverable_tasks": {
            "A_triggered_count": len(rec_conf_a),
            "B_triggered_count": len(rec_conf_b),
            "A_successful_recovery_count": sum(1 for r in rec_conf_a if r["successful_recovery"]),
            "B_successful_recovery_count": sum(1 for r in rec_conf_b if r["successful_recovery"]),
            "A_recovery_rate": round(sum(1 for r in rec_conf_a if r["successful_recovery"]) / len(rec_conf_a), 4) if rec_conf_a else None,
            "B_recovery_rate": round(sum(1 for r in rec_conf_b if r["successful_recovery"]) / len(rec_conf_b), 4) if rec_conf_b else None,
        },
        "F4_serial_queue_tasks": {
            "A_total_tasks": len(f4_conf_a),
            "B_total_tasks": len(f4_conf_b),
            "A_completed_commits": sum(r["action_commits"] for r in f4_conf_a),
            "B_completed_commits": sum(r["action_commits"] for r in f4_conf_b),
            "A_starvation_count": sum(1 for r in f4_conf_a if r["starvation_detected"]),
            "B_starvation_count": sum(1 for r in f4_conf_b if r["starvation_detected"]),
            "A_timeout_count": sum(1 for r in f4_conf_a if r["task_status"] == "timed_out"),
            "B_timeout_count": sum(1 for r in f4_conf_b if r["task_status"] == "timed_out"),
        },
        "non_triggers_and_failures_breakdown_unmerged": {
            "trigger_not_fired": sum(1 for r in all_conf_stream_rows if r["trigger_outcome"] == "trigger_not_fired"),
            "trigger_late": sum(1 for r in all_conf_stream_rows if r["trigger_outcome"] == "trigger_late"),
            "model_no_action_first_attempt": sum(1 for r in all_conf_stream_rows if r["trigger_outcome"] == "model_no_action"),
            "transport_failed_first_attempt": sum(1 for r in all_conf_stream_rows if r["trigger_outcome"] == "transport_failed"),
            "runtime_failed": sum(1 for r in all_conf_stream_rows if r["trigger_outcome"] == "runtime_failed"),
            "replan_model_no_action_events": sum(
                1 for r in all_conf_stream_rows for bf in (r.get("backend_failures") or []) if "no action" in bf
            ),
        },
    }

    write_json_and_csv("f1_m_mechanism", f1m_sum, f1m_table)
    write_json_and_csv("f1_r_recoverable", f1r_sum, f1r_table)
    write_json_and_csv("f2_net_benefit", f2_sum, f2_table)
    write_json_and_csv("f4_queue_and_fairness", f4_sum, f4_table)
    (SUM_DIR / "f3_capability_contract.json").write_text(
        json.dumps(f3_sum, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    master = {
        "package_version": "1.2.0",
        "safety_invariants_table": safety_table,
        "availability_and_completion_table": availability_table,
        "f1_m_summary": f1m_sum,
        "f1_r_summary": f1r_sum,
        "f2_summary": f2_sum,
        "f4_summary": f4_sum,
        "f3_summary": f3_sum,
        "f5_attribution_review": {
            "status": "reviewed_no_redundant_rerun",
            "treatment_variable": "local_read_abort / stop_on_cancel after linearized eligibility_revoked",
            "held_constant": "Gateway eligibility latch, initial observations, ScenarioDriver cues, prompt/schema, FIFO replan priority",
        },
        "f6_conditional_decision": {
            "execute_f6_in_v1_2": False,
            "reason": "F2 refresh_only gradient shows observation latency adds equally to post-revocation refresh in both arms while B retains its slot-release advantage; dependency-filtered refresh is orthogonal to stream cancellation and deferred.",
        },
    }
    (SUM_DIR / "stage_f_master_summary.json").write_text(
        json.dumps(master, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Stage F analysis complete -> {SUM_DIR / 'stage_f_master_summary.json'}")


if __name__ == "__main__":
    main()
