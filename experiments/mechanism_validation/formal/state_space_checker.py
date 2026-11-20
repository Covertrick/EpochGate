"""Exhaustive enumerator for the Week 5 recovery decision function.

The checker is this process. TLC is not on PATH and no TLA+ run is claimed.
Java being installed is not a model-checker run.

Fact combinations are the Cartesian product of the nine observations below.
Authorization is a separate parameter space. The enumerator evaluates every
fact under every declared policy and every explicit permission grant. It does
not select the assignment that lets a crashed claim publish.
"""

from __future__ import annotations

import csv
import itertools
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMAND = ".venv/Scripts/python.exe experiments/mechanism_validation/formal/state_space_checker.py"

RELATIONS = ("MATCH", "MISMATCH", "NOT_APPLICABLE")
AUTHORITIES = ("VALID", "SUPERSEDED", "UNKNOWN")
SEMANTICS = ("VALID", "INVALID", "UNKNOWN")
SINKS = ("VERSION_FENCED", "IDEMPOTENT_ONLY", "QUERYABLE", "OPAQUE")

EVIDENCE_STATES = frozenset(
    {
        "UNKNOWN",
        "SEALED_ONLY",
        "TARGET_CONFIRMED",
        "RECEIPT_CONFIRMED",
        "CONFLICT",
        "INCONSISTENT",
    }
)
AUTHORITY_STATES = frozenset(AUTHORITIES)
SEMANTIC_STATES = frozenset(SEMANTICS)
SINK_CONTRACTS = frozenset(SINKS)

# CONFLICT is a separate evidence state. It is not a rank between UNKNOWN and VALID.
EVIDENCE_ORDER = None

RECOVERY_ACTIONS = frozenset(
    {
        "HOLD_INCONSISTENT",
        "HOLD_CONFLICT",
        "WITHHOLD_UNSEALED",
        "WITHHOLD_SUPERSEDED",
        "WITHHOLD_UNKNOWN",
        "WITHHOLD_STALE_AUTHORITY",
        "WITHHOLD_NO_DISPATCH_PERMISSION",
        "WITHHOLD_CONFIRM_NO_WRITE_PERMISSION",
        "WITHHOLD_RECONCILE_NO_PERMISSION",
        "WITHHOLD_OPAQUE_UNCONFIRMED",
        "RECONCILE_READONLY",
        "CONFIRM_EXISTING_TARGET",
        "REPLAY_STORED_RECEIPT",
        "PUBLISH_SEALED_BYTES",
    }
)

CONFIRM_ACTIONS = frozenset({"CONFIRM_EXISTING_TARGET", "REPLAY_STORED_RECEIPT"})
PUBLISH_ACTION = "PUBLISH_SEALED_BYTES"

# Names recorded in Week 3 and Week 4. This table documents the mapping.
# It is not a rewrite of those rows.
HISTORICAL_NAMES = {
    "UNKNOWN": "Week 4 prose: sealed, target, and receipt all absent. Week 5 evidence UNKNOWN. Not a failure and not an automatic retry. Week 4 RSR also returned this label when the precheck was neither VALID nor INVALID; that collapse is recorded as week4_collapsed_label while evidence stays SEALED_ONLY.",
    "PUBLISH_SEALED": "Week 4 evidence label for sealed bytes with no target and no receipt. Week 5 evidence is SEALED_ONLY, never TARGET_CONFIRMED. The historical rows keep the name PUBLISH_SEALED.",
    "CONFIRM_TARGET": "Week 4 label for target digest equal to sealed and no receipt file. Week 5 evidence TARGET_CONFIRMED. Action CONFIRM_EXISTING_TARGET does not rewrite the file.",
    "REPLAY_RECEIPT": "Week 4 label only when receipt digest, sealed, and target are the same. A disagreeing receipt is not this state and is not success.",
    "CONFLICT": "Same effect key, sealed and target both present, payload hashes differ. Not an ordered point between UNKNOWN and VALID. Action HOLD_CONFLICT does not overwrite.",
    "SEMANTICALLY_SUPERSEDED": "Week 4 label for the sealed-only durable condition plus an INVALID precheck. Week 5 keeps that label on the historical rows and factors it into evidence SEALED_ONLY, semantic INVALID, action WITHHOLD_SUPERSEDED.",
    "WITHHOLD_SUPERSEDED": "Week 4 action for an INVALID precheck. Week 5 keeps this action. publish_allowed is false.",
    "WITHHOLD_UNSEALED": "Week 4 action when there is nothing sealed to publish.",
    "HOLD_CONFLICT": "Week 4 action. The target payload is left in place.",
    "REPLAY_STORED_RECEIPT": "Week 4 action. No second publish.",
    "CONFIRM_EXISTING_TARGET": "Week 4 action. No rewrite.",
    "PUBLISH_SEALED_BYTES": "Week 4 action that writes sealed bytes to a missing target. Week 5 emits it only when safety and an explicit dispatch permission both hold. The transition is RESTORED_BYTES, not CONFIRMED_EFFECT.",
    "RELAY_THEN_REPLAY": "Week 4 outbox action on after_commit_before_send. One logical effect, then a replay. Week 5 models that as one PRODUCE_NEW_EFFECT transition. Historical rows keep RELAY_THEN_REPLAY.",
    "RESTORED_BYTES": "Week 3 recovery label for a new process writing sealed bytes. It does not imply CONFIRMED_EFFECT. Week 3 kept the physical column at PRESENT_UNVERIFIED.",
    "CONFIRMED_EFFECT": "Week 3 physical label for a forward publish that matches the legal payload. This enumerator never assigns it to a recovery write.",
    "CORRECT_STOP": "Week 3 successor status. It is not a successor failure. Week 1 mapped it to NOT_APPLICABLE, not NOT_COMPLETED.",
    "NOT_APPLICABLE": "Week 3 recovery or successor label. It is not a failure.",
    "STALE_ACCEPTED": "Week 2 authority-oracle observation that a stale token was accepted. It is not the Week 5 authority value SUPERSEDED, and it is not a rank between UNKNOWN and VALID. Historical rows keep the name.",
    "NO_STALE_ACCEPTANCE_RECORDED": "Week 2 authority-oracle observation. It is not proof of authority VALID, and it is not a failure.",
}

REQUIRED_OUTPUTS = (
    "evidence_state",
    "authority_state",
    "semantic_state",
    "recovery_action",
    "publish_allowed",
    "requires_reconciliation",
    "rejection_reason",
)

INVARIANT_IDS = ("I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8", "I9")


@dataclass(frozen=True)
class Fact:
    sealed: bool
    target: bool
    receipt: bool
    receipt_digest: str
    effect_key: str
    payload_hash: str
    authority: str
    semantic: str
    sink: str

    def as_dict(self) -> dict[str, object]:
        return {
            "sealed_present": self.sealed,
            "target_present": self.target,
            "receipt_present": self.receipt,
            "receipt_digest_match": self.receipt_digest,
            "effect_key_match": self.effect_key,
            "payload_hash_match": self.payload_hash,
            "authority": self.authority,
            "semantic_oracle": self.semantic,
            "sink_contract": self.sink,
        }


@dataclass(frozen=True)
class Policy:
    claim_consumes_authorization: bool
    crashed_continuation: str
    revocation_of_sent: str
    confirm_requires_write_permission: bool
    republish_requires_new_dispatch: bool
    reconcile_requires_permission: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "claim_consumes_authorization": self.claim_consumes_authorization,
            "crashed_continuation": self.crashed_continuation,
            "revocation_of_sent": self.revocation_of_sent,
            "confirm_requires_write_permission": self.confirm_requires_write_permission,
            "republish_requires_new_dispatch": self.republish_requires_new_dispatch,
            "reconcile_requires_permission": self.reconcile_requires_permission,
        }


@dataclass(frozen=True)
class Grants:
    new_dispatch: bool
    write_permission: bool
    reconcile_permission: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "new_dispatch_granted": self.new_dispatch,
            "write_permission_granted": self.write_permission,
            "reconcile_permission_granted": self.reconcile_permission,
        }


