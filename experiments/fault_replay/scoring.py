"""Week 3 physical classes stay independent of the semantic oracle.

CLAIM_DIGEST confirmation means the target bytes match the frozen claim.
That is not the Week 2 legal-world comparison. RESTORED_BYTES is not
CONFIRMED_EFFECT. Semantic INVALID is not physical NOT_LEGAL.
"""

from __future__ import annotations

from typing import Any

from frozen_proposals.hashing import sha256_bytes
from frozen_proposals.oracles import (
    Observation,
    adjudicate,
    authority_oracle,
    effect_history_adjudicated,
    evidence_oracle,
    physical_effect_state,
    recovery_state,
    semantics_oracle,
    successor_oracle,
    terminal_oracle,
)
from frozen_proposals.predicate import PREDICATE_VERSION


def classify_claim_digest(
    target: bytes | None,
    claim_sha256: str,
    *,
    recovery_attempted: bool,
    restored_bytes: bool,
) -> tuple[str, str]:
    """Return (physical_effect_state, recovery_state) from the claim digest."""
    if target is None:
        if recovery_attempted:
            return "ABSENT", "NOT_RECOVERED"
        return "ABSENT", "NOT_APPLICABLE"
    if sha256_bytes(target) != claim_sha256:
        if recovery_attempted and restored_bytes:
            return "NOT_LEGAL", "UNKNOWN"
        return "NOT_LEGAL", "NOT_APPLICABLE"
    if restored_bytes:
        return "PRESENT_UNVERIFIED", "RESTORED_BYTES"
    return "CONFIRMED_EFFECT", "NOT_APPLICABLE"


def published_old_bytes(target: bytes | None, claim: bytes) -> bool:
    return target is not None and target == claim


def week2_bundle(obs: Observation, runner_status: str) -> dict[str, Any]:
    """Call the five Week 2 oracles. None of them is overwritten by another."""
    terminal = terminal_oracle(obs)
    authority = authority_oracle(obs)
    physical = physical_effect_state(obs)
    evidence = evidence_oracle(obs)
    semantic = semantics_oracle(obs)
    successor = successor_oracle(obs)
    recovery = recovery_state(obs)
    adjudication, basis = adjudicate(
        terminal=terminal,
        authority=authority,
        physical=physical,
        evidence=evidence,
        duplicate_effect=obs.duplicate_effect,
        runner_status=runner_status,
    )
    return {
        "terminal_outcome": terminal,
        "authority_decision": authority,
        "week2_physical_effect_state": physical,
        "evidence_state": evidence,
        "semantic_validity": semantic,
        "week2_recovery_state": recovery,
        "successor_obligation": successor["obligation"],
        "successor_status": successor["status"],
        "week1_successor_obligation": successor["week1_successor_obligation"],
        "week1_successor_status": successor["week1_successor_status"],
        "stop_correctness": successor["week1_stop_correctness"],
        "adjudication": adjudication,
        "adjudication_basis": basis,
        "terminal_adjudicated": terminal in {"MATCH", "MISMATCH"},
        "effect_history_adjudicated": effect_history_adjudicated(authority, physical, evidence),
        "duplicate_effect": obs.duplicate_effect,
        "predicate_version": obs.predicate_version or PREDICATE_VERSION,
    }
