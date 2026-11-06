"""Gate A0。调用方不得把留出标签用于改系数。"""

from __future__ import annotations

import numpy as np

from epochgate.ucr2_cost import cost_oracle, multiplier, terminal_cost
from epochgate.ucr2_protocol import COSTLY_PROBE_SECONDS, OVERLAP_GAP
from epochgate.ucr2_workload import iter_calibration, iter_dev, iter_holdout, reveal, time_bin


def partial_corr(y: np.ndarray, x: np.ndarray, controls: np.ndarray) -> float:
    design = np.column_stack([np.ones(len(y)), controls])
    beta_y, *_ = np.linalg.lstsq(design, y, rcond=None)
    beta_x, *_ = np.linalg.lstsq(design, x, rcond=None)
    residual_y = y - design @ beta_y
    residual_x = x - design @ beta_x
    if residual_y.std() < 1e-8 or residual_x.std() < 1e-8:
        return 0.0
    return float(np.corrcoef(residual_y, residual_x)[0, 1])


def information_value(example) -> tuple[float, bool]:
    before = cost_oracle(example)
    revealed = reveal(example)
    after = cost_oracle(revealed)
    before_cost, _ = terminal_cost(before, example, None)
    after_cost, _ = terminal_cost(after, revealed, None)
    return before_cost - after_cost, before != after


def summarize(rows: list) -> dict:
    remaining = np.asarray([row.true_remaining_s for row in rows])
    restart = np.asarray([row.true_restart_s for row in rows])
    generated = np.asarray([row.features["generated_tokens"] for row in rows])
    total = np.asarray([row.n_total for row in rows])
    rate = np.asarray([row.token_rate_true for row in rows])
    gap = np.abs(restart - remaining) / np.maximum(np.maximum(restart, remaining), 1e-6)
    overlap = gap < OVERLAP_GAP
    values = []
    changed = []
    for row in rows:
        value, did = information_value(row)
        values.append(value)
        changed.append(did)
    values_a = np.asarray(values)
    changed_a = np.asarray(changed)
    by_tool = {}
    for name, code in (("reserve", 0.0), ("query", 1.0)):
        mask = np.asarray([row.features["tool_type"] == code for row in rows])
        if np.any(mask):
            by_tool[name] = {"S": float(remaining[mask].mean()), "C": float(restart[mask].mean()), "n": int(mask.sum())}
    by_time = {}
    for name, bin_id in (("early", 0), ("mid", 1), ("late", 2)):
        mask = np.asarray([time_bin(row.features["predicate_time"]) == bin_id for row in rows])
        if np.any(mask):
            by_time[name] = float(restart[mask].mean())
    classes = {}
    for label in ("uninformative", "informative", "costly"):
        mask = np.asarray([row.probe_class == label for row in rows])
        classes[label] = {
            "n": int(mask.sum()),
            "change_rate": float(changed_a[mask].mean()) if np.any(mask) else None,
        }
    costly = np.asarray([row.probe_class == "costly" for row in rows])
    probe_cost = np.asarray([
        (COSTLY_PROBE_SECONDS if row.probe_class == "costly" else float(row.features["probe_budget"])) * multiplier(row)
        for row in rows
    ])
    high = generated >= np.median(generated)
    timeout = np.asarray([
        row.predicate_now == "invalid" or row.true_remaining_s > row.completion_horizon for row in rows
    ])
    overlap_values = values_a[overlap]
    return {
        "n": len(rows),
        "corr_g_s": partial_corr(remaining, generated, np.column_stack([total, rate])),
        "corr_inv_r_s": partial_corr(remaining, 1.0 / rate, np.column_stack([total, generated])),
        "by_tool": by_tool,
        "c_by_time": by_time,
        "mean_s_high_tokens": float(remaining[high].mean()),
        "mean_s_low_tokens": float(remaining[~high].mean()),
        "overlap_fraction": float(overlap.mean()),
        "overlap_positive": float(np.mean(overlap_values > 0)),
        "overlap_negative": float(np.mean(overlap_values < 0)),
        "classes": classes,
        "costly_value_below_cost": float(np.mean(values_a[costly] < probe_cost[costly])),
        "always_continue_failure": float(timeout.mean()),
    }


def gate_a0() -> dict:
    pooled = summarize(list(iter_dev("F9")) + list(iter_dev("F10")) + list(iter_calibration()))
    horizons = {family: summarize(list(iter_holdout(family)))["always_continue_failure"] for family in ("F13", "F14", "F15", "F16")}
    reserve = pooled["by_tool"]["reserve"]["S"]
    query = pooled["by_tool"]["query"]["S"]
    times = [pooled["c_by_time"][name] for name in ("early", "mid", "late")]
    checks = {
        "corr_g": pooled["corr_g_s"] < -0.3,
        "corr_r": pooled["corr_inv_r_s"] > 0.3,
        "tool": abs(query - reserve) / reserve >= 0.2,
        "time": times[0] < times[1] < times[2],
        "tokens": pooled["mean_s_high_tokens"] < pooled["mean_s_low_tokens"],
        "overlap": 0.2 <= pooled["overlap_fraction"] <= 0.4,
        "positive": pooled["overlap_positive"] >= 0.2,
        "negative": pooled["overlap_negative"] >= 0.2,
        "costly": pooled["costly_value_below_cost"] >= 0.9,
        "info_change": pooled["classes"]["informative"]["change_rate"] >= 0.2,
        "noinfo_change": pooled["classes"]["uninformative"]["change_rate"] <= 0.05,
        "horizon": pooled["always_continue_failure"] < 0.15 and all(value < 0.15 for value in horizons.values()),
    }
    return {"pooled": pooled, "holdout_always_continue_failure": horizons, "checks": checks, "pass": all(checks.values())}