@dataclass(frozen=True)
class Decision:
    evidence_state: str
    authority_state: str
    semantic_state: str
    recovery_action: str
    publish_allowed: bool
    requires_reconciliation: bool
    rejection_reason: str
    operation: str
    treated_as_success: bool
    successor_failure: bool
    recovery_history: str
    physical_label: str
    branch: str
    week4_collapsed_label: str

    def outputs_defined(self) -> bool:
        values = (
            self.evidence_state,
            self.authority_state,
            self.semantic_state,
            self.recovery_action,
            self.publish_allowed,
            self.requires_reconciliation,
            self.rejection_reason,
        )
        if any(value is None or value == "" for value in values):
            return False
        if self.evidence_state not in EVIDENCE_STATES:
            return False
        if self.authority_state not in AUTHORITY_STATES:
            return False
        if self.semantic_state not in SEMANTIC_STATES:
            return False
        if self.recovery_action not in RECOVERY_ACTIONS:
            return False
        if not isinstance(self.publish_allowed, bool):
            return False
        if not isinstance(self.requires_reconciliation, bool):
            return False
        return True


def all_facts() -> list[Fact]:
    facts: list[Fact] = []
    for sealed, target, receipt in itertools.product((False, True), repeat=3):
        for receipt_digest, effect_key, payload_hash in itertools.product(RELATIONS, repeat=3):
            for authority in AUTHORITIES:
                for semantic in SEMANTICS:
                    for sink in SINKS:
                        facts.append(
                            Fact(
                                sealed=sealed,
                                target=target,
                                receipt=receipt,
                                receipt_digest=receipt_digest,
                                effect_key=effect_key,
                                payload_hash=payload_hash,
                                authority=authority,
                                semantic=semantic,
                                sink=sink,
                            )
                        )
    return facts


def all_policies() -> list[Policy]:
    policies: list[Policy] = []
    for (
        claim_consumes,
        continuation,
        revocation,
        confirm_write,
        republish_dispatch,
        reconcile_perm,
    ) in itertools.product(
        (False, True),
        ("OLD_GRANT_ALLOWED", "OLD_GRANT_DENIED"),
        ("FENCE_AT_COMMIT_BOUNDARY", "SENT_IMMUNE"),
        (False, True),
        (False, True),
        (False, True),
    ):
        policies.append(
            Policy(
                claim_consumes_authorization=claim_consumes,
                crashed_continuation=continuation,
                revocation_of_sent=revocation,
                confirm_requires_write_permission=confirm_write,
                republish_requires_new_dispatch=republish_dispatch,
                reconcile_requires_permission=reconcile_perm,
            )
        )
    return policies


def all_grants() -> list[Grants]:
    return [Grants(*values) for values in itertools.product((False, True), repeat=3)]


def encoding_errors(fact: Fact) -> tuple[str, ...]:
    errors: list[str] = []
    digest_comparable = fact.receipt and fact.sealed
    if digest_comparable and fact.receipt_digest not in {"MATCH", "MISMATCH"}:
        errors.append("RECEIPT_DIGEST_MISSING")
    if not digest_comparable and fact.receipt_digest != "NOT_APPLICABLE":
        errors.append("RECEIPT_DIGEST_WITHOUT_PAIR")
    payload_comparable = fact.sealed and fact.target
    if payload_comparable and fact.payload_hash not in {"MATCH", "MISMATCH"}:
        errors.append("PAYLOAD_HASH_MISSING")
    if not payload_comparable and fact.payload_hash != "NOT_APPLICABLE":
        errors.append("PAYLOAD_HASH_WITHOUT_PAIR")
    artifacts = int(fact.sealed) + int(fact.target) + int(fact.receipt)
    if artifacts >= 2 and fact.effect_key not in {"MATCH", "MISMATCH"}:
        errors.append("EFFECT_KEY_MISSING")
    if artifacts < 2 and fact.effect_key != "NOT_APPLICABLE":
        errors.append("EFFECT_KEY_WITHOUT_PAIR")
    return tuple(errors)


def is_realizable(fact: Fact) -> bool:
    return not encoding_errors(fact)


def classify_evidence(fact: Fact) -> tuple[str, str]:
    """Byte evidence only. Authority and semantics do not change this label."""
    errors = encoding_errors(fact)
    if errors:
        return "INCONSISTENT", "FACT_ENCODING:" + "+".join(errors)
    if not fact.sealed and not fact.target and not fact.receipt:
        return "UNKNOWN", "NO_DURABLE_BYTES"
    if fact.sealed and fact.target:
        if fact.effect_key == "MISMATCH":
            return "INCONSISTENT", "EFFECT_KEY_MISMATCH"
        if fact.payload_hash == "MISMATCH":
            return "CONFLICT", "SAME_KEY_DIFFERENT_PAYLOAD"
        if not fact.receipt:
            return "TARGET_CONFIRMED", "NONE"
        if fact.receipt_digest == "MATCH":
            return "RECEIPT_CONFIRMED", "NONE"
        return "INCONSISTENT", "RECEIPT_DISAGREES_WITH_SEALED"
    if fact.receipt and not fact.target:
        if not fact.sealed:
            return "INCONSISTENT", "RECEIPT_WITHOUT_SEALED_OR_TARGET"
        if fact.effect_key == "MISMATCH":
            return "INCONSISTENT", "RECEIPT_KEY_MISMATCH_WITHOUT_TARGET"
        if fact.receipt_digest == "MISMATCH":
            return "INCONSISTENT", "RECEIPT_DISAGREES_WITH_SEALED"
        return "INCONSISTENT", "RECEIPT_WITHOUT_TARGET"
    if fact.target and not fact.sealed:
        return "INCONSISTENT", "TARGET_WITHOUT_SEALED"
    if fact.sealed and not fact.target and not fact.receipt:
        return "SEALED_ONLY", "NONE"
    return "INCONSISTENT", "UNCLASSIFIED_COMBINATION"


def week4_collapsed_label(evidence: str, semantic: str) -> str:
    if evidence == "CONFLICT":
        return "CONFLICT"
    if evidence == "UNKNOWN":
        return "UNKNOWN"
    if evidence == "TARGET_CONFIRMED":
        return "CONFIRM_TARGET"
    if evidence == "RECEIPT_CONFIRMED":
        return "REPLAY_RECEIPT"
    if evidence == "SEALED_ONLY" and semantic == "INVALID":
        return "SEMANTICALLY_SUPERSEDED"
    if evidence == "SEALED_ONLY" and semantic == "UNKNOWN":
        return "UNKNOWN"
    if evidence == "SEALED_ONLY":
        return "PUBLISH_SEALED"
    return "OUTSIDE_WEEK4_PROSE"


def logical_effects(fact: Fact, evidence: str | None = None) -> int:
    if evidence is None:
        evidence, _reason = classify_evidence(fact)
    if evidence in {"TARGET_CONFIRMED", "RECEIPT_CONFIRMED", "CONFLICT"}:
        return 1
    if evidence == "INCONSISTENT" and fact.target and fact.effect_key == "MATCH":
        return 1
    if (
        evidence == "INCONSISTENT"
        and fact.receipt
        and fact.sealed
        and fact.receipt_digest == "MATCH"
        and fact.effect_key == "MATCH"
    ):
        return 1
    return 0


def dispatch_permitted(fact: Fact, policy: Policy, grants: Grants) -> bool:
    """Explicit permission for a new recovery publish.

    The killed process does not resume. `crashed_continuation` only says
    whether a new process may spend the original grant on the same sealed
    operation. A consumed grant does not remain spendable. A required new
    dispatch permission is a separate grant bit, not an implied yes.
    """
    if fact.authority != "VALID" or fact.semantic != "VALID":
        return False
    if fact.sink == "OPAQUE":
        return False
    consumed = policy.claim_consumes_authorization and fact.sealed
    old_usable = policy.crashed_continuation == "OLD_GRANT_ALLOWED" and not consumed
    if policy.republish_requires_new_dispatch:
        return grants.new_dispatch
    if old_usable:
        return True
    return grants.new_dispatch


