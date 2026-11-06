"""在开发集拟合，在校准集扩区间，只在 F5–F8 上评估一次。"""

from __future__ import annotations

import json
import random
from pathlib import Path

from epochgate.ucr_estimate import coverage, fit_models
from epochgate.ucr_protocol import BOOTSTRAP_REPLICATES, BOOTSTRAP_SEED, FAMILIES, PRIMARY_WIDTH, WIDTH_SCALES
from epochgate.ucr_replay import fit_value_table, play
from epochgate.ucr_workload import iter_family

OUT = Path("results/experiments/ucr")
POLICIES = (
    "always-continue",
    "always-abort",
    "predicate-only",
    "point",
    "interval",
    "full",
    "cost-oracle",
)
TEST_FAMILIES = ("F5", "F6", "F7", "F8")


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _bootstrap(diffs: list[float]) -> dict[str, float]:
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(diffs)
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(BOOTSTRAP_REPLICATES))
    return {
        "n": n,
        "mean": sum(diffs) / n,
        "p2_5": means[int(0.025 * (len(means) - 1))],
        "p97_5": means[int(0.975 * (len(means) - 1))],
    }


def main() -> None:
    dev = [item for family in ("F1", "F2") for item in iter_family(family)]
    cal = [item for family in ("F3", "F4") for item in iter_family(family)]
    remaining, restart, shadow = fit_models(dev, cal)
    services = [max(item.true_remaining_s, item.true_restart_s) for item in dev]
    slack = 1.5 * _percentile([item.true_remaining_s for item in dev], 0.9)
    failure_penalty = 10.0 * max(services)
    value_table = fit_value_table(cal, remaining, restart, shadow, slack)
    (OUT / "fit.json").write_text(
        json.dumps(
            {
                "dev": len(dev),
                "cal": len(cal),
                "slack_s": slack,
                "failure_penalty_M": failure_penalty,
                "s_expansion": remaining.expansion,
                "c_expansion": restart.expansion,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    rows: list[dict] = []
    for family in TEST_FAMILIES:
        examples = list(iter_family(family))
        for width in WIDTH_SCALES:
            bundle = {
                policy: [play(item, policy=policy, remaining=remaining, restart=restart, shadow=shadow, value_table=value_table, slack_s=slack, width_scale=width) for item in examples]
                for policy in POLICIES
            }
            for index, example in enumerate(examples):
                record = {
                    "family": family,
                    "seed": example.seed,
                    "width_scale": width,
                    "generated_tokens": example.features["generated_tokens"],
                    "predicate_now": example.predicate_now,
                }
                for policy, scores in bundle.items():
                    score = scores[index]
                    record[policy] = {
                        "action": score.action,
                        "probed": score.probed,
                        "action_changed": score.action_changed,
                        "cost": score.cost,
                        "failed": score.failed,
                        "covered": score.covered,
                        "separated": score.separated,
                        "overlap": score.overlap,
                    }
                s_lo, s_hi = remaining.interval(example, width)
                c_lo, c_hi = restart.interval(example, width)
                record["s_width"] = s_hi - s_lo
                record["c_width"] = c_hi - c_lo
                record["s_hit"] = s_lo <= example.true_remaining_s <= s_hi
                record["c_hit"] = c_lo <= example.true_restart_s <= c_hi
                rows.append(record)
            print(f"{family} width {width}", flush=True)

    (OUT / "scores.json").write_text(json.dumps(rows), encoding="utf-8")
    summary = _summarize(rows, dev, cal, remaining, restart)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("gate_A", "gate_B", "gate_D_partial")}, ensure_ascii=False))


