"""Reconstruct the 40-task metrics independently of decision and Worker code."""

import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean


def score(row):
    job = row["job"]
    seen = set()
    invalid = duplicates = effects = 0
    for receipt in job["receipts"]:
        step = receipt["step"]
        duplicates += step in seen
        seen.add(step)
        payload = json.loads(receipt["payload"])
        encoded = json.dumps([job["id"], step, payload], separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()
        if hashlib.sha256(encoded).hexdigest() != receipt["receipt"]:
            raise ValueError(f"Invalid receipt for {row['id']} / {row['policy']}")
        proof = receipt["commit_evidence"]
        if any((proof["observed"][key] or {}).get("version", 0) != version for key, version in proof["versions"].items()):
            raise ValueError("Version evidence failed")
        if any(proof["observed"][key]["value"] != value for key, value in proof["predicates"].items()):
            raise ValueError("Predicate evidence failed")
        effects += bool(payload)
        allowed = {**row["expected"], "inspection": row["id"].split("-",1)[0]}
        invalid += any(key not in allowed or allowed[key] != value for key, value in payload.items())
    trace = job["decisions"]
    repairs = [event for event in trace if event["kind"] == "repair"]
    preserved = sum(len(set(event["preserved_steps"]) - set(event["committed_steps"])) for event in repairs)
    eligible = sum(len(event["before"]) - len(event["committed_steps"]) for event in repairs)
    good_final = all(row["world"].get(key, {}).get("value") == expected for key, expected in row["expected"].items())
    status = job["status"]
    return {"id": row["id"], "schedule": row["schedule"], "holdout": row["holdout"], "status": status,
            "task_success": status == "completed" and good_final and not (invalid or duplicates),
            "effect_count": effects, "valid_effects": effects - invalid, "invalid_effects": invalid,
            "duplicates": duplicates, "unknown": status == "unknown", "replans": len(repairs),
            "model_tokens": None, "tool_calls": sum(event["kind"] == "dispatch" or event.get("action") == "QUERY_EFFECT"
                                                   for event in trace),
            "latency_seconds": row["elapsed_seconds"], "recovery_seconds": row["recovery_seconds"],
            "preserved": preserved, "eligible_to_preserve": eligible}


def audit(path):
    arms = defaultdict(dict)
    source = gzip.open(path,"rt",encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")
    with source as stream:
        for line in stream:
            row = json.loads(line)
            if row["id"] in arms[row["policy"]]:
                raise ValueError("Duplicate task/policy result")
            arms[row["policy"]][row["id"]] = score(row)
    if len(arms) != 5 or len({frozenset(results) for results in arms.values()}) != 1:
        raise ValueError("Comparison requires a common task universe for all five policies")
    if len(next(iter(arms.values()))) != 40:
        raise ValueError("Expected 40 task instances per arm")
    summary = {}
    for policy, results in arms.items():
        rows = list(results.values())
        total = lambda field: sum(row[field] for row in rows)
        recovery = [row["recovery_seconds"] for row in rows if row["recovery_seconds"] is not None]
        summary[policy] = {"tasks": len(rows), "success": total("task_success"), "unknown": total("unknown"),
                           "cancelled": sum(row["status"] == "cancelled" for row in rows),
                           "valid_effects": total("valid_effects"), "effect_count": total("effect_count"),
                           "invalid_effects": total("invalid_effects"), "duplicates": total("duplicates"),
                           "replans": total("replans"), "model_tokens": None, "tool_calls": total("tool_calls"),
                           "mean_seconds": mean(row["latency_seconds"] for row in rows),
                           "mean_recovery_seconds": mean(recovery) if recovery else None,
                           "preserved": total("preserved"), "eligible_to_preserve": total("eligible_to_preserve"),
                           "details": rows}
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    args.report.write_text(json.dumps(audit(args.source), indent=2) + "\n", encoding="utf-8", newline="\n")