def dispatch_permitted_independent(consumes: bool, allowed: bool, republish: bool, grant: bool) -> bool:
    """Same predicate, written out so the sweep can be checked against it."""
    if republish:
        return grant
    if allowed and not consumes:
        return True
    return grant


def reconcile_permitted(policy: Policy, grants: Grants) -> bool:
    if not policy.reconcile_requires_permission:
        return True
    return grants.reconcile_permission


def confirm_write_permitted(policy: Policy, grants: Grants) -> bool:
    if not policy.confirm_requires_write_permission:
        return True
    return grants.write_permission


def historical_overlay(authority: str, semantic: str) -> tuple[str, bool, bool]:
    """Reason, treated_as_success, requires_reconciliation for an existing effect."""
    if semantic == "INVALID":
        return "SEMANTIC_INVALID_NO_REPAIR_PUBLISH", False, True
    if semantic == "UNKNOWN" and authority == "UNKNOWN":
        return "AUTHORITY_AND_SEMANTIC_UNKNOWN_NOT_FAILURE", False, True
    if semantic == "UNKNOWN":
        return "SEMANTIC_UNKNOWN_NOT_FAILURE", False, True
    if authority == "UNKNOWN":
        return "AUTHORITY_UNKNOWN_NOT_FAILURE", False, True
    if authority == "SUPERSEDED":
        return "AUTHORITY_SUPERSEDED_HISTORICAL_NOT_REWRITTEN", False, True
    return "NONE", True, False


def fence_reason_for_committed(fact: Fact, policy: Policy, overlay_reason: str) -> str:
    if fact.authority != "SUPERSEDED" or fact.sink != "VERSION_FENCED":
        return overlay_reason
    if policy.revocation_of_sent == "SENT_IMMUNE":
        return "SENT_ALREADY_COMMITTED_NOT_REWRITTEN"
    return "FENCE_DOES_NOT_REWRITE_COMMITTED_TARGET"


def make_decision(
    fact: Fact,
    *,
    evidence: str,
    action: str,
    reason: str,
    publish: bool,
    reconcile: bool,
    operation: str,
    success: bool,
    branch: str,
    recovery_history: str = "NOT_APPLICABLE",
    physical_label: str = "NOT_CONFIRMED_EFFECT",
) -> Decision:
    return Decision(
        evidence_state=evidence,
        authority_state=fact.authority,
        semantic_state=fact.semantic,
        recovery_action=action,
        publish_allowed=publish,
        requires_reconciliation=reconcile,
        rejection_reason=reason,
        operation=operation,
        treated_as_success=success,
        successor_failure=False,
        recovery_history=recovery_history,
        physical_label=physical_label,
        branch=branch,
        week4_collapsed_label=week4_collapsed_label(evidence, fact.semantic),
    )


def decide(fact: Fact, policy: Policy, grants: Grants) -> Decision:
    evidence, byte_reason = classify_evidence(fact)
    if evidence == "INCONSISTENT":
        if fact.sink == "QUERYABLE" and reconcile_permitted(policy, grants):
            action = "RECONCILE_READONLY"
            branch = "INCONSISTENT_RECONCILE_READONLY"
        elif fact.sink == "QUERYABLE":
            action = "WITHHOLD_RECONCILE_NO_PERMISSION"
            branch = "INCONSISTENT_RECONCILE_UNPERMITTED"
        else:
            action = "HOLD_INCONSISTENT"
            branch = "INCONSISTENT_HOLD"
        return make_decision(
            fact,
            evidence=evidence,
            action=action,
            reason=byte_reason,
            publish=False,
            reconcile=True,
            operation="NONE",
            success=False,
            branch=branch,
        )
    if evidence == "CONFLICT":
        return make_decision(
            fact,
            evidence=evidence,
            action="HOLD_CONFLICT",
            reason="SAME_KEY_DIFFERENT_PAYLOAD",
            publish=False,
            reconcile=True,
            operation="NONE",
            success=False,
            branch="CONFLICT_HOLD",
        )
    if evidence == "UNKNOWN":
        return make_decision(
            fact,
            evidence=evidence,
            action="WITHHOLD_UNSEALED",
            reason="NO_DURABLE_BYTES",
            publish=False,
            reconcile=False,
            operation="NONE",
            success=False,
            branch="UNKNOWN_WITHHOLD",
        )
    if evidence == "RECEIPT_CONFIRMED":
        reason, success, reconcile = historical_overlay(fact.authority, fact.semantic)
        if fact.sink == "VERSION_FENCED" and fact.authority == "SUPERSEDED":
            reason = fence_reason_for_committed(fact, policy, reason)
        return make_decision(
            fact,
            evidence=evidence,
            action="REPLAY_STORED_RECEIPT",
            reason=reason,
            publish=False,
            reconcile=reconcile,
            operation="CONFIRM_HISTORICAL",
            success=success,
            branch="RECEIPT_REPLAY",
        )
    if evidence == "TARGET_CONFIRMED":
        reason, success, reconcile = historical_overlay(fact.authority, fact.semantic)
        if not confirm_write_permitted(policy, grants):
            return make_decision(
                fact,
                evidence=evidence,
                action="WITHHOLD_CONFIRM_NO_WRITE_PERMISSION",
                reason="CONFIRM_REQUIRES_WRITE_PERMISSION",
                publish=False,
                reconcile=reconcile,
                operation="NONE",
                success=False,
                branch="CONFIRM_UNPERMITTED",
            )
        reason = fence_reason_for_committed(fact, policy, reason)
        branch = "CONFIRM_FENCED" if reason != historical_overlay(fact.authority, fact.semantic)[0] else "CONFIRM_EXISTING"
        return make_decision(
            fact,
            evidence=evidence,
            action="CONFIRM_EXISTING_TARGET",
            reason=reason,
            publish=False,
            reconcile=reconcile,
            operation="CONFIRM_HISTORICAL",
            success=success,
            branch=branch,
        )
    if evidence == "SEALED_ONLY":
        return decide_sealed_only(fact, policy, grants, evidence)
    return make_decision(
        fact,
        evidence="INCONSISTENT",
        action="HOLD_INCONSISTENT",
        reason="UNCLASSIFIED_COMBINATION",
        publish=False,
        reconcile=True,
        operation="NONE",
        success=False,
        branch="UNCLASSIFIED",
    )


