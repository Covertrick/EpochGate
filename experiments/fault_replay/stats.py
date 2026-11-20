"""Pre-registered paired contrasts. Cells are not the resampling unit."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Callable

from fault_replay.contract import BOOTSTRAP_DRAWS, BOOTSTRAP_SEED


def _cluster_pairs(rows: list[dict[str, Any]], indicator: Callable[[dict[str, Any]], int], other: str) -> tuple[list[int], list[tuple[int, int]]]:
    grouped: dict[str, dict[str, int]] = defaultdict(dict)
    for row in rows:
        grouped[str(row["cluster_key"])][str(row["path"])] = indicator(row)
    diffs: list[int] = []
    pairs: list[tuple[int, int]] = []
    for key in sorted(grouped):
        cells = grouped[key]
        if "Claim" not in cells or other not in cells:
            continue
        diffs.append(cells["Claim"] - cells[other])
        pairs.append((cells["Claim"], cells[other]))
    return diffs, pairs


def bootstrap_mean_difference(diffs: list[int], *, seed: int = BOOTSTRAP_SEED, draws: int = BOOTSTRAP_DRAWS) -> dict[str, Any]:
    n = len(diffs)
    if n == 0:
        return {"estimate": None, "ci_low": None, "ci_high": None, "n_clusters": 0, "draws": draws, "seed": seed}
    point = sum(diffs) / n
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
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
    }


def mcnemar_exact(pairs: list[tuple[int, int]]) -> dict[str, Any]:
    claim_only = sum(1 for claim, other in pairs if claim == 1 and other == 0)
    other_only = sum(1 for claim, other in pairs if claim == 0 and other == 1)
    discordant = claim_only + other_only
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


def indicator_authority(row: dict[str, Any]) -> int:
    return 1 if row.get("authority_decision") == "STALE_ACCEPTED" else 0


def indicator_semantic(row: dict[str, Any]) -> int:
    return 1 if row.get("semantic_validity") == "INVALID" else 0


def indicator_successor(row: dict[str, Any]) -> int:
    return 1 if row.get("successor_status") in {"FAILED", "NOT_COMPLETED"} else 0


def indicator_outdated(row: dict[str, Any]) -> int:
    observed_physical = row.get("physical_effect_state") in {"CONFIRMED_EFFECT", "NOT_LEGAL", "PRESENT_UNVERIFIED"}
    restored = row.get("recovery_state") == "RESTORED_BYTES"
    return int(
        row.get("semantic_validity") == "INVALID"
        and row.get("authority_decision") == "NO_STALE_ACCEPTANCE_RECORDED"
        and (observed_physical or restored)
    )


INDICATORS = (
    ("authority", indicator_authority),
    ("semantic", indicator_semantic),
    ("successor", indicator_successor),
    ("outdated", indicator_outdated),
)

COMPARISONS = ("Direct", "Admission", "Outbox")


def pre_registered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for other in COMPARISONS:
        for name, indicator in INDICATORS:
            diffs, pairs = _cluster_pairs(rows, indicator, other)
            estimate = bootstrap_mean_difference(diffs)
            supplementary = mcnemar_exact(pairs)
            results.append(
                {
                    "comparison": f"Claim_vs_{other}",
                    "indicator": name,
                    "role": "pre_registered",
                    **estimate,
                    "mcnemar": supplementary,
                }
            )
    return results
