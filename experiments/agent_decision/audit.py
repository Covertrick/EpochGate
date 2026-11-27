"""Independent scorer for frozen receiver exports. Does not import the runner or policy."""

import argparse
import hashlib
import json
from pathlib import Path


def score(row):
    if "error" in row:
        return {"success": False, "error": row["error"]}
    job = row["job"]
    seen = set()
    duplicates = 0
    invalid = 0
    expected = row["case"]["expected"]
    for receipt in job["receipts"]:
        key = receipt["step"]
        duplicates += key in seen
        seen.add(key)
        payload = json.loads(receipt["payload"])
        encoded = json.dumps([job["id"], key, payload], separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()
        assert hashlib.sha256(encoded).hexdigest() == receipt["receipt"]
        proof = receipt["commit_evidence"]
        for key, version in proof["versions"].items():
            assert (proof["observed"][key] or {}).get("version", 0) == version
        for key, value in proof["predicates"].items():
            assert proof["observed"][key]["value"] == value
        invalid += any(name in payload and payload[name] != value for name, value in expected.items())
    trace = job["decisions"]
    matched = all(row["world"].get(key, {}).get("value") == value for key, value in expected.items())
    return {"success": job["status"] == "completed" and matched and not invalid and not duplicates,
            "invalid_effects": invalid, "duplicates": duplicates, "unknown": job["status"] == "unknown",
            "replans": sum(item.get("kind") == "repair" for item in trace),
            "preserved_steps": sum(len(item["preserved_steps"]) for item in trace if item.get("kind") == "repair"),
            "queries": sum(item.get("action") == "QUERY_EFFECT" for item in trace),
            "recovery_query_seconds": sum(item.get("query_seconds", 0) for item in trace),
            "tool_calls": sum(item.get("kind") == "dispatch" or item.get("action") == "QUERY_EFFECT" for item in trace),
            "tokens": job["task"].get("planner", {}).get("tokens"),
            "seconds": row["elapsed_seconds"]}


def audit(source):
    groups = {}
    seen_instances = set()
    for row in json.loads(source.read_bytes()):
        arm = row["arm"]
        case = row["case"]["id"]
        assert (arm, case) not in seen_instances
        seen_instances.add((arm, case))
        groups.setdefault(arm, []).append({"case": case, "holdout": row["case"]["holdout"], **score(row)})
    return {arm: {"tasks": len(rows), "success": sum(item["success"] for item in rows),
                  "unknown": sum(item.get("unknown", False) for item in rows),
                  "invalid_effects": sum(item.get("invalid_effects", 0) for item in rows),
                  "duplicates": sum(item.get("duplicates", 0) for item in rows), "details": rows}
            for arm, rows in groups.items()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.source), indent=2))
