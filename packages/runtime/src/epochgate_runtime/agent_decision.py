"""Evidence-filtered action selection and deterministic DAG repair."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from epochgate_runtime.durable import canonical


class Action(StrEnum):
    CONTINUE = "CONTINUE"
    QUERY_EFFECT = "QUERY_EFFECT"
    RETRY = "RETRY"
    REFRESH_STATE = "REFRESH_STATE"
    LOCAL_REPLAN = "LOCAL_REPLAN"
    FULL_REPLAN = "FULL_REPLAN"
    WAIT = "WAIT"
    STOP = "STOP"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class Cost:
    tokens: int = 0
    tools: int = 0
    replans: int = 0
    latency_ms: int = 0

    def fits(self, budget):
        return all(value <= budget.get(key, 0) for key, value in
                   (("tokens", self.tokens), ("tools", self.tools), ("replans", self.replans)))

    def score(self):
        return self.tokens + 30 * self.tools + 100 * self.replans + self.latency_ms / 10


COSTS = {
    Action.QUERY_EFFECT: Cost(tools=1, latency_ms=80),
    Action.RETRY: Cost(tools=1, latency_ms=100),
    Action.REFRESH_STATE: Cost(tools=1, latency_ms=150),
    Action.LOCAL_REPLAN: Cost(replans=1, latency_ms=160),
    Action.FULL_REPLAN: Cost(replans=1, latency_ms=300),
}


@dataclass(frozen=True)
class Observation:
    evidence: str
    authority: str
    semantic: str
    queryable: bool
    retry_guaranteed: bool
    dependency_known: bool = True


class DecisionEngine(Protocol):
    def decide(self, observation, budget, *, affected=()): ...


class EvidencePolicy:
    def __init__(self, *, optimize=False, full_plan=False):
        self.optimize = optimize
        self.full_plan = full_plan

    def decide(self, observation, budget, *, affected=()):
        s = observation
        if s.evidence == "CONFLICT":
            reason, candidates = "TARGET_CONFLICT", [Action.ESCALATE, Action.STOP]
        elif s.evidence == "CONFIRMED":
            reason, candidates = "TARGET_RECEIPT_CONFIRMED", [Action.CONTINUE]
        elif s.authority != "VALID":
            reason, candidates = "AUTHORITY_UNAVAILABLE", [Action.WAIT, Action.ESCALATE]
        elif s.evidence == "UNKNOWN":
            candidates = ([Action.QUERY_EFFECT] if s.queryable else []) + [Action.WAIT, Action.ESCALATE]
            reason = "EFFECT_UNRESOLVED_QUERYABLE" if s.queryable else "EFFECT_UNRESOLVED_NO_PROOF"
        elif not s.dependency_known or s.semantic == "UNKNOWN":
            reason, candidates = "DEPENDENCIES_UNRESOLVED", [Action.REFRESH_STATE, Action.ESCALATE]
        elif s.semantic == "INVALID":
            reason, candidates = "PREDICATE_INVALID", [Action.LOCAL_REPLAN, Action.FULL_REPLAN]
            if self.full_plan:
                candidates.reverse()
        elif s.evidence == "ABSENT" and s.retry_guaranteed:
            reason, candidates = "TARGET_PROVES_ABSENCE", [Action.RETRY]
        elif s.evidence == "NEW":
            reason, candidates = "NEW_AUTHORIZED_STEP", [Action.CONTINUE]
        else:
            reason, candidates = "NO_SAFE_DISPATCH", [Action.ESCALATE]
        candidate_cost = {a: COSTS.get(a, Cost()) for a in candidates}
        if reason == "NEW_AUTHORIZED_STEP":
            candidate_cost[Action.CONTINUE] = COSTS[Action.RETRY]
        affordable = [a for a in candidates if candidate_cost[a].fits(budget)]
        action = affordable[0] if affordable else Action.STOP
        if self.optimize and affordable:
            # Compare actionable choices first. Waiting/escalation are fallbacks when progress is unaffordable.
            progressing = [a for a in affordable if a not in {Action.WAIT, Action.STOP, Action.ESCALATE}]
            if progressing:
                action = min(progressing, key=lambda a: candidate_cost[a].score())
        cost = candidate_cost.get(action, Cost())
        return {"action": action, "reason_code": reason if affordable else "BUDGET_EXHAUSTED",
                "affected_steps": list(affected), "safe_candidates": candidates,
                "candidate_costs": {a: vars(candidate_cost[a]) for a in candidates},
                "budget": dict(budget), "budget_charge": {"tokens": cost.tokens, "tools": cost.tools, "replans": cost.replans},
                "required_evidence": ["target_receipt"] if action == Action.QUERY_EFFECT else [],
                "policy_version": "evidence-cost-v1" if self.optimize else "evidence-rule-v1"}


def order_plan(plan):
    if not isinstance(plan, list) or not plan or len(plan) > 100:
        raise ValueError("Plan requires 1–100 nodes")
    indexed = {}
    for node in plan:
        if not isinstance(node, dict):
            raise ValueError("Plan node must be an object")
        key = node.get("step_id")
        if not isinstance(key, str) or not key or key in indexed:
            raise ValueError("Step identifiers must be unique")
        for field in ("dependencies", "read_set"):
            values = node.get(field, [])
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError(f"Invalid {field}")
        for field in ("effect", "preconditions", "repair"):
            value = node.get(field, {})
            if not isinstance(value, dict):
                raise ValueError(f"Invalid {field}")
        repair = node.get("repair", {})
        if any(not isinstance(repair.get(field, {}), dict) for field in ("effect", "preconditions")):
            raise ValueError("Invalid replacement effect or predicate")
        writes = node.get("write_set", list(node.get("effect", {})))
        if not isinstance(writes, list) or any(not isinstance(value, str) for value in writes):
            raise ValueError("Invalid write_set")
        if set(writes) != set(node.get("effect", {})):
            raise ValueError("write_set must describe all proposed effects")
        if "effect" in repair and set(repair["effect"]) != set(writes):
            raise ValueError("Repair must retain the declared write scope")
        indexed[key] = {**node, "write_set": writes, "tool_name": "managed_write"}
    for node in plan:
        if not set(node.get("dependencies", ())) <= indexed.keys():
            raise ValueError("Unknown dependency")
    ordered = []
    completed = set()
    while len(ordered) < len(indexed):
        ready = [node for key, node in indexed.items() if key not in completed and set(node.get("dependencies", ())) <= completed]
        if not ready:
            raise ValueError("Cyclic plan")
        ordered.extend(ready)
        completed.update(node["step_id"] for node in ready)
    return ordered


def predicates_hold(node, world):
    return all(key in world and canonical(world[key]["value"]) == canonical(expected)
               for key, expected in node.get("preconditions", {}).items())


def repair_plan(plan, before, after, committed=(), *, strategy="partial"):
    ordered = order_plan(plan)
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    affected, invalid, unresolved, stale_dependencies = set(), set(), set(), set()
    committed = set(committed)
    global_revalidation = strategy == "predicate_graph" and any(
        not predicates_hold(node, after) and (node["step_id"] not in committed or
                                             changed.intersection(node.get("preconditions", {})))
        for node in ordered
    )
    revised = []
    for node in ordered:
        key = node["step_id"]
        dependencies = set(node.get("read_set", ())) | set(node.get("preconditions", {})) | set(node.get("effect", {}))
        affected_here = bool(changed & dependencies)
        inherited = bool(stale_dependencies.intersection(node.get("dependencies", ())))
        if affected_here or inherited:
            affected.add(key)
        if key in committed:
            if affected_here and not predicates_hold(node, after):
                stale_dependencies.add(key)
            revised.append(node)
            continue
        failed = not predicates_hold(node, after)
        if failed or inherited:
            stale_dependencies.add(key)
        needs_repair = {"continue": False, "full": bool(changed), "version": affected_here or inherited,
                        "predicate": failed, "predicate_graph": global_revalidation or inherited,
                        "partial": failed or inherited}[strategy]
        if needs_repair:
            invalid.add(key)
            replacement = node.get("repair", {}) if failed or inherited else {}
            candidate = {**node, **{field: replacement[field] for field in ("effect", "preconditions")
                                   if field in replacement}}
            if not predicates_hold(candidate, after) or (inherited and strategy in {"partial", "predicate_graph"} and not node.get("repair")):
                unresolved.add(key)
            revised.append({**candidate, "write_set": list(candidate.get("effect", {}))})
        else:
            revised.append(node)
    return {"plan": revised, "changed_resources": sorted(changed), "affected_steps": sorted(affected),
            "invalidated_steps": sorted(invalid), "unresolved_steps": sorted(unresolved),
            "preserved_steps": [n["step_id"] for n in ordered if n["step_id"] not in invalid],
            "committed_steps": sorted(committed)}
