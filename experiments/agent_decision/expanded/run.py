"""Forty fixed offline tasks through the durable worker and managed receiver."""

import argparse
import json
from pathlib import Path
import tempfile
import time

from epochgate_runtime.durable import Queue
from epochgate_runtime.durable_worker import run_one


FAMILIES = {
    "shipping": ("standard-450", "discount-250"),
    "catalog": ("published-draft", "published-revised"),
    "deployment": ("staging-v1", "staging-v2"),
    "fulfillment": ("route-east", "route-west"),
}
SCHEDULES = ("unrelated", "version_only", "invalid_predicate", "tool_failure", "response_loss",
             "queryable_unknown", "opaque_unknown", "many_dependencies", "cancel", "successor_reconcile")
POLICIES = {
    "full": ("full", "fixed"), "version": ("version", "fixed"),
    "predicate_strong": ("predicate_graph", "fixed"), "partial": ("partial", "fixed"),
    "partial_evidence": ("partial", "evidence"),
}


def plan_for(family, schedule):
    old, new = FAMILIES[family]
    steps = [
        {"step_id": "inspect", "effect": {"inspection": family}},
        {"step_id": "eligibility", "dependencies": ["inspect"], "read_set": ["spec"],
         "preconditions": {"spec": 1}, "repair": {"preconditions": {"spec": 2}}},
        {"step_id": "publish", "dependencies": ["eligibility"], "effect": {"artifact": old},
         "repair": {"effect": {"artifact": new}}},
    ]
    if schedule == "many_dependencies":
        steps.insert(2, {"step_id": "review", "dependencies": ["eligibility"], "effect": {"review": "old"},
                         "repair": {"effect": {"review": "new"}}})
        steps[-1]["dependencies"].append("review")
    return steps


def seed(queue, effect):
    job = queue.submit({"plan": [{"step_id": "setup", "effect": effect}]})
    assert run_one(queue, queue, "external-actor", 30)
    assert queue.get(job)["status"] == "completed"


def expire(queue, job):
    with queue.transaction() as db:
        db.execute("UPDATE jobs SET deadline=0 WHERE id=? AND status='running'", (job,))


class FaultReceiver:
    def __init__(self, queue, schedule):
        self.queue = queue
        self.schedule = schedule
        self.fired = False

    def receipt(self, job, step):
        return self.queue.receipt(job, step)

    def commit(self, job, epoch, worker, step, effect, versions, predicates):
        if step == "eligibility" and not self.fired and self.schedule in {
            "tool_failure", "response_loss", "queryable_unknown", "opaque_unknown", "successor_reconcile"
        }:
            self.fired = True
            if self.schedule in {"response_loss", "successor_reconcile"}:
                result = self.queue.commit(job, epoch, worker, step, effect, versions, predicates)
                assert result["status"] == "COMMITTED"
            raise ConnectionError(self.schedule)
        result = self.queue.commit(job, epoch, worker, step, effect, versions, predicates)
        if step == "inspect" and result["status"] == "COMMITTED" and self.schedule == "cancel":
            self.fired = True
            assert self.queue.cancel(job)
        return result


def run(family, schedule, policy, base):
    strategy, recovery = POLICIES[policy]
    plan = plan_for(family, schedule)
    expected = {"artifact": FAMILIES[family][schedule in {"invalid_predicate", "many_dependencies", "successor_reconcile"}]}
    if schedule == "many_dependencies":
        expected["review"] = "new"
    with tempfile.TemporaryDirectory(dir=base) as work:
        queue = Queue(Path(work) / "trial.sqlite")
        seed(queue, {"spec": 1})
        task = {"plan": plan, "observed_world": queue.world(), "strategy": strategy,
                "recovery": recovery, "queryable": schedule != "opaque_unknown",
                "budget": {"tools": 30, "replans": 8, "tokens": 20000}}
        job = queue.submit(task)
        # Keep the claimed task out of the seed worker's queue before changing the world.
        if schedule in {"unrelated", "version_only", "invalid_predicate", "many_dependencies"}:
            assert queue.claim("scheduler", 30)["id"] == job
            update = {"noise": 1} if schedule == "unrelated" else {"spec": 1 if schedule == "version_only" else 2}
            seed(queue, update)
            expire(queue, job)
        receiver = FaultReceiver(queue,schedule)
        started = time.perf_counter()
        run_one(queue, receiver, "primary", 30)
        recovery_seconds = None
        if queue.get(job)["status"] == "running":
            if schedule == "successor_reconcile":
                seed(queue, {"spec": 2})
            expire(queue, job)
            recovery_started = time.perf_counter()
            run_one(queue, receiver, "takeover", 30)
            recovery_seconds = time.perf_counter() - recovery_started
        return {"id": f"{family}-{schedule}", "holdout": family == "fulfillment", "schedule": schedule,
                "policy": policy, "expected": expected, "job": queue.get(job), "world": queue.world(),
                "fault_injected": receiver.fired, "elapsed_seconds": time.perf_counter()-started,
                "recovery_seconds": recovery_seconds}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x",encoding="utf-8",newline="\n") as stream:
        for family in FAMILIES:
            for schedule in SCHEDULES:
                for policy in POLICIES:
                    stream.write(json.dumps(run(family,schedule,policy,output.parent),ensure_ascii=False)+"\n")
