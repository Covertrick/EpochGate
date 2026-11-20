"""Recompute the preregistered Claim vs Direct paired difference from the raw joint table.

The resampling unit is the proposal-scenario cluster. The interval is a cluster
bootstrap percentile interval, not a Wilson interval. UNKNOWN_AS_ZERO keeps
UNKNOWN and blocked rows in the denominator.

Exit status is 0 when fault_replay/week3_replay_joint.csv has no duplicate run_id.
Exit status is 1 when that file contains duplicate run_id values.
Exit status is 2 when the joint table cannot be read.
"""

from __future__ import annotations

import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
JOINT_PATH = ROOT / "fault_replay" / "week3_replay_joint.csv"

BOOTSTRAP_SEED = 20261120
BOOTSTRAP_DRAWS = 4000
PRIMARY_COMPARISON = "Direct"
PRIMARY_METRIC = "authority"
CO_REPORTED_METRIC = "semantic"


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_joint(path: Path = JOINT_PATH) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def duplicate_run_ids(rows: list[dict[str, str]]) -> list[str]:
    counts = Counter(row.get("run_id", "") for row in rows)
    return sorted(run_id for run_id, count in counts.items() if run_id and count > 1)


def select_main(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Follow the raw in_main_comparison flag. Do not drop UNKNOWN or blocked rows."""
    main = [row for row in rows if as_bool(row.get("in_main_comparison", ""))]
    scenario_rule = [row for row in rows if row.get("scenario_id") not in {"S06", "S09"}]
    main_ids = {row["run_id"] for row in main}
    scenario_ids = {row["run_id"] for row in scenario_rule}
    info = {
        "main_cells": len(main),
        "main_clusters": len({row["cluster_key"] for row in main}),
        "scenario_rule_cells": len(scenario_rule),
        "scenario_rule_clusters": len({row["cluster_key"] for row in scenario_rule}),
        "filter_follows_raw_in_main_comparison": True,
        "raw_flag_matches_s06_s09_exclusion": main_ids == scenario_ids,
        "excluded_scenarios_in_raw_flag": sorted(
            {row["scenario_id"] for row in rows if not as_bool(row.get("in_main_comparison", ""))}
        ),
    }
    return main, info


def is_unknown(row: dict[str, str], metric: str) -> bool:
    if metric == "authority":
        return row.get("authority_decision") == "UNKNOWN"
    if metric == "semantic":
        return row.get("semantic_validity") == "UNKNOWN"
    if metric == "successor":
        return row.get("successor_status") == "UNKNOWN"
    if metric == "outdated":
        return row.get("authority_decision") == "UNKNOWN" or row.get("semantic_validity") == "UNKNOWN"
    raise KeyError(metric)


def base_indicator(row: dict[str, str], metric: str) -> int:
    if metric == "authority":
        return 1 if row.get("authority_decision") == "STALE_ACCEPTED" else 0
    if metric == "semantic":
        return 1 if row.get("semantic_validity") == "INVALID" else 0
    if metric == "successor":
        return 1 if row.get("successor_status") in {"FAILED", "NOT_COMPLETED"} else 0
    if metric == "outdated":
        observed_physical = row.get("physical_effect_state") in {
            "CONFIRMED_EFFECT",
            "NOT_LEGAL",
            "PRESENT_UNVERIFIED",
        }
        restored = row.get("recovery_state") == "RESTORED_BYTES"
        return int(
            row.get("semantic_validity") == "INVALID"
            and row.get("authority_decision") == "NO_STALE_ACCEPTANCE_RECORDED"
            and (observed_physical or restored)
        )
    raise KeyError(metric)


def coded_indicator(row: dict[str, str], metric: str, policy: str) -> int | None:
    unknown = is_unknown(row, metric)
    if policy == "UNKNOWN_EXCLUDED":
        if unknown:
            return None
        return base_indicator(row, metric)
    if policy == "UNKNOWN_AS_FAILURE" and unknown:
        return 1
    if policy not in {"UNKNOWN_AS_ZERO", "UNKNOWN_AS_FAILURE"}:
        raise KeyError(policy)
    return base_indicator(row, metric)


def cluster_pairs(
    rows: list[dict[str, str]],
    metric: str,
    other: str,
    policy: str,
) -> tuple[list[str], list[int], list[tuple[int, int]]]:
    grouped: dict[str, dict[str, int | None]] = defaultdict(dict)
    for row in rows:
        grouped[str(row["cluster_key"])][str(row["path"])] = coded_indicator(row, metric, policy)
    keys: list[str] = []
    diffs: list[int] = []
    pairs: list[tuple[int, int]] = []
    for key in sorted(grouped):
        cells = grouped[key]
        if "Claim" not in cells or other not in cells:
            continue
        left = cells["Claim"]
        right = cells[other]
        if left is None or right is None:
            continue
        keys.append(key)
        diffs.append(int(left) - int(right))
        pairs.append((int(left), int(right)))
    return keys, diffs, pairs


def bootstrap_mean_difference(
    diffs: list[int],
    *,
    seed: int = BOOTSTRAP_SEED,
    draws: int = BOOTSTRAP_DRAWS,
) -> dict[str, Any]:
    n = len(diffs)
    if n == 0:
        return {
            "estimate": None,
            "ci_low": None,
            "ci_high": None,
            "n_clusters": 0,
            "draws": draws,
            "seed": seed,
            "diff_sum": 0,
            "low_index": None,
            "high_index": None,
        }
    point = sum(diffs) / n
    rng = random.Random(seed)
    means: list[float] = []
    for _draw in range(draws):
        total = 0
        for _index in range(n):
            total += diffs[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    low_index = int(0.025 * (draws - 1))
    high_index = int(0.975 * (draws - 1))
    return {
        "estimate": point,
        "ci_low": means[low_index],
        "ci_high": means[high_index],
        "n_clusters": n,
        "draws": draws,
        "seed": seed,
        "diff_sum": sum(diffs),
        "low_index": low_index,
        "high_index": high_index,
    }


def mcnemar_exact(pairs: list[tuple[int, int]]) -> dict[str, Any]:
    claim_only = sum(1 for claim, other in pairs if claim == 1 and other == 0)
    other_only = sum(1 for claim, other in pairs if claim == 0 and other == 1)
    discordant = sum(1 for claim, other in pairs if claim != other)
    if discordant == 0:
        return {"b": claim_only, "c": other_only, "n_discordant": 0, "p_value": None}
    probabilities = [math.comb(discordant, k) / (2**discordant) for k in range(discordant + 1)]
    observed = probabilities[claim_only]
    p_value = sum(item for item in probabilities if item <= observed + 1e-15)
    return {
        "b": claim_only,
        "c": other_only,
        "n_discordant": discordant,
        "p_value": min(1.0, p_value),
    }


def contrast(
    rows: list[dict[str, str]],
    metric: str,
    other: str,
    policy: str,
    *,
    seed: int = BOOTSTRAP_SEED,
    draws: int = BOOTSTRAP_DRAWS,
) -> dict[str, Any]:
    _keys, diffs, pairs = cluster_pairs(rows, metric, other, policy)
    estimate = bootstrap_mean_difference(diffs, seed=seed, draws=draws)
    supplementary = mcnemar_exact(pairs)
    return {
        "comparison": f"Claim_vs_{other}",
        "metric": metric,
        "unknown_policy": policy,
        **estimate,
        "mcnemar": supplementary,
    }


def build_report(rows: list[dict[str, str]]) -> dict[str, Any]:
    duplicates = duplicate_run_ids(rows)
    main, filter_info = select_main(rows)
    report: dict[str, Any] = {
        "source": str(JOINT_PATH),
        "n_rows": len(rows),
        "duplicate_run_ids": duplicates,
        "filter": filter_info,
        "seed": BOOTSTRAP_SEED,
        "draws": BOOTSTRAP_DRAWS,
        "interval": "cluster_bootstrap_percentile",
        "not_wilson": True,
        "unknown_policy": "UNKNOWN_AS_ZERO",
        "week5_estimates": "NOT_COMPUTED",
    }
    if duplicates:
        report["primary"] = None
        return report
    report["primary"] = contrast(main, PRIMARY_METRIC, PRIMARY_COMPARISON, "UNKNOWN_AS_ZERO")
    report["co_reported_semantic"] = contrast(main, CO_REPORTED_METRIC, PRIMARY_COMPARISON, "UNKNOWN_AS_ZERO")
    return report


def main() -> int:
    if not JOINT_PATH.is_file():
        print(f"missing joint table: {JOINT_PATH}", file=sys.stderr)
        return 2
    rows = load_joint()
    report = build_report(rows)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if report["duplicate_run_ids"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