def decide_sealed_only(fact: Fact, policy: Policy, grants: Grants, evidence: str) -> Decision:
    if fact.semantic == "INVALID":
        return make_decision(
            fact,
            evidence=evidence,
            action="WITHHOLD_SUPERSEDED",
            reason="SEMANTIC_INVALID",
            publish=False,
            reconcile=True,
            operation="NONE",
            success=False,
            branch="SEALED_INVALID",
        )
    if fact.semantic == "UNKNOWN":
        return make_decision(
            fact,
            evidence=evidence,
            action="WITHHOLD_UNKNOWN",
            reason="SEMANTIC_UNKNOWN_NOT_FAILURE",
            publish=False,
            reconcile=True,
            operation="NONE",
            success=False,
            branch="SEALED_SEMANTIC_UNKNOWN",
        )
    if fact.authority == "UNKNOWN":
        return make_decision(
            fact,
            evidence=evidence,
            action="WITHHOLD_UNKNOWN",
            reason="AUTHORITY_UNKNOWN_NOT_FAILURE",
            publish=False,
            reconcile=True,
            operation="NONE",
            success=False,
            branch="SEALED_AUTHORITY_UNKNOWN",
        )
    if fact.authority == "SUPERSEDED":
        if fact.sink == "VERSION_FENCED":
            if policy.revocation_of_sent == "FENCE_AT_COMMIT_BOUNDARY":
                reason = "FENCE_AT_COMMIT_BOUNDARY"
                branch = "SEALED_FENCE_REJECTS_UNCOMMITTED"
            else:
                reason = "SENT_IMMUNE_DOES_NOT_COVER_UNCOMMITTED"
                branch = "SEALED_SENT_IMMUNE_STILL_UNCOMMITTED"
        else:
            reason = "AUTHORITY_SUPERSEDED"
            branch = "SEALED_AUTHORITY_SUPERSEDED"
        return make_decision(
            fact,
            evidence=evidence,
            action="WITHHOLD_STALE_AUTHORITY",
            reason=reason,
            publish=False,
            reconcile=False,
            operation="NONE",
            success=False,
            branch=branch,
        )
    if fact.sink == "OPAQUE":
        return make_decision(
            fact,
            evidence=evidence,
            action="WITHHOLD_OPAQUE_UNCONFIRMED",
            reason="OPAQUE_SINK_CANNOT_PROVE_ABSENCE",
            publish=False,
            reconcile=True,
            operation="NONE",
            success=False,
            branch="SEALED_OPAQUE",
        )
    if not dispatch_permitted(fact, policy, grants):
        return make_decision(
            fact,
            evidence=evidence,
            action="WITHHOLD_NO_DISPATCH_PERMISSION",
            reason="DISPATCH_PERMISSION_ABSENT",
            publish=False,
            reconcile=False,
            operation="NONE",
            success=False,
            branch="SEALED_DISPATCH_DENIED",
        )
    return make_decision(
        fact,
        evidence=evidence,
        action="PUBLISH_SEALED_BYTES",
        reason="NONE",
        publish=True,
        reconcile=False,
        operation="PRODUCE_NEW_EFFECT",
        success=False,
        branch="SEALED_PUBLISH",
        recovery_history="RESTORED_BYTES",
        physical_label="PRESENT_UNVERIFIED",
    )


def apply_transition(fact: Fact, decision: Decision) -> Fact:
    if decision.recovery_action != PUBLISH_ACTION:
        return fact
    return Fact(
        sealed=True,
        target=True,
        receipt=False,
        receipt_digest="NOT_APPLICABLE",
        effect_key="MATCH",
        payload_hash="MATCH",
        authority=fact.authority,
        semantic=fact.semantic,
        sink=fact.sink,
    )


def check_transition(fact: Fact, decision: Decision, policy: Policy, grants: Grants) -> list[tuple[str, str]]:
    problems: list[tuple[str, str]] = []
    evidence = decision.evidence_state
    post = apply_transition(fact, decision)
    pre_effects = logical_effects(fact, evidence)

    if not decision.outputs_defined():
        problems.append(("UNDEFINED", "missing_output"))

    if decision.authority_state != fact.authority or decision.semantic_state != fact.semantic:
        problems.append(("I3", "dimension_collapsed"))

    if decision.successor_failure:
        problems.append(("I3", "unknown_or_stop_marked_failure"))

    if decision.publish_allowed != (decision.recovery_action == PUBLISH_ACTION):
        problems.append(("I5", "publish_flag_disagrees_with_action"))

    if decision.recovery_action == PUBLISH_ACTION:
        if pre_effects != 0:
            problems.append(("I1", "publish_with_existing_logical_effect"))
        if logical_effects(post) != 1:
            problems.append(("I1", "publish_did_not_yield_one_effect"))
        if post == fact:
            problems.append(("I1", "publish_wrote_nothing"))
        if decision.operation != "PRODUCE_NEW_EFFECT":
            problems.append(("I9", "publish_not_new_effect"))
        if decision.recovery_history != "RESTORED_BYTES":
            problems.append(("I9", "publish_history_not_restored_bytes"))
        if decision.physical_label == "CONFIRMED_EFFECT" or decision.treated_as_success:
            problems.append(("I9", "restored_bytes_treated_as_confirmed_effect"))
        if not dispatch_permitted(fact, policy, grants):
            problems.append(("I5", "publish_without_dispatch_permission"))
        if fact.authority != "VALID" or fact.semantic != "VALID" or evidence != "SEALED_ONLY":
            problems.append(("I5", "publish_outside_safety_preconditions"))
        if fact.sink == "OPAQUE":
            problems.append(("I1", "publish_to_opaque_sink"))
        if fact.sink == "VERSION_FENCED" and fact.authority != "VALID":
            problems.append(("I7", "fence_not_applied_at_commit_boundary"))
    else:
        if post != fact:
            invariant = "I6" if evidence in {"TARGET_CONFIRMED", "RECEIPT_CONFIRMED"} else "I2"
            problems.append((invariant, "mutation_without_publish"))
        if decision.operation == "PRODUCE_NEW_EFFECT":
            problems.append(("I9", "new_effect_without_publish"))

    if fact.target and fact.payload_hash == "MISMATCH":
        if decision.publish_allowed or post.payload_hash != fact.payload_hash or post.target != fact.target:
            problems.append(("I2", "different_payload_overwritten"))
    if fact.target and fact.effect_key == "MISMATCH":
        if decision.publish_allowed or post != fact:
            problems.append(("I2", "foreign_key_overwritten"))

    unknown_dimension = fact.authority == "UNKNOWN" or fact.semantic == "UNKNOWN" or evidence == "UNKNOWN"
    if unknown_dimension and decision.publish_allowed:
        problems.append(("I3", "unknown_automatic_resend"))
    if fact.semantic == "INVALID" and decision.publish_allowed:
        problems.append(("I4", "invalid_published"))

    if evidence in {"TARGET_CONFIRMED", "RECEIPT_CONFIRMED"}:
        if decision.publish_allowed or post != fact:
            problems.append(("I6", "confirmed_target_rewritten"))
        if decision.recovery_action == PUBLISH_ACTION:
            problems.append(("I6", "confirmed_target_published_again"))

    if fact.sink == "VERSION_FENCED" and fact.authority == "SUPERSEDED" and not fact.target:
        if decision.publish_allowed:
            problems.append(("I7", "superseded_publish_before_commit_boundary"))

    if evidence in {"INCONSISTENT", "CONFLICT", "UNKNOWN"}:
        if decision.treated_as_success or decision.publish_allowed:
            problems.append(("I8", "inconsistent_or_unknown_treated_as_success"))
        if decision.recovery_action in CONFIRM_ACTIONS | {PUBLISH_ACTION}:
            problems.append(("I8", "inconsistent_confirmation_action"))

    if decision.operation == "PRODUCE_NEW_EFFECT" and decision.operation == "CONFIRM_HISTORICAL":
        problems.append(("I9", "operation_alias"))
    if decision.recovery_action in CONFIRM_ACTIONS and decision.operation != "CONFIRM_HISTORICAL":
        problems.append(("I9", "confirm_action_not_historical"))
    if decision.recovery_action == PUBLISH_ACTION and decision.operation != "PRODUCE_NEW_EFFECT":
        problems.append(("I9", "publish_action_not_new"))
    if decision.recovery_history == "RESTORED_BYTES" and decision.physical_label == "CONFIRMED_EFFECT":
        problems.append(("I9", "restored_equals_confirmed"))

    if decision.recovery_action == PUBLISH_ACTION:
        second = decide(post, policy, grants)
        second_post = apply_transition(post, second)
        if second.publish_allowed or second.recovery_action == PUBLISH_ACTION:
            problems.append(("I1", "second_recovery_published_again"))
        if second.evidence_state != "TARGET_CONFIRMED":
            problems.append(("I6", "publish_successor_not_target_confirmed"))
        if second_post != post:
            problems.append(("I6", "second_recovery_rewrote_target"))
        if second.operation == "PRODUCE_NEW_EFFECT":
            problems.append(("I9", "second_step_produced_another_effect"))
        if logical_effects(post) != 1 or logical_effects(second_post) != 1:
            problems.append(("I1", "effect_count_after_publish"))
    return problems


