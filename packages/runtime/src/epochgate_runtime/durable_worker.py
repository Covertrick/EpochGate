"""Independent durable Worker. It can use the local or authenticated HTTP receiver."""

import argparse
import json
import os
from pathlib import Path
import threading
import time
from urllib.request import Request, urlopen
import uuid

from epochgate_runtime.agent_decision import Action, EvidencePolicy, Observation, order_plan, predicates_hold, repair_plan
from epochgate_runtime.durable import Queue


class HttpReceiver:
    def __init__(self, base, token):
        self.url = base.rstrip("/") + "/agent/receiver"
        self.token = token

    def call(self, data):
        body = json.dumps(data).encode()
        token = self.token() if callable(self.token) else self.token
        request = Request(self.url, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    def receipt(self, job, step):
        return self.call({"operation": "query", "job": job, "step": step})["record"]

    def commit(self, job, epoch, worker, step, effect, versions, predicates):
        return self.call(dict(operation="commit", job=job, epoch=epoch, worker=worker, step=step,
                              effect=effect, versions=versions, predicates=predicates))


def execute(queue, receiver, claim, *, fault_after_commit=False):
    job, epoch, worker = (claim[k] for k in ("id", "epoch", "owner"))
    snapshot = queue.get(job)
    task = snapshot["task"]
    budget = {**{"tokens": 20000, "tools": 30, "replans": 3}, **task.get("budget", {})}
    budget["tokens"] -= task.get("planner", {}).get("tokens", 0)
    plan = order_plan(task["plan"])
    initial = task.get("observed_world", {})
    provider_queryable = task.get("queryable", True)
    strategy = task.get("strategy", "partial")
    fixed = task.get("recovery") == "fixed"
    policy = EvidencePolicy(optimize=task.get("optimize", False), full_plan=strategy == "full")
    verified = set()
    inflight = set()
    for event in snapshot["decisions"]:
        for key, amount in event.get("budget_charge", {}).items():
            budget[key] -= amount
        if event.get("kind") == "dispatch":
            inflight.add(event["step"])
        elif event.get("kind") == "evidence" and event["result"] in {"CONFIRMED", "ABSENT"}:
            inflight.discard(event["step"])
            if event["result"] == "CONFIRMED":
                verified.add(event["step"])
        elif event.get("kind") == "repair":
            plan = event["after"]
            initial = event["world"]

    def record(kind, step=None, *, finish=None, **data):
        return queue.record(job, epoch, worker, {"kind": kind, "step": step, **data}, finish=finish)

    def decide(step, observation, *, affected=()):
        decision = policy.decide(observation, budget, affected=affected)
        if fixed and observation.evidence == "UNKNOWN":
            decision.update(action=Action.WAIT, reason_code="FIXED_STOP_ON_AMBIGUITY", policy_version="fixed-recovery-v1",
                            budget_charge={"tokens": 0, "tools": 0, "replans": 0})
        if not record("decision", step, observation=vars(observation), **decision):
            return None
        for key, amount in decision["budget_charge"].items():
            budget[key] -= amount
        return decision["action"]

    if budget["tokens"] < 0:
        record("terminal", result="TOKEN_BUDGET_EXHAUSTED", budget=budget, finish="stopped")
        return
    if task.get("planner") and not snapshot["decisions"]:
        if not record("planner", usage=task["planner"], remaining_budget=budget):
            return

    for step in sorted(inflight):
        action = decide(step, Observation("UNKNOWN", "VALID", "UNKNOWN", provider_queryable, False))
        if action != Action.QUERY_EFFECT:
            record("terminal", step, result="UNKNOWN", finish="unknown")
            return
        query_started = time.perf_counter()
        try:
            receipt = receiver.receipt(job, step)
        except (OSError, ValueError, KeyError):
            record("terminal", step, result="TARGET_UNAVAILABLE", finish="unknown")
            return
        if not record("evidence", step, result="CONFIRMED" if receipt else "ABSENT", target=receipt,
                      query_seconds=time.perf_counter() - query_started):
            return
        if receipt:
            verified.add(step)
            decide(step, Observation("CONFIRMED", "VALID", "UNKNOWN", True, True))

    while True:
        node = next((item for item in plan if item["step_id"] not in verified), None)
        if not node:
            record("terminal", result="DONE", budget=budget, finish="completed")
            return
        step = node["step_id"]
        world = queue.world()
        if node.get("dependencies_known") is False:
            action = decide(step, Observation("NEW", "VALID", "UNKNOWN", True, False, dependency_known=False))
            if action == Action.REFRESH_STATE:
                world = queue.world()
                if not record("observation", step, world=world, result="STATE_REFRESHED"):
                    return
            record("terminal", step, result="DEPENDENCIES_UNRESOLVED", finish="stopped")
            return
        repair = repair_plan(plan, initial, world, verified, strategy=strategy)
        if repair["invalidated_steps"]:
            action = decide(step, Observation("NEW", "VALID", "INVALID", True, True),
                            affected=repair["invalidated_steps"])
            if action not in {Action.LOCAL_REPLAN, Action.FULL_REPLAN} or repair["unresolved_steps"]:
                record("terminal", step, result="UNRESOLVED_PLAN", repair=repair, finish="stopped")
                return
            repaired = repair.pop("plan")
            if not record("repair", step, before=plan, after=repaired, world=world, **repair):
                return
            plan = repaired
            node = next(item for item in plan if item["step_id"] == step)
        initial = world
        semantic = "VALID" if predicates_hold(node, world) else "INVALID"
        action = decide(step, Observation("ABSENT" if step in inflight else "NEW", "VALID", semantic, True, True))
        if action not in {Action.CONTINUE, Action.RETRY}:
            record("terminal", step, result="DISPATCH_NOT_ALLOWED", finish="stopped")
            return
        effect = node.get("effect", {})
        resources = set(node.get("read_set", ())) | set(effect)
        versions = {key: world.get(key, {}).get("version", 0) for key in resources}
        predicates = node.get("preconditions", {})
        if not record("dispatch", step, changed_resources=repair["changed_resources"],
                      proposal={"effect": effect, "versions": versions, "predicates": predicates}):
            return
        dispatch_started = time.perf_counter()
        try:
            result = receiver.commit(job, epoch, worker, step, effect, versions, predicates)
        except (OSError, ValueError, KeyError):
            # Leave the persistent dispatch marker for the next lease to reconcile.
            return
        if result["status"] in {"COMMITTED", "REPLAY"}:
            if fault_after_commit:
                os._exit(86)
            if not record("evidence", step, result="CONFIRMED", target=result,
                          dispatch_seconds=time.perf_counter() - dispatch_started):
                return
            verified.add(step)
        elif result["status"] == "FENCED":
            return
        elif result["status"] in {"STALE_WORLD", "INVALID_PREDICATE"}:
            if not record("evidence", step, result="ABSENT", target=result):
                return
            # The receiver explicitly rejected the write. Reconsider using the latest world view.
        else:
            record("terminal", step, result=result["status"], target=result, finish="stopped")
            return


def run_one(queue, receiver, worker, ttl, *, fault=False):
    claim = queue.claim(worker, ttl)
    if claim is None:
        return False
    stop = threading.Event()

    def keep_lease():
        while not stop.wait(ttl / 3):
            if not queue.heartbeat(claim["id"], claim["epoch"], worker, ttl):
                break

    thread = threading.Thread(target=keep_lease, daemon=True)
    thread.start()
    try:
        execute(queue, receiver, claim, fault_after_commit=fault)
    finally:
        stop.set()
        thread.join()
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--ttl", type=float, default=10)
    parser.add_argument("--receiver-url")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--fault-after-commit", action="store_true")
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    queue = Queue(args.data_dir / "agent-queue.sqlite")
    def token():
        explicit = os.environ.get("EG_CONTROL_TOKEN")
        if explicit:
            return explicit
        return json.loads((args.data_dir / "server.json").read_text(encoding="utf-8"))["token"]

    receiver = HttpReceiver(args.receiver_url, token) if args.receiver_url else queue
    worker = uuid.uuid4().hex
    while True:
        worked = run_one(queue, receiver, worker, args.ttl, fault=args.fault_after_commit)
        if args.once:
            return
        if not worked:
            time.sleep(.25)


if __name__ == "__main__":
    main()