def _summarize(rows: list[dict], dev, cal, remaining, restart) -> dict:
    primary = [row for row in rows if row["width_scale"] == PRIMARY_WIDTH]
    by_family: dict[str, dict] = {}
    regret_hits = []
    probe_hits = []
    for family in TEST_FAMILIES:
        group = [row for row in primary if row["family"] == family]
        n = len(group)
        s_cover = sum(row["s_hit"] for row in group) / n
        c_cover = sum(row["c_hit"] for row in group) / n
        full_regret = [row["full"]["cost"] - row["cost-oracle"]["cost"] for row in group]
        point_regret = [row["point"]["cost"] - row["cost-oracle"]["cost"] for row in group]
        interval_gap = [row["interval"]["cost"] - row["full"]["cost"] for row in group]
        point_gap = [row["point"]["cost"] - row["full"]["cost"] for row in group]
        continue_fail = sum(row["always-continue"]["failed"] for row in group) / n
        full_fail = sum(row["full"]["failed"] for row in group) / n
        probed = [row for row in group if row["full"]["probed"]]
        overlap_rows = [row for row in group if row["full"]["overlap"] or row["interval"]["overlap"]]
        forced_abort = sum(
            1
            for row in group
            if row["full"]["overlap"] and row["full"]["action"] == "abort-and-replan" and not row["full"]["probed"]
        )
        by_family[family] = {
            "n": n,
            "s_coverage": s_cover,
            "c_coverage": c_cover,
            "full_regret": _bootstrap(full_regret),
            "point_regret": _bootstrap(point_regret),
            "full_minus_point": _bootstrap([-item for item in point_gap]),
            "point_minus_full": _bootstrap(point_gap),
            "interval_minus_full": _bootstrap(interval_gap),
            "probe_rate": len(probed) / n,
            "probe_action_change_rate": (sum(row["full"]["action_changed"] for row in probed) / len(probed)) if probed else 0.0,
            "mean_probe_cost": (sum(row["full"]["cost"] for row in probed) / len(probed)) if probed else 0.0,
            "always_continue_failure": continue_fail,
            "full_failure": full_fail,
            "failure_constraint": full_fail <= continue_fail + 0.02,
            "overlap_forced_abort": forced_abort,
            "guarantee_violations": sum(
                1
                for row in group
                if row["s_hit"] and row["c_hit"] and row["full"]["separated"] and not row["full"]["probed"]
                and row["full"]["action"] != row["cost-oracle"]["action"]
            ),
        }
        if by_family[family]["point_minus_full"]["p2_5"] > 0:
            regret_hits.append(family)
        if by_family[family]["interval_minus_full"]["p2_5"] > 0:
            probe_hits.append(family)

    low = [row["s_width"] for row in primary if row["generated_tokens"] <= 20]
    high = [row["s_width"] for row in primary if row["generated_tokens"] > 20]
    wide = [row for row in rows if row["width_scale"] == 2.0]
    narrow = [row for row in rows if row["width_scale"] == 0.5]
    gate_a = all(by_family[family]["s_coverage"] >= 0.9 and by_family[family]["c_coverage"] >= 0.9 for family in TEST_FAMILIES)
    gate_a = gate_a and (sum(high) / len(high) < sum(low) / len(low) if low and high else False)
    return {
        "families": list(FAMILIES),
        "dev_coverage_s": coverage(remaining, dev, "S"),
        "cal_coverage_s": coverage(remaining, cal, "S"),
        "by_family": by_family,
        "width_low_tokens": sum(low) / len(low) if low else None,
        "width_high_tokens": sum(high) / len(high) if high else None,
        "probe_rate_narrow": sum(row["full"]["probed"] for row in narrow) / len(narrow),
        "probe_rate_wide": sum(row["full"]["probed"] for row in wide) / len(wide),
        "regret_families": regret_hits,
        "probe_families": probe_hits,
        "gate_A": gate_a,
        "gate_B": len(regret_hits) >= 1 and all(item["overlap_forced_abort"] == 0 for item in by_family.values()),
        "gate_D_partial": {
            "two_families": len(regret_hits) >= 2,
            "probe_family": len(probe_hits) >= 1,
            "failure_constraints": all(item["failure_constraint"] for item in by_family.values()),
        },
    }


if __name__ == "__main__":
    main()
