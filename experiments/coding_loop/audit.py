"""Verify the coding trace, distinct file effects and tested artifact."""

import argparse
import hashlib
import json
from pathlib import Path
import re


def audit(directory):
    state = json.loads((directory / "trace.json").read_bytes())
    trace = state["events"]
    writes = [row for row in trace if row["action"] == "write" and row.get("result") == "VERIFIED"]
    receipts = [row["sha256"] for row in writes]
    tests = [row for row in trace if row["action"] == "test_result"]
    source_sha256 = hashlib.sha256((directory / "shipping.py").read_bytes()).hexdigest()
    change_at = next(index for index,row in enumerate(trace) if row["action"] == "world_change")
    repair_at = next(index for index,row in enumerate(trace) if row["action"] == "repair" and row.get("invalidated"))
    initial_failure = any(item["phase"] == "before" and item["exit_code"] != 0 for item in tests)
    final_test = tests[-1]
    patch_path = directory / "agent.patch"
    patch = patch_path.read_text(encoding="utf-8-sig")
    total = re.search(r"Ran (\d+) tests?", final_test["output"])
    rules_sha256 = hashlib.sha256((directory / "rules.py").read_bytes()).hexdigest()
    before = {node["step_id"]: node for node in trace[repair_at]["before"]}
    after = {node["step_id"]: node for node in trace[repair_at]["after"]}
    preserved = before.keys() - set(trace[repair_at]["invalidated"])
    if not (state["status"] == "completed" and receipts and len(receipts) == len(set(receipts)) and initial_failure
            and change_at < repair_at and final_test["phase"] == "after" and final_test["exit_code"] == 0
            and final_test["source_sha256"] == source_sha256 == receipts[-1] and total and "OK" in final_test["output"]
            and trace[change_at]["after"] == rules_sha256 and all(before[key] == after[key] for key in preserved)
            and all(row["isolation"]=="docker-linux-readonly-no-network" for row in tests)
            and [line for line in patch.splitlines() if line.startswith("diff --git ")] == ["diff --git a/shipping.py b/shipping.py"]):
        raise ValueError("Coding evidence did not satisfy the completion gate")
    usage = [row["usage"] for row in trace if "usage" in row]
    return {"status": "verified", "model": usage[-1]["model"], "model_calls": len(usage),
            "total_tokens": sum(item["total_tokens"] for item in usage),
            "tool_calls": sum(row["action"] in {"read","write","test"} for row in trace if "usage" in row),
            "tests": [{"phase": row["phase"], "exit_code": row["exit_code"], "seconds": row["seconds"]} for row in tests],
            "duration_seconds": state["elapsed_seconds"], "confirmed_writes": len(writes), "tests_passed": int(total[1]),
            "source_sha256": source_sha256, "patch_sha256": hashlib.sha256(patch_path.read_bytes()).hexdigest(),
            "preserved_steps": sorted(preserved),
            "invalidated_steps": trace[repair_at]["invalidated"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    args.report.write_text(json.dumps(audit(args.source),ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
