"""解释 F5/F6 的覆盖失败。不改协议，只重放已冻结的生成器和估计器。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from epochgate.ucr_estimate import fit_models
from epochgate.ucr_protocol import C_FEATURES, FAMILIES, FEATURE_KEYS, S_FEATURES
from epochgate.ucr_workload import iter_family

OUT = Path("results/experiments/ucr/diagnosis.json")


def _stats(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    return {
        "min": float(array.min()),
        "p50": float(np.median(array)),
        "max": float(array.max()),
        "mean": float(array.mean()),
    }


def _scores(model, examples, target: str) -> np.ndarray:
    lower, upper = model.raw(examples)
    values = np.asarray(
        [item.true_remaining_s if target == "S" else item.true_restart_s for item in examples],
        dtype=float,
    )
    return np.maximum(lower - values, values - upper)


def main() -> None:
    groups = {name: list(iter_family(name)) for name in FAMILIES}
    dev = groups["F1"] + groups["F2"]
    cal = groups["F3"] + groups["F4"]
    remaining, restart, _shadow = fit_models(dev, cal)
    train_ranges = {
        key: (min(item.features[key] for item in dev), max(item.features[key] for item in dev))
        for key in FEATURE_KEYS
    }
    report: dict = {"train_support": {key: {"min": lo, "max": hi} for key, (lo, hi) in train_ranges.items()}}
    families = {}
    for name, rows in groups.items():
        features = {}
        outside = {}
        for key in FEATURE_KEYS:
            values = [item.features[key] for item in rows]
            lo, hi = train_ranges[key]
            features[key] = _stats(values)
            outside[key] = {
                "below": int(sum(value < lo - 1e-9 for value in values)),
                "above": int(sum(value > hi + 1e-9 for value in values)),
                "n": len(values),
            }
        s_scores = _scores(remaining, rows, "S")
        c_scores = _scores(restart, rows, "C")
        misses = []
        for item in rows:
            s_lo, s_hi = remaining.interval(item)
            c_lo, c_hi = restart.interval(item)
            if not (s_lo <= item.true_remaining_s <= s_hi) or not (c_lo <= item.true_restart_s <= c_hi):
                misses.append(
                    {
                        "tool": item.features["tool_type"],
                        "fields": item.features["tool_param_fields"],
                        "predicate": item.predicate_now,
                        "declared_timing": FAMILIES[name]["timing"],
                        "s": item.true_remaining_s,
                        "c": item.true_restart_s,
                        "s_interval": [s_lo, s_hi],
                        "c_hit": c_lo <= item.true_restart_s <= c_hi,
                        "s_hit": s_lo <= item.true_remaining_s <= s_hi,
                    }
                )
        generated = np.asarray([item.features["generated_tokens"] for item in rows])
        widths = np.asarray([remaining.interval(item)[1] - remaining.interval(item)[0] for item in rows])
        true_s = np.asarray([item.true_remaining_s for item in rows])
        families[name] = {
            "split": FAMILIES[name]["split"],
            "declared_timing_used_by_generator": False,
            "features": features,
            "outside_train_support": outside,
            "S": _stats([item.true_remaining_s for item in rows]),
            "C": _stats([item.true_restart_s for item in rows]),
            "cqr_S": _stats(s_scores.tolist()) | {"p90": float(np.quantile(s_scores, 0.9))},
            "cqr_C": _stats(c_scores.tolist()) | {"p90": float(np.quantile(c_scores, 0.9))},
            "s_coverage": float(np.mean([row["s_hit"] for row in ({"s_hit": remaining.interval(item)[0] <= item.true_remaining_s <= remaining.interval(item)[1]} for item in rows)])),
            "miss_count": len(misses),
            "miss_by_tool": _count(misses, "tool"),
            "miss_by_predicate": _count(misses, "predicate"),
            "miss_fields": _stats([row["fields"] for row in misses]) if misses else None,
            "corr_generated_tokens_S": float(np.corrcoef(generated, true_s)[0, 1]),
            "corr_generated_tokens_width": float(np.corrcoef(generated, widths)[0, 1]),
            "mean_width_low_tokens": float(widths[generated <= 20].mean()) if np.any(generated <= 20) else None,
            "mean_width_high_tokens": float(widths[generated > 20].mean()) if np.any(generated > 20) else None,
        }
    report["expansion"] = {"S": remaining.expansion, "C": restart.expansion}
    report["s_features_include_generated_tokens"] = "generated_tokens" in S_FEATURES
    report["c_features_include_replan_scale"] = "replan_scale" in C_FEATURES
    report["families"] = families
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"expansion": report["expansion"], "F5_S": families["F5"]["S"], "F5_outside_fields": families["F5"]["outside_train_support"]["tool_param_fields"], "F6_C": families["F6"]["C"], "F5_corr_tokens_S": families["F5"]["corr_generated_tokens_S"]}, ensure_ascii=False))


def _count(rows: list[dict], key: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for row in rows:
        label = str(row[key])
        found[label] = found.get(label, 0) + 1
    return found


if __name__ == "__main__":
    main()
