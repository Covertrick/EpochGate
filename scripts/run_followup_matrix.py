"""Run the post-4.0 policy matrix and write auditable summary rows.

The predicate truth is controlled by the synthetic workload. Cost estimates are
deterministically perturbed to study the uncertainty band; this is a replay
study, not a claim that a model has solved semantic predicate checking.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from epochgate.decision_policy import DecisionInput, RuntimeAction, decide
from epochgate.replay import replay
from epochgate.workload import build_workload

OUT = Path("results/experiments/followup")
POLICIES = ("AlwaysContinue", "AlwaysAbort", "PredicateOnly", "Oracle", "UncertaintyAwarePolicy")
SCHEDULERS = ("fcfs", "runtime-plas", "srpt")
TRIGGERS = ("harmful", "harmless")
TIMINGS = ("early", "mid", "late")
LOADS = {"low": 20.0, "medium": 1.9573, "high": 1.7824}
SLOTS = (2, 4, 8)
RHOS = (0.5, 0.75, 1.0, 1.25, 2.0)
ERRORS = (0.0, 0.10, 0.25)
SEEDS = range(5000, 5050)


def _policy_arm(policy: str, trigger: str, rho: float, remaining: float, error: float) -> tuple[str, str]:
    valid = trigger == "harmless"
    if policy == "AlwaysContinue":
        return "G2", "continue"
    if policy == "AlwaysAbort":
        return "G1", "abort-and-replan"
    if policy == "PredicateOnly":
        return ("G1", "abort-and-replan") if not valid else ("G2", "continue")
    if policy == "Oracle":
        if not valid or rho < 1.0:
            return "G1", "abort-and-replan"
        return "G2", "continue"
    result = decide(
        DecisionInput(
            predicate_valid=valid,
            candidate_formed=False,
            remaining_service_estimate=remaining * (1.0 + error),
            restart_service_estimate=(rho * remaining) * (1.0 - error),
            remaining_service_error=error * remaining,
            restart_service_error=error * rho * remaining,
            safety_buffer=0.05 * remaining,
        )
    )
    if result.action is RuntimeAction.ABORT_AND_REPLAN:
        return "G1", result.action.value
    return "G2", result.action.value


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    jsonl = OUT / "matrix.jsonl"
    summary = OUT / "summary.json"
    rows: list[dict] = []
    with jsonl.open("w", encoding="utf-8") as handle:
        for trigger in TRIGGERS:
            for timing in TIMINGS:
                for load, interarrival in LOADS.items():
                    for slots in SLOTS:
                        for rho in RHOS:
                            for error in ERRORS:
                                for policy in POLICIES:
                                    for scheduler in SCHEDULERS:
                                        for seed in SEEDS:
                                            workload = build_workload(
                                                seed=seed, trigger_class=trigger, timing=timing,
                                                rho=rho, load=load, interarrival_s=interarrival,
                                                slots=slots,
                                            )
                                            arm, action = _policy_arm(
                                                policy, trigger, rho, workload.remaining_at_event_s, error
                                            )
                                            result = replay(
                                                workload, policy=arm, scheduler=scheduler,
                                                phase="followup", protocol_hash="followup-0-5",
                                            )
                                            row = {
                                                **result.summary,
                                                "strategy": policy,
                                                "estimate_error": error,
                                                "decision_action": action,
                                                "replay_arm": arm,
                                            }
                                            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                                            rows.append(row)
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = tuple(row[k] for k in ("trigger_class", "timing", "load", "slots", "rho", "estimate_error", "strategy", "scheduler"))
        groups.setdefault(key, []).append(row)
    cells = []
    for key, items in groups.items():
        cells.append({
            "trigger": key[0], "timing": key[1], "load": key[2], "slots": key[3],
            "rho": key[4], "estimate_error": key[5], "strategy": key[6], "scheduler": key[7],
            "n": len(items),
            "W95_mean": sum(float(x["W95"]) for x in items) / len(items),
            "completion_rate_mean": sum(float(x["bystander_completion_rate"]) for x in items) / len(items),
            "old_attempt_committed": sum(int(x["old_attempt_committed"]) for x in items),
            "duplicate_side_effects": sum(int(x["duplicate_side_effects"]) for x in items),
        })
    payload = {
        "protocol": "followup-0-5",
        "rows": len(rows),
        "cells": len(cells),
        "traces_per_cell": 50,
        "sha256_jsonl": hashlib.sha256(jsonl.read_bytes()).hexdigest(),
        "summary": cells,
    }
    summary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"rows": len(rows), "cells": len(cells), "jsonl": str(jsonl), "summary": str(summary)}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
