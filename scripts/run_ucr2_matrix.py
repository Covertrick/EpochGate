"""F13–F16 的插值评估。没有锁文件就停止。Gate B 失败时不读压力测试。"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np

from epochgate.ucr2_protocol import BOOTSTRAP_REPLICATES, BOOTSTRAP_SEED, FAILURE_SLACK, HOLDOUT_FAMILIES
from epochgate.ucr2_replay import play
from epochgate.ucr2_workload import iter_holdout, iter_stress, time_bin

OUT = Path("results/experiments/ucr2")
POLICIES = (
    "always-continue",
    "always-abort",
    "predicate-only",
    "expected-value",
    "cost-oracle",
    "point",
    "interval",
    "full",
    "noprobe",
)


def wilson_lower(hits: int, n: int) -> float:
    if n == 0:
        return 0.0
    z = 1.96
    p = hits / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5
    return float((center - margin) / denom)


def bootstrap_lower(values: np.ndarray) -> float:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    means = np.empty(BOOTSTRAP_REPLICATES)
    n = len(values)
    for index in range(BOOTSTRAP_REPLICATES):
        means[index] = values[rng.integers(0, n, n)].mean()
    return float(np.quantile(means, 0.025))


def _score_family(rows: list, bundle: dict, penalty: float) -> dict:
    tables = {}
    for policy in POLICIES:
        scores = [
            play(
                row, policy="interval" if policy == "noprobe" else policy,
                remaining=bundle["remaining"], restart=bundle["restart"],
                value_table=bundle["value_table"], failure_table=bundle["failure_table"],
            )
            for row in rows
        ]
        tables[policy] = scores
    oracle = np.asarray([row.cost for row in tables["cost-oracle"]])
    summary = {}
    for policy, scores in tables.items():
        cost = np.asarray([row.cost for row in scores])
        failed = np.asarray([row.failed for row in scores], dtype=float)
        regret = cost - oracle
        summary[policy] = {
            "regret_mean": float(regret.mean()),
            "regret_lower": bootstrap_lower(regret),
            "jm_mean": float((cost + penalty * failed).mean()),
            "failure_rate": float(failed.mean()),
            "probe_rate": float(np.mean([row.probed for row in scores])),
            "action_change_rate": float(np.mean([row.action_changed for row in scores if row.probed])) if any(row.probed for row in scores) else 0.0,
            "probe_outside_overlap": int(sum(row.probed and not row.overlap for row in scores)),
            "selective_violations": int(sum(row.selective_violation for row in scores)),
            "probe_bound_violations": int(sum(row.probe_bound_violation for row in scores)),
        }
    s_hit = np.asarray([row.covered_s for row in tables["full"]])
    c_hit = np.asarray([row.covered_c for row in tables["full"]])
    both = s_hit & c_hit
    low = np.asarray([row.features["generated_tokens"] <= 20 for row in rows])
    high = ~low
    s_width = np.asarray([row.s_width for row in tables["full"]])
    slices = {}
    for name, mask in (
        ("tool", np.asarray([row.features["tool_type"] for row in rows])),
        ("time", np.asarray([time_bin(row.features["predicate_time"]) for row in rows])),
        ("rate", np.asarray([row.nominal_rate for row in rows])),
    ):
        slices[name] = {}
        for level in sorted(set(mask.tolist())):
            chosen = mask == level
            slices[name][str(level)] = {
                "n": int(chosen.sum()),
                "s": float(s_hit[chosen].mean()) if chosen.any() else None,
                "c": float(c_hit[chosen].mean()) if chosen.any() else None,
            }
    point_gap = tables["point"] and (
        np.asarray([row.cost for row in tables["point"]]) - np.asarray([row.cost for row in tables["full"]])
    )
    expected_gap = np.asarray([row.cost for row in tables["expected-value"]]) - np.asarray([row.cost for row in tables["full"]])
    probe_gap = np.asarray([row.cost for row in tables["noprobe"]]) - np.asarray([row.cost for row in tables["full"]])
    return {
        "n": len(rows),
        "s_coverage": float(s_hit.mean()),
        "c_coverage": float(c_hit.mean()),
        "s_wilson_lower": wilson_lower(int(s_hit.sum()), len(rows)),
        "c_wilson_lower": wilson_lower(int(c_hit.sum()), len(rows)),
        "s_misses": int((~s_hit).sum()),
        "c_misses": int((~c_hit).sum()),
        "both_coverage": float(both.mean()),
        "width_low_tokens": float(s_width[low].mean()) if low.any() else None,
        "width_high_tokens": float(s_width[high].mean()) if high.any() else None,
        "slices": slices,
        "policies": summary,
        "full_minus_point_lower": bootstrap_lower(point_gap),
        "full_minus_expected_lower": bootstrap_lower(expected_gap),
        "noprobe_minus_full_lower": bootstrap_lower(probe_gap),
    }


def main() -> None:
    lock_path = OUT / "estimator_lock.json"
    if not lock_path.exists():
        raise SystemExit("missing estimator lock; refuse to open holdout")
    bundle = joblib.load(OUT / "model.joblib")
    penalty = float(bundle["lock"]["M"])
    families = {name: _score_family(list(iter_holdout(name)), bundle, penalty) for name in HOLDOUT_FAMILIES}
    gate_a = all(
        row["s_coverage"] >= 0.9 and row["c_coverage"] >= 0.9 and row["width_high_tokens"] < row["width_low_tokens"]
        for row in families.values()
    )
    better = [
        name for name, row in families.items()
        if row["full_minus_point_lower"] > 0 and row["full_minus_expected_lower"] > 0
    ]
    probe_helps = [
        name for name, row in families.items()
        if row["noprobe_minus_full_lower"] > 0 and row["policies"]["full"]["probe_rate"] > 0
    ]
    failure_ok = all(
        row["policies"]["full"]["failure_rate"] <= row["policies"]["always-continue"]["failure_rate"] + FAILURE_SLACK
        for row in families.values()
    )
    probe_clean = all(row["policies"]["full"]["probe_outside_overlap"] == 0 for row in families.values())
    changed = any(row["policies"]["full"]["action_change_rate"] > 0 for row in families.values())
    selective_ok = all(row["policies"]["full"]["selective_violations"] == 0 for row in families.values())
    bound_ok = all(row["policies"]["full"]["probe_bound_violations"] == 0 for row in families.values())
    gate_b = len(better) >= 2 and len(probe_helps) >= 1 and failure_ok and probe_clean and changed and selective_ok and bound_ok
    summary = {
        "gate_a": gate_a,
        "gate_b": gate_b,
        "better_families": better,
        "probe_helps": probe_helps,
        "failure_ok": failure_ok,
        "selective_ok": selective_ok,
        "families": families,
        "stress": None,
    }
    if gate_b:
        summary["stress"] = {
            name: _score_family(list(iter_stress(name)), bundle, penalty)
            for name in ("F17", "F18", "F19", "F20")
        }
        summary["stress_claim"] = "no conformal guarantee"
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"gate_a": gate_a, "gate_b": gate_b, "better": better, "probe_helps": probe_helps}, ensure_ascii=False))


if __name__ == "__main__":
    main()
