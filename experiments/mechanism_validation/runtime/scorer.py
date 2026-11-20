"""Shared scorers. They read an observation; they do not commit Claim or Outbox rows."""

from __future__ import annotations

from typing import Any

from frozen_proposals.oracles import (
    ORACLE_VERSIONS,
    Observation,
    authority_oracle,
    physical_effect_state,
    semantics_oracle,
    successor_oracle,
)

from mechanism_validation.runtime.hashing import sha256_bytes


def score_observation(
    *,
    run_id: str,
    proposal_id: str,
    proposal_sha256: str,
    predicate_version: str,
    scoring_world: dict[str, Any] | None,
    physical_bytes: bytes | None,
    receiver_log: list[dict[str, Any]] | None,
    authority_log: list[dict[str, Any]] | None,
    withheld: bool,
    successor_obligation: str,
    stop_condition: str,
    recovery_attempted: bool,
    recovery_bytes_sha256: str | None,
) -> dict[str, Any]:
    obs = Observation(
        run_id=run_id,
        proposal_id=proposal_id,
        proposal_sha256=proposal_sha256,
        predicate_version=predicate_version,
        scoring_world=scoring_world,
        physical_bytes=physical_bytes,
        receiver_log=receiver_log,
        authority_log=authority_log,
        withheld=withheld,
        duplicate_effect=False,
        successor_obligation=successor_obligation,
        stop_condition=stop_condition,
        recovery_attempted=recovery_attempted,
        recovery_bytes_sha256=recovery_bytes_sha256,
    )
    successor = successor_oracle(obs)
    physical = physical_effect_state(obs)
    return {
        "semantic_validity": semantics_oracle(obs),
        "physical_effect_state": physical,
        "authority_decision": authority_oracle(obs),
        "successor_status": successor["status"],
        "successor_obligation": successor["obligation"],
        "stop_correctness": successor["week1_stop_correctness"],
        "oracle_versions": dict(ORACLE_VERSIONS),
        "physical_sha256": None if physical_bytes is None else sha256_bytes(physical_bytes),
    }
