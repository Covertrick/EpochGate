"""Run the fixed pilot and optionally sample bounded live model proposals."""

import argparse
import json
from pathlib import Path
import tempfile
import time

from epochgate_runtime.cli import load_project_env
from epochgate_runtime.durable import Queue
from epochgate_runtime.durable_worker import run_one
from epochgate_runtime.model_planner import propose

ARMS = {
    "always_continue": ("continue", "fixed", False),
    "baseline": ("full", "fixed", False),
    "version_based": ("version", "fixed", False),
    "predicate_based": ("predicate", "fixed", False),
    "a1": ("partial", "fixed", False),
    "a2": ("full", "evidence", False),
    "a3": ("partial", "evidence", False),
    "a4": ("partial", "evidence", True),
}

FAMILIES = {
    "code": ("def answer():\n    return 1\n", "def answer():\n    return 2\n"),
    "publication": ("release-notes-v1", "release-notes-v2"),
    "fulfillment": ("route-east", "route-west"),
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def setup_world(queue, values):
    job = queue.submit({"plan": [{"step_id": "seed", "effect": values}]})
    assert run_one(queue, queue, "setup", 30)
    assert queue.get(job)["status"] == "completed"


def case(family, scenario):
    old, new = FAMILIES[family]
    expected = old if scenario == "unrelated" else new
    return {
        "id": family + "-" + scenario, "family": family, "holdout": family == "fulfillment",
        "scenario": scenario, "expected": {"artifact": expected},
        "plan": [
            {"step_id": "verify", "read_set": ["spec"], "preconditions": {"spec": "v1"},
             "repair": {"preconditions": {"spec": "v2"}}},
            {"step_id": "publish", "dependencies": ["verify"], "effect": {"artifact": old},
             "repair": {"effect": {"artifact": new}}},
        ],
    }


def execute_case(fixture, arm, plan=None):
    started = time.perf_counter()
    strategy, recovery, optimize = ARMS[arm]
    scenario = fixture["scenario"]
    with tempfile.TemporaryDirectory() as scratch:
        queue = Queue(Path(scratch) / "pilot.sqlite")
        setup_world(queue, {"spec": "v1"})
        observed = queue.world()
        if any(item.get("preconditions", {}).get("spec") == "v2" for item in (plan or {}).get("plan", [])):
            setup_world(queue, {"spec": "v2"})
        elif scenario == "unrelated":
            setup_world(queue, {"noise": 1})
        elif scenario not in {"opaque", "lost_ack"} or plan:
            setup_world(queue, {"spec": "v2"})
        task = {"plan": fixture["plan"], "observed_world": observed, **(plan or {}), "strategy": strategy,
                "recovery": recovery, "optimize": optimize, "queryable": scenario != "opaque"}
        if plan:
            task["observed_world"] = queue.world()
        job = queue.submit(task)
        if scenario in {"opaque", "lost_ack"} and not plan:
            # Seed the real pending-dispatch boundary. Only lost_ack commits a receiver record.
            claim = queue.claim("lost-worker", 30)
            proposal = {"effect": {}, "versions": {"spec": 1}, "predicates": {"spec": "v1"}}
            queue.record(job, claim["epoch"], "lost-worker", {"kind": "dispatch", "step": "verify", "proposal": proposal})
            if scenario == "lost_ack":
                verdict = queue.commit(job, claim["epoch"], "lost-worker", "verify", **proposal)
                assert verdict["status"] == "COMMITTED"
            setup_world(queue, {"spec": "v2"})
            with queue.transaction() as db:
                db.execute("UPDATE jobs SET deadline=0 WHERE id=?", (job,))
        run_one(queue, queue, "pilot-worker", 30)
        return {"case": fixture, "arm": arm, "job": queue.get(job), "world": queue.world(),
                "elapsed_seconds": time.perf_counter() - started}


def run_frozen():
    cases = [case(family, scenario) for family in FAMILIES for scenario in ("updated", "unrelated", "lost_ack")]
    cases.append(case("fulfillment", "opaque"))
    return [execute_case(fixture, arm) for fixture in cases for arm in ARMS]


def run_live(previous=None):
    load_project_env()
    results = []
    for family, (_old, desired) in FAMILIES.items():
        fixture = case(family, "updated")
        try:
            # Inputs are bounded synthetic fixtures. No repository content or credentials enter the prompt.
            if previous is None:
                proposal = propose(f"Write exactly this value to artifact without fences or extra text: {desired!r}", ["artifact"])
            else:
                prior = next(row for row in previous if row["case"]["family"] == family)
                if "error" in prior:
                    results.append(prior)
                    continue
                task = prior["job"]["task"]
                proposal = {"plan": task["plan"], "planner": task["planner"]}
            results.append(execute_case(fixture, "a4", proposal))
        except Exception as exc:
            results.append({"case": fixture, "arm": "a4", "error": type(exc).__name__, "http_status": getattr(exc, "code", None)})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--reuse-proposals", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    name = "live-pilot.json" if args.live else "frozen-pilot.json"
    path = args.output / name
    if path.exists():
        parser.error("Refusing to overwrite a pilot record")
    previous = json.loads(args.reuse_proposals.read_bytes()) if args.reuse_proposals else None
    write_json(path, run_live(previous) if args.live else run_frozen())


if __name__ == "__main__":
    main()