def prose_clause(fact: Fact) -> str | None:
    """Week 4 prose predicates, applied only to realizable byte facts.

    Semantic INVALID is not part of the byte clause. The prose listed it as a
    separate RSR outcome on the sealed-only durable condition.
    """
    if not is_realizable(fact):
        return None
    if fact.sealed and fact.target and fact.effect_key == "MATCH" and fact.payload_hash == "MISMATCH":
        return "CONFLICT"
    if (
        fact.receipt
        and fact.sealed
        and fact.target
        and fact.effect_key == "MATCH"
        and fact.payload_hash == "MATCH"
        and fact.receipt_digest == "MATCH"
    ):
        return "REPLAY_RECEIPT"
    if (
        fact.target
        and fact.sealed
        and fact.effect_key == "MATCH"
        and fact.payload_hash == "MATCH"
        and not fact.receipt
    ):
        return "CONFIRM_TARGET"
    if fact.sealed and not fact.target and not fact.receipt:
        return "PUBLISH_SEALED"
    if not fact.sealed and not fact.target and not fact.receipt:
        return "UNKNOWN"
    return None


PROSE_TO_EVIDENCE = {
    "CONFLICT": "CONFLICT",
    "REPLAY_RECEIPT": "RECEIPT_CONFIRMED",
    "CONFIRM_TARGET": "TARGET_CONFIRMED",
    "PUBLISH_SEALED": "SEALED_ONLY",
    "UNKNOWN": "UNKNOWN",
}


def naive_week4(fact: Fact) -> tuple[str, str, bool]:
    """`fault_replay.kill_child.run_reconcile` order, without a semantic or authority gate.

    One target path. The code compares digests, not a separate effect-key flag.
    """
    if fact.sealed and fact.target and fact.payload_hash == "MISMATCH":
        return "CONFLICT", "HOLD_CONFLICT", False
    if (
        fact.receipt
        and fact.sealed
        and fact.target
        and fact.receipt_digest == "MATCH"
        and fact.payload_hash == "MATCH"
    ):
        return "REPLAY_RECEIPT", "REPLAY_STORED_RECEIPT", False
    if fact.target and fact.sealed and fact.payload_hash == "MATCH" and not fact.receipt:
        return "CONFIRM_TARGET", "CONFIRM_EXISTING_TARGET", False
    if fact.sealed and not fact.target and not fact.receipt:
        return "PUBLISH_SEALED", "PUBLISH_SEALED_BYTES", True
    return "UNKNOWN", "WITHHOLD_UNSEALED", False


def naive_failures(fact: Fact) -> list[str]:
    _label, action, publishes = naive_week4(fact)
    failed: list[str] = []
    if publishes and fact.semantic == "INVALID":
        failed.append("I4")
    if publishes and (fact.semantic == "UNKNOWN" or fact.authority == "UNKNOWN"):
        failed.append("I3")
    if publishes:
        failed.append("I5")
    if publishes and fact.sink == "OPAQUE":
        failed.append("I1")
    if publishes and fact.sink == "VERSION_FENCED" and fact.authority == "SUPERSEDED":
        failed.append("I7")
    if publishes and fact.authority == "SUPERSEDED":
        failed.append("I5")
    if action in {"CONFIRM_EXISTING_TARGET", "REPLAY_STORED_RECEIPT"} and fact.effect_key == "MISMATCH":
        failed.append("I8")
    return list(dict.fromkeys(failed))


def fact_text(fact: Fact) -> str:
    return (
        f"sealed={str(fact.sealed).lower()} target={str(fact.target).lower()} "
        f"receipt={str(fact.receipt).lower()} receipt_digest={fact.receipt_digest} "
        f"effect_key={fact.effect_key} payload_hash={fact.payload_hash} "
        f"authority={fact.authority} semantic={fact.semantic} sink={fact.sink}"
    )


def check_historical_interpretations() -> list[str]:
    problems: list[str] = []
    interpretations = {
        "UNKNOWN": {"failure": False, "auto_retry": False},
        "CORRECT_STOP": {"failure": False, "successor_failure": False},
        "NOT_APPLICABLE": {"failure": False},
        "RESTORED_BYTES": {"confirmed_effect": False},
        "PUBLISH_SEALED": {"evidence": "SEALED_ONLY", "equals_target_confirmed": False},
        "CONFLICT": {"ordered_between_unknown_and_valid": False},
    }
    if interpretations["UNKNOWN"]["failure"] or interpretations["UNKNOWN"]["auto_retry"]:
        problems.append("UNKNOWN marked as failure or automatic retry")
    if interpretations["CORRECT_STOP"]["failure"] or interpretations["CORRECT_STOP"]["successor_failure"]:
        problems.append("CORRECT_STOP marked as successor failure")
    if interpretations["NOT_APPLICABLE"]["failure"]:
        problems.append("NOT_APPLICABLE marked as failure")
    if interpretations["RESTORED_BYTES"]["confirmed_effect"]:
        problems.append("RESTORED_BYTES implies CONFIRMED_EFFECT")
    if interpretations["PUBLISH_SEALED"]["evidence"] == "TARGET_CONFIRMED":
        problems.append("PUBLISH_SEALED mapped to TARGET_CONFIRMED")
    if interpretations["PUBLISH_SEALED"]["equals_target_confirmed"]:
        problems.append("PUBLISH_SEALED treated as target confirmation")
    if interpretations["CONFLICT"]["ordered_between_unknown_and_valid"]:
        problems.append("CONFLICT placed on an order between UNKNOWN and VALID")
    if EVIDENCE_ORDER is not None:
        problems.append("evidence order table is present")
    missing = [name for name in HISTORICAL_NAMES if not HISTORICAL_NAMES[name]]
    if missing:
        problems.append("blank historical mapping: " + ",".join(missing))
    return problems


