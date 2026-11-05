"""重跑修正臂映射后的策略矩阵，并写出配对 bootstrap。

原始 JSONL 超过 GitHub 单文件限制，只留在本机。提交的是 summary、comparisons 和 audit。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from epochgate.followup_matrix import (
    ERRORS,
    LOADS,
    N_TRACES,
    POLICIES,
    PROTOCOL,
    RHOS,
    SCHEDULERS,
    SEEDS,
    SIZES,
    SLOTS,
    TIMINGS,
    TRIGGERS,
    beats,
    bootstrap_mean,
    run_index,
    unravel,
)

OUT = Path("results/experiments/followup")
POLICY_INDEX = {name: index for index, name in enumerate(POLICIES)}


def _locate(trigger: str, timing: str, load: str, slots: int, rho: float, error: float, scheduler: str, seed: int) -> int:
    coords = (
        TRIGGERS.index(trigger),
        TIMINGS.index(timing),
        [name for name, _ in LOADS].index(load),
        SLOTS.index(slots),
        RHOS.index(rho),
        ERRORS.index(error),
        SCHEDULERS.index(scheduler),
        SEEDS.index(seed),
    )
    index = 0
    for coord, size in zip(coords, SIZES, strict=True):
        index = index * size + coord
    return index


def _paired(w95: list[list[float]], indexes: list[int], baseline: str, challenger: str) -> list[float]:
    left = POLICY_INDEX[baseline]
    right = POLICY_INDEX[challenger]
    return [w95[left][i] - w95[right][i] for i in indexes]


def _contrast(w95: list[list[float]], indexes: list[int], baseline: str, challenger: str, seed: int) -> dict:
    diffs = _paired(w95, indexes, baseline, challenger)
    interval = bootstrap_mean(diffs, seed=seed)
    baseline_mean = sum(w95[POLICY_INDEX[baseline]][i] for i in indexes) / len(indexes)
    interval["baseline"] = baseline
    interval["challenger"] = challenger
    interval["baseline_mean"] = baseline_mean
    interval["relative"] = None if baseline_mean == 0 else interval["mean"] / baseline_mean
    interval["beats"] = beats(interval)
    return interval


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=N_TRACES)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = parser.parse_args()
    n = min(args.limit, N_TRACES)
    OUT.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUT / "matrix.jsonl"
    w95 = [[0.0] * n for _ in POLICIES]
    release = [[0.0] * n for _ in POLICIES]
    restart = [[0.0] * n for _ in POLICIES]
    completion = [[0.0] * n for _ in POLICIES]
    old_commits = 0
    duplicates = 0
    completion_min = 1.0
    action_counts: dict[str, int] = {}
    outside_band = 0
    outside_match = 0
    inside_band = 0
    defer_switched = 0
    digest = hashlib.sha256()

    def consume(index: int, rows: list[tuple], handle) -> None:
        nonlocal old_commits, duplicates, completion_min, outside_band, outside_match, inside_band, defer_switched
        trigger_i, timing_i, load_i, slots_i, rho_i, error_i, scheduler_i, seed_i = unravel(index)
        context = {
            "trigger_class": TRIGGERS[trigger_i],
            "timing": TIMINGS[timing_i],
            "load": LOADS[load_i][0],
            "slots": SLOTS[slots_i],
            "rho": RHOS[rho_i],
            "estimate_error": ERRORS[error_i],
            "scheduler": SCHEDULERS[scheduler_i],
            "seed": SEEDS[seed_i],
        }
        by_policy = {}
        for row in rows:
            policy, arm, action, row_w95, row_w99, row_release, row_completion, old, dup, row_restart, reclaimed = row
            slot = POLICY_INDEX[policy]
            w95[slot][index] = row_w95
            release[slot][index] = row_release
            restart[slot][index] = row_restart
            completion[slot][index] = row_completion
            completion_min = min(completion_min, row_completion)
            old_commits += old
            duplicates += dup
            action_counts[f"{policy}:{action}"] = action_counts.get(f"{policy}:{action}", 0) + 1
            if action == "continue/defer" and arm == "G1":
                defer_switched += 1
            by_policy[policy] = action
            payload = {
                **context,
                "strategy": policy,
                "replay_arm": arm,
                "decision_action": action,
                "W95": row_w95,
                "W99": row_w99,
                "focal_release_at": row_release,
                "bystander_completion_rate": row_completion,
                "old_attempt_committed": old,
                "duplicate_side_effects": dup,
                "restart_service_s": row_restart,
                "reclaimed_slot_s": reclaimed,
                "protocol": PROTOCOL,
            }
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            handle.write(line)
            digest.update(line.encode())
        oracle_action = by_policy["Oracle"]
        uap_action = by_policy["UncertaintyAwarePolicy"]
        if uap_action == "continue/defer":
            inside_band += 1
        else:
            outside_band += 1
            outside_match += int(uap_action == oracle_action)

    with jsonl_path.open("w", encoding="utf-8") as handle:
        if args.workers == 1 or n < 8:
            for index in range(n):
                got, rows = run_index(index)
                consume(got, rows, handle)
                if (index + 1) % 500 == 0:
                    print(f"replay {index + 1}/{n}", flush=True)
        else:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                for done, (got, rows) in enumerate(pool.map(run_index, range(n), chunksize=20), start=1):
                    consume(got, rows, handle)
                    if done % 2000 == 0 or done == n:
                        print(f"replay {done}/{n}", flush=True)

    cells = []
    if n == N_TRACES:
        seeds_per_cell = len(SEEDS)
        n_cells = n // seeds_per_cell
        for cell in range(n_cells):
            start = cell * seeds_per_cell
            trigger_i, timing_i, load_i, slots_i, rho_i, error_i, scheduler_i, _seed_i = unravel(start)
            for policy in POLICIES:
                slot = POLICY_INDEX[policy]
                block = w95[slot][start : start + seeds_per_cell]
                releases = release[slot][start : start + seeds_per_cell]
                cells.append(
                    {
                        "trigger": TRIGGERS[trigger_i],
                        "timing": TIMINGS[timing_i],
                        "load": LOADS[load_i][0],
                        "slots": SLOTS[slots_i],
                        "rho": RHOS[rho_i],
                        "estimate_error": ERRORS[error_i],
                        "scheduler": SCHEDULERS[scheduler_i],
                        "strategy": policy,
                        "n": seeds_per_cell,
                        "W95_mean": sum(block) / seeds_per_cell,
                        "focal_release_mean": sum(releases) / seeds_per_cell,
                        "completion_rate_mean": sum(completion[slot][start : start + seeds_per_cell]) / seeds_per_cell,
                    }
                )

    contrasts = []
    gate_loads: dict[str, list[str]] = {}
    if n == N_TRACES:
        groups: dict[tuple, list[int]] = {}
        for index in range(n):
            trigger_i, _timing_i, load_i, _slots_i, rho_i, _error_i, _scheduler_i, _seed_i = unravel(index)
            key = (TRIGGERS[trigger_i], LOADS[load_i][0], RHOS[rho_i])
            groups.setdefault(key, []).append(index)
        serial = 0
        for (trigger, load, rho), indexes in sorted(groups.items()):
            for baseline in ("AlwaysContinue", "AlwaysAbort"):
                item = _contrast(w95, indexes, baseline, "UncertaintyAwarePolicy", seed=1000 + serial)
                item.update({"trigger": trigger, "load": load, "rho": rho})
                contrasts.append(item)
                if item["beats"]:
                    gate_loads.setdefault(f"{trigger}|{baseline}", []).append(f"{load}|rho={rho}")
                serial += 1

    probe_index = _locate("harmful", "mid", "high", 4, 1.0, 0.25, "fcfs", 5000)
    probe = None
    if probe_index < n:
        probe = {
            policy: {
                "W95": w95[POLICY_INDEX[policy]][probe_index],
                "focal_release_at": release[POLICY_INDEX[policy]][probe_index],
                "restart_service_s": restart[POLICY_INDEX[policy]][probe_index],
            }
            for policy in POLICIES
        }

    agreement = None if outside_band == 0 else outside_match / outside_band
    two_load_hits = {key: sorted(set(values)) for key, values in gate_loads.items() if len(set(load.split("|", 1)[0] for load in values)) >= 2}
    complete = n == N_TRACES
    gate_2 = bool(
        complete
        and agreement == 1.0
        and inside_band > 0
        and defer_switched == 0
        and old_commits == 0
        and duplicates == 0
        and two_load_hits
    )
    audit = {
        "protocol": PROTOCOL,
        "rows": n * len(POLICIES),
        "traces": n,
        "complete": complete,
        "sha256_jsonl": digest.hexdigest(),
        "old_attempt_committed": old_commits,
        "duplicate_side_effects": duplicates,
        "completion_rate_min": completion_min,
        "oracle_agreement_outside_band": agreement,
        "outside_band_traces": outside_band,
        "inside_band_traces": inside_band,
        "defer_replayed_as_abort": defer_switched,
        "action_counts": action_counts,
        "probe_harmful_mid_high_slots4_rho1_error025_fcfs_seed5000": probe,
        "gate_2_replay": "PASS" if gate_2 else "FAIL",
        "gate_2_two_load_hits": two_load_hits,
        "predicate_truth": "synthetic harmful/harmless workload label",
    }
    (OUT / "summary.json").write_text(
        json.dumps({"protocol": PROTOCOL, "rows": n * len(POLICIES), "cells": len(cells), "summary": cells}, ensure_ascii=False),
        encoding="utf-8",
    )
    (OUT / "comparisons.json").write_text(json.dumps(contrasts, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: audit[k] for k in ("rows", "gate_2_replay", "oracle_agreement_outside_band", "inside_band_traces", "gate_2_two_load_hits")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