def run_specimens(policies: list[Policy], grants_list: list[Grants]) -> list[dict[str, object]]:
    by_key = {(policy.as_dict()["crashed_continuation"], policy.claim_consumes_authorization, policy.republish_requires_new_dispatch, policy.revocation_of_sent, policy.confirm_requires_write_permission, policy.reconcile_requires_permission): policy for policy in policies}
    no_grants = Grants(False, False, False)
    new_dispatch = Grants(True, False, False)
    write_grant = Grants(False, True, False)

    def policy_of(continuation: str, consumes: bool, republish: bool, revocation: str = "FENCE_AT_COMMIT_BOUNDARY", confirm_write: bool = False, reconcile: bool = False) -> Policy:
        return by_key[(continuation, consumes, republish, revocation, confirm_write, reconcile)]

    sealed = Fact(True, False, False, "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "VALID", "VALID", "IDEMPOTENT_ONLY")
    cases: list[tuple[str, Decision, str, bool]] = []

    allowed = decide(sealed, policy_of("OLD_GRANT_ALLOWED", False, False), no_grants)
    cases.append(("old_grant_publishes", allowed, "PUBLISH_SEALED_BYTES", True))

    denied = decide(sealed, policy_of("OLD_GRANT_DENIED", False, False), no_grants)
    cases.append(("continuation_denied_without_new_grant", denied, "WITHHOLD_NO_DISPATCH_PERMISSION", False))

    explicit = decide(sealed, policy_of("OLD_GRANT_DENIED", False, False), new_dispatch)
    cases.append(("explicit_new_dispatch", explicit, "PUBLISH_SEALED_BYTES", True))

    consumed = decide(sealed, policy_of("OLD_GRANT_ALLOWED", True, False), no_grants)
    cases.append(("claim_consumes_blocks_old_grant", consumed, "WITHHOLD_NO_DISPATCH_PERMISSION", False))

    republish_needs = decide(sealed, policy_of("OLD_GRANT_ALLOWED", False, True), no_grants)
    cases.append(("republish_requires_grant_absent", republish_needs, "WITHHOLD_NO_DISPATCH_PERMISSION", False))

    republish_has = decide(sealed, policy_of("OLD_GRANT_DENIED", True, True), new_dispatch)
    cases.append(("republish_grant_present", republish_has, "PUBLISH_SEALED_BYTES", True))

    invalid = Fact(True, False, False, "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "VALID", "INVALID", "IDEMPOTENT_ONLY")
    invalid_decision = decide(invalid, policy_of("OLD_GRANT_ALLOWED", False, False), new_dispatch)
    cases.append(("invalid_does_not_publish", invalid_decision, "WITHHOLD_SUPERSEDED", False))

    unknown_sem = Fact(True, False, False, "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "VALID", "UNKNOWN", "QUERYABLE")
    unknown_decision = decide(unknown_sem, policy_of("OLD_GRANT_ALLOWED", False, False), new_dispatch)
    cases.append(("semantic_unknown_does_not_resend", unknown_decision, "WITHHOLD_UNKNOWN", False))

    opaque = Fact(True, False, False, "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "VALID", "VALID", "OPAQUE")
    opaque_decision = decide(opaque, policy_of("OLD_GRANT_ALLOWED", False, False), new_dispatch)
    cases.append(("opaque_does_not_publish", opaque_decision, "WITHHOLD_OPAQUE_UNCONFIRMED", False))

    fenced = Fact(True, False, False, "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "SUPERSEDED", "VALID", "VERSION_FENCED")
    fence_decision = decide(fenced, policy_of("OLD_GRANT_ALLOWED", False, False, "SENT_IMMUNE"), new_dispatch)
    cases.append(("sent_immune_does_not_publish_uncommitted", fence_decision, "WITHHOLD_STALE_AUTHORITY", False))

    failures: list[dict[str, object]] = []
    for name, decision, action, publish in cases:
        if decision.recovery_action != action or decision.publish_allowed != publish or decision.successor_failure:
            failures.append(
                {
                    "specimen": name,
                    "expected_action": action,
                    "expected_publish": publish,
                    "actual_action": decision.recovery_action,
                    "actual_publish": decision.publish_allowed,
                    "reason": decision.rejection_reason,
                }
            )
    if allowed.recovery_history != "RESTORED_BYTES" or allowed.physical_label == "CONFIRMED_EFFECT" or allowed.treated_as_success:
        failures.append({"specimen": "restored_bytes_not_confirmed_effect", "actual": allowed.physical_label})
    post = apply_transition(sealed, allowed)
    second = decide(post, policy_of("OLD_GRANT_ALLOWED", False, False), no_grants)
    if second.evidence_state != "TARGET_CONFIRMED" or second.publish_allowed or second.recovery_action == PUBLISH_ACTION:
        failures.append(
            {
                "specimen": "second_step_after_publish",
                "evidence": second.evidence_state,
                "action": second.recovery_action,
                "publish": second.publish_allowed,
            }
        )

    receipt_without_target = Fact(True, False, True, "MATCH", "MATCH", "NOT_APPLICABLE", "VALID", "VALID", "QUERYABLE")
    receipt_decision = decide(receipt_without_target, policy_of("OLD_GRANT_ALLOWED", False, False), Grants(True, True, True))
    if (
        receipt_decision.evidence_state != "INCONSISTENT"
        or receipt_decision.rejection_reason != "RECEIPT_WITHOUT_TARGET"
        or receipt_decision.publish_allowed
        or receipt_decision.treated_as_success
    ):
        failures.append({"specimen": "receipt_without_target", "actual": receipt_decision.rejection_reason, "evidence": receipt_decision.evidence_state})

    disagree = Fact(True, True, True, "MISMATCH", "MATCH", "MATCH", "VALID", "VALID", "IDEMPOTENT_ONLY")
    disagree_decision = decide(disagree, policy_of("OLD_GRANT_ALLOWED", False, False), new_dispatch)
    if (
        disagree_decision.evidence_state != "INCONSISTENT"
        or disagree_decision.evidence_state == "RECEIPT_CONFIRMED"
        or disagree_decision.treated_as_success
        or disagree_decision.publish_allowed
    ):
        failures.append({"specimen": "receipt_disagrees", "evidence": disagree_decision.evidence_state, "reason": disagree_decision.rejection_reason})

    target_only = Fact(False, True, False, "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "VALID", "VALID", "VERSION_FENCED")
    target_only_decision = decide(target_only, policy_of("OLD_GRANT_ALLOWED", False, False), write_grant)
    if target_only_decision.evidence_state != "INCONSISTENT" or target_only_decision.rejection_reason != "TARGET_WITHOUT_SEALED" or target_only_decision.publish_allowed:
        failures.append({"specimen": "target_without_sealed", "evidence": target_only_decision.evidence_state, "reason": target_only_decision.rejection_reason})

    conflict = Fact(True, True, False, "NOT_APPLICABLE", "MATCH", "MISMATCH", "UNKNOWN", "INVALID", "OPAQUE")
    conflict_decision = decide(conflict, policy_of("OLD_GRANT_ALLOWED", False, False), new_dispatch)
    if (
        conflict_decision.evidence_state != "CONFLICT"
        or conflict_decision.recovery_action != "HOLD_CONFLICT"
        or conflict_decision.publish_allowed
        or conflict_decision.treated_as_success
        or conflict_decision.authority_state != "UNKNOWN"
        or conflict_decision.successor_failure
    ):
        failures.append({"specimen": "same_key_different_payload", "evidence": conflict_decision.evidence_state, "action": conflict_decision.recovery_action})

    nothing = Fact(False, False, False, "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "UNKNOWN", "VALID", "QUERYABLE")
    nothing_decision = decide(nothing, policy_of("OLD_GRANT_ALLOWED", False, False), new_dispatch)
    if (
        nothing_decision.evidence_state != "UNKNOWN"
        or nothing_decision.publish_allowed
        or nothing_decision.successor_failure
        or nothing_decision.recovery_action != "WITHHOLD_UNSEALED"
    ):
        failures.append({"specimen": "unknown_is_not_failure", "action": nothing_decision.recovery_action})

    for policy in policies:
        for grants in grants_list:
            probed = decide(sealed, policy, grants)
            if probed.publish_allowed and not dispatch_permitted(sealed, policy, grants):
                failures.append({"specimen": "publish_permission_drift", "policy": policy.as_dict(), "grants": grants.as_dict()})
                break
        else:
            continue
        break
    return failures


def independent_publish_count() -> int:
    """9 of 16 permission combinations, each copied across the 5 bits that do not affect dispatch."""
    matched = 0
    for consumes, allowed, republish, grant in itertools.product((False, True), repeat=4):
        if dispatch_permitted_independent(consumes, allowed, republish, grant):
            matched += 1
    return matched * 32


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def write_csv(path: Path, facts: list[Fact], table: Counter[tuple[object, ...]]) -> int:
    fieldnames = [
        "sealed_present",
        "target_present",
        "receipt_present",
        "receipt_digest_match",
        "effect_key_match",
        "payload_hash_match",
        "authority",
        "semantic_oracle",
        "sink_contract",
        "evidence_state",
        "authority_state",
        "semantic_state",
        "recovery_action",
        "publish_allowed",
        "requires_reconciliation",
        "rejection_reason",
        "operation",
        "treated_as_success",
        "successor_failure",
        "recovery_history",
        "physical_label",
        "week4_collapsed_label",
        "branch",
        "assignment_count",
    ]
    rows: list[dict[str, object]] = []
    for key, count in table.items():
        fact_index = int(key[0])
        fact = facts[fact_index]
        rows.append(
            {
                "sealed_present": bool_text(fact.sealed),
                "target_present": bool_text(fact.target),
                "receipt_present": bool_text(fact.receipt),
                "receipt_digest_match": fact.receipt_digest,
                "effect_key_match": fact.effect_key,
                "payload_hash_match": fact.payload_hash,
                "authority": fact.authority,
                "semantic_oracle": fact.semantic,
                "sink_contract": fact.sink,
                "evidence_state": key[1],
                "authority_state": key[2],
                "semantic_state": key[3],
                "recovery_action": key[4],
                "publish_allowed": bool_text(bool(key[5])),
                "requires_reconciliation": bool_text(bool(key[6])),
                "rejection_reason": key[7],
                "operation": key[8],
                "treated_as_success": bool_text(bool(key[9])),
                "successor_failure": bool_text(bool(key[10])),
                "recovery_history": key[11],
                "physical_label": key[12],
                "week4_collapsed_label": key[13],
                "branch": key[14],
                "assignment_count": count,
            }
        )
    rows.sort(
        key=lambda row: (
            row["sealed_present"],
            row["target_present"],
            row["receipt_present"],
            row["receipt_digest_match"],
            row["effect_key_match"],
            row["payload_hash_match"],
            row["authority"],
            row["semantic_oracle"],
            row["sink_contract"],
            row["branch"],
        )
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def write_counterexamples(
    path: Path,
    *,
    adopted: list[dict[str, object]],
    specimens: list[dict[str, object]],
    historical: list[str],
    naive_rows: list[str],
    collapse_rows: list[str],
    sent_immune_rows: list[str],
    invariant_status: dict[str, str],
) -> None:
    unmet = [name for name, status in invariant_status.items() if status != "PASS"]
    lines = [
        "# Week 5 恢复模型反例",
        "",
        "检查器是 `mechanism_validation/formal/state_space_checker.py`。本文件由该检查器写出。TLC 没有运行。",
        "",
        "采用的决策函数如果违反不变量，违例留在这里，对应不变量标为 UNMET。未采纳的读法即使已经被决策函数排除，也保留在后面，不删行。",
        "",
        "## 采用的决策函数",
        "",
        f"不变量：{', '.join(f'{name}={status}' for name, status in invariant_status.items())}。",
        f"未满足：{', '.join(unmet) if unmet else '无'}。",
        f"采用的决策函数反例数：{len(adopted)}。",
        f"标本断言失败数：{len(specimens)}。",
        f"历史解释断言失败数：{len(historical)}。",
        "",
    ]
    if not adopted and not specimens and not historical:
        lines.append("本次穷举没有留下采用的决策函数的反例。下面的反例属于未采纳的读法。")
        lines.append("")
    for item in adopted:
        lines.append(
            f"- status=UNMET invariant={item['invariant']} detail={item['detail']} branch={item['branch']} {item['fact']}"
        )
    for item in specimens:
        lines.append(f"- status=UNMET reading=specimen {json.dumps(item, ensure_ascii=False, sort_keys=True)}")
    for item in historical:
        lines.append(f"- status=UNMET reading=historical_interpretation {item}")
    lines.extend(
        [
            "",
            "## 未采纳读法：无门闩的 Week 4 发布",
            "",
            "`fault_replay.kill_child.run_reconcile` 在密封字节存在、目标缺失、回执缺失时直接发布，不看语义、权威和派发许可。历史矩阵里的那些行保持原名，不回写。下面每一行是这个读法在可实现事实上违反 I1–I9 的组合。采用的决策函数不走这个分支。",
            "",
            f"行数：{len(naive_rows)}。",
            "",
        ]
    )
    lines.extend(naive_rows)
    lines.extend(
        [
            "",
            "## 未采纳读法：SENT_IMMUNE 允许未提交的发布",
            "",
            "已声明的 `SENT_IMMUNE` 只表示已经越过目标提交边界的效果不被改写。把它读成“权威已经 SUPERSEDED 仍可发布密封字节”会跳过提交边界上的版本栅栏。下面是这个读法的违例。采用的参数不采用这个读法。",
            "",
            f"行数：{len(sent_immune_rows)}。",
            "",
        ]
    )
    lines.extend(sent_immune_rows)
    lines.extend(
        [
            "",
            "## 未采纳读法：Week 4 else 分支把不一致收成 UNKNOWN",
            "",
            "散文里的 UNKNOWN 是密封、目标、回执都缺失。`run_reconcile` 的 else 还把回执与密封不一致、有回执没有目标、有目标没有密封收成 UNKNOWN。这些组合在 Week 5 里是 INCONSISTENT，不是成功，也不是自动重发。历史结果文件里的 UNKNOWN 行不改名。下面每一行是 else 分支与采用的证据状态不一致、且采用的状态不是成功的组合。它们不是采用的决策函数的 UNMET。",
            "",
            f"行数：{len(collapse_rows)}。",
            "",
        ]
    )
    lines.extend(collapse_rows)
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    started = time.perf_counter()
    facts = all_facts()
    policies = all_policies()
    grants_list = all_grants()
    expected_facts = 2 * 2 * 2 * 3 * 3 * 3 * 3 * 3 * 4
    expected_policies = 64
    expected_grants = 8
    if len(facts) != expected_facts or len(policies) != expected_policies or len(grants_list) != expected_grants:
        raise SystemExit(
            f"cartesian size facts={len(facts)} policies={len(policies)} grants={len(grants_list)}"
        )

    table: Counter[tuple[object, ...]] = Counter()
    adopted: list[dict[str, object]] = []
    undefined = 0
    unclassified = 0
    evaluations = 0
    transitions = 0
    publish_true = 0
    evidence_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    invariant_hits: Counter[str] = Counter()
    policy_publish: Counter[str] = Counter()
    unknown_coexisting = 0
    unknown_coexisting_publish = 0
    realizable = 0
    actions_by_fact: list[set[str]] = [set() for _ in facts]

    for fact_index, fact in enumerate(facts):
        if is_realizable(fact):
            realizable += 1
        for policy in policies:
            for grants in grants_list:
                try:
                    decision = decide(fact, policy, grants)
                except Exception as exc:  # enumerator must count crashes as undefined
                    undefined += 1
                    adopted.append(
                        {
                            "invariant": "UNDEFINED",
                            "detail": f"exception:{type(exc).__name__}",
                            "branch": "EXCEPTION",
                            "fact": fact_text(fact),
                        }
                    )
                    continue
                evaluations += 1
                if not decision.outputs_defined():
                    undefined += 1
                if decision.branch == "UNCLASSIFIED" or decision.rejection_reason == "UNCLASSIFIED_COMBINATION":
                    unclassified += 1
                    undefined += 1
                problems = check_transition(fact, decision, policy, grants)
                transitions += 1
                if problems:
                    for invariant, detail in problems:
                        invariant_hits[invariant] += 1
                        if len(adopted) < 20000:
                            adopted.append(
                                {
                                    "invariant": invariant,
                                    "detail": detail,
                                    "branch": decision.branch,
                                    "fact": fact_text(fact),
                                    "policy": policy.as_dict(),
                                    "grants": grants.as_dict(),
                                }
                            )
                if decision.publish_allowed:
                    publish_true += 1
                    policy_publish[json.dumps(policy.as_dict(), sort_keys=True)] += 1
                if fact.authority == "UNKNOWN" or fact.semantic == "UNKNOWN" or decision.evidence_state == "UNKNOWN":
                    unknown_coexisting += 1
                    if decision.publish_allowed:
                        unknown_coexisting_publish += 1
                evidence_counts[decision.evidence_state] += 1
                action_counts[decision.recovery_action] += 1
                actions_by_fact[fact_index].add(decision.recovery_action)
                table_key = (
                    fact_index,
                    decision.evidence_state,
                    decision.authority_state,
                    decision.semantic_state,
                    decision.recovery_action,
                    decision.publish_allowed,
                    decision.requires_reconciliation,
                    decision.rejection_reason,
                    decision.operation,
                    decision.treated_as_success,
                    decision.successor_failure,
                    decision.recovery_history,
                    decision.physical_label,
                    decision.week4_collapsed_label,
                    decision.branch,
                )
                table[table_key] += 1

    assignment_total = sum(table.values())
    per_fact_assignments: Counter[int] = Counter()
    for key, count in table.items():
        per_fact_assignments[int(key[0])] += count
    if assignment_total != expected_facts * expected_policies * expected_grants:
        undefined += abs(expected_facts * expected_policies * expected_grants - assignment_total)
    if len(per_fact_assignments) != len(facts) or any(count != expected_policies * expected_grants for count in per_fact_assignments.values()):
        undefined += 1

    specimens = run_specimens(policies, grants_list)
    historical = check_historical_interpretations()
    prose_mismatches: list[str] = []
    for fact in facts:
        clause = prose_clause(fact)
        if clause is None:
            continue
        evidence, _reason = classify_evidence(fact)
        expected = PROSE_TO_EVIDENCE[clause]
        if evidence != expected or (clause == "PUBLISH_SEALED" and evidence == "TARGET_CONFIRMED"):
            prose_mismatches.append(f"{clause} -> {evidence} | {fact_text(fact)}")
    if prose_mismatches:
        historical.extend(prose_mismatches[:50])
        undefined += len(prose_mismatches)

    expected_dispatch = independent_publish_count()
    clean_publish_facts = [
        fact
        for fact in facts
        if fact.sealed
        and not fact.target
        and not fact.receipt
        and fact.receipt_digest == "NOT_APPLICABLE"
        and fact.effect_key == "NOT_APPLICABLE"
        and fact.payload_hash == "NOT_APPLICABLE"
        and fact.authority == "VALID"
        and fact.semantic == "VALID"
        and fact.sink != "OPAQUE"
    ]
    per_fact_publish = []
    for fact in clean_publish_facts:
        count = sum(1 for policy in policies for grants in grants_list if decide(fact, policy, grants).publish_allowed)
        per_fact_publish.append(count)
        if count != expected_dispatch:
            specimens.append(
                {
                    "specimen": "dispatch_count",
                    "sink": fact.sink,
                    "actual": count,
                    "expected": expected_dispatch,
                }
            )

    naive_rows: list[str] = []
    collapse_rows: list[str] = []
    sent_immune_rows: list[str] = []
    for fact_index, fact in enumerate(facts):
        if not is_realizable(fact):
            continue
        label, action, publishes = naive_week4(fact)
        failed = naive_failures(fact)
        adopted_actions = ",".join(sorted(actions_by_fact[fact_index]))
        if failed:
            naive_rows.append(
                f"- status=RETAINED_NOT_ADOPTED reading=unguarded_week4_publish invariants={'+'.join(failed)} "
                f"naive_evidence={label} naive_action={action} adopted_actions={adopted_actions} | {fact_text(fact)}"
            )
        if publishes and fact.authority == "SUPERSEDED" and fact.semantic == "VALID":
            sent_hits = ["I5"]
            if fact.sink == "VERSION_FENCED":
                sent_hits.append("I7")
            if fact.sink == "OPAQUE":
                sent_hits.append("I1")
            sent_immune_rows.append(
                f"- status=RETAINED_NOT_ADOPTED reading=sent_immune_authorizes_uncommitted_publish "
                f"invariants={'+'.join(sent_hits)} adopted_actions={adopted_actions} | {fact_text(fact)}"
            )
        adopted_evidence, adopted_reason = classify_evidence(fact)
        if label == "UNKNOWN" and adopted_evidence == "INCONSISTENT":
            collapse_rows.append(
                f"- status=RETAINED_NOT_ADOPTED reading=week4_else_branch defect=INCONSISTENT_COLLAPSED_TO_UNKNOWN "
                f"adopted_invariant_status=NOT_UNMET adopted_evidence=INCONSISTENT adopted_reason={adopted_reason} "
                f"naive_action={action} | {fact_text(fact)}"
            )

    invariant_status = {name: ("UNMET" if invariant_hits[name] or (name == "I3" and unknown_coexisting_publish) else "PASS") for name in INVARIANT_IDS}
    if specimens or historical or unclassified or undefined:
        for name in INVARIANT_IDS:
            if invariant_status[name] == "PASS" and any(
                str(item.get("invariant")) == name for item in adopted
            ):
                invariant_status[name] = "UNMET"

    results = {
        "checker": "mechanism_validation/formal/state_space_checker.py",
        "model_checker": "python_exhaustive_enumerator",
        "tlc_ran": False,
        "tlc_available": False,
        "tla_checked": False,
        "command": COMMAND,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "undefined_combination_count": undefined,
        "unclassified_combination_count": unclassified,
        "fact_combination_count": len(facts),
        "realizable_fact_count": realizable,
        "encoding_inconsistent_fact_count": len(facts) - realizable,
        "policy_count": len(policies),
        "grant_combination_count": len(grants_list),
        "states_checked": evaluations,
        "transitions_checked": transitions,
        "second_step_checked_on_publish": publish_true,
        "assignment_count_in_decision_table": assignment_total,
        "decision_table_rows": None,
        "invariant_results": invariant_status,
        "unmet_invariants": [name for name, status in invariant_status.items() if status != "PASS"],
        "invariant_hit_counts": {name: invariant_hits[name] for name in INVARIANT_IDS},
        "adopted_counterexample_count": len(adopted) + len(specimens) + len(historical),
        "rejected_reading_counterexample_count": len(naive_rows) + len(sent_immune_rows) + len(collapse_rows),
        "unguarded_week4_publish_counterexamples": len(naive_rows),
        "sent_immune_uncommitted_publish_counterexamples": len(sent_immune_rows),
        "else_branch_collapse_counterexamples": len(collapse_rows),
        "publish_allowed_true_count": publish_true,
        "publish_allowed_per_clean_non_opaque_fact": per_fact_publish,
        "independent_dispatch_assignments_per_clean_fact": expected_dispatch,
        "unknown_coexisting_evaluations": unknown_coexisting,
        "unknown_coexisting_publish_count": unknown_coexisting_publish,
        "evidence_counts": dict(evidence_counts),
        "action_counts": dict(action_counts),
        "policies_that_publish": len(policy_publish),
        "authorization_parameterized": True,
        "authorization_chosen_to_favor_claim": False,
        "dead_process_resumes": False,
        "authorization_parameters": [
            "claim_consumes_authorization",
            "crashed_continuation",
            "revocation_of_sent",
            "confirm_requires_write_permission",
            "republish_requires_new_dispatch",
            "reconcile_requires_permission",
        ],
        "permission_grants": [
            "new_dispatch_granted",
            "write_permission_granted",
            "reconcile_permission_granted",
        ],
        "observed_code_not_used_as_the_only_policy": {
            "byte_claim_clears_eligibility_on_commit": False,
            "outbox_clears_eligibility_on_commit": False,
            "kill_child_rechecks_authority_before_republish": False,
            "kill_child_rechecks_semantics_before_republish": False,
            "revoke_returns_early_when_phase_committed": True,
            "sink_accept_rejects_stale_expected_version": True,
            "sink_accept_same_key_different_payload": "conflict",
            "sink_accept_same_payload": "replayed",
        },
        "historical_names_preserved": sorted(HISTORICAL_NAMES),
        "evidence_order": EVIDENCE_ORDER,
        "prose_alignment_mismatches": len(prose_mismatches),
    }

    csv_rows = write_csv(ROOT / "recovery_decision_table.csv", facts, table)
    results["decision_table_rows"] = csv_rows
    results["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    (ROOT / "state_space_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_counterexamples(
        ROOT / "counterexamples.md",
        adopted=adopted,
        specimens=specimens,
        historical=historical,
        naive_rows=naive_rows,
        collapse_rows=collapse_rows,
        sent_immune_rows=sent_immune_rows,
        invariant_status=invariant_status,
    )
    print(f"undefined_combination_count={results['undefined_combination_count']}")
    print(f"states_checked={evaluations}")
    print(f"decision_table_rows={csv_rows}")
    print(f"assignment_count={assignment_total}")
    print(f"publish_allowed_true_count={publish_true}")
    print(f"realizable_fact_count={realizable}")
    for name in INVARIANT_IDS:
        print(f"{name}={invariant_status[name]} hits={invariant_hits[name]}")
    print(f"adopted_counterexamples={results['adopted_counterexample_count']}")
    print(f"rejected_reading_counterexamples={results['rejected_reading_counterexample_count']}")
    print(f"specimen_failures={len(specimens)}")
    print(f"historical_failures={len(historical)}")
    print(f"prose_alignment_mismatches={len(prose_mismatches)}")
    print("tlc_ran=false")
    print("authorization_parameterized=true")
    print("authorization_chosen_to_favor_claim=false")
    if results["undefined_combination_count"] or results["unmet_invariants"] or specimens or historical:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
