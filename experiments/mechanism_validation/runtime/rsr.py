"""Re-check sealed bytes with the Week 2 semantic oracle before a publish.

VALID may continue. INVALID and UNKNOWN do not publish. This call is not atomic
with the later publish, so it does not remove TOCTOU.
"""

from __future__ import annotations

from typing import Any

from frozen_proposals.oracles import Observation, semantics_oracle
from frozen_proposals.predicate import PREDICATE_VERSION

from mechanism_validation.runtime.hashing import sha256_bytes

ORACLE_VERSION = "week2.semantics_oracle:1.0.0+week5.rsr:1"


def semantic_precheck(payload: bytes | None, world: dict[str, Any] | None, predicate_version: str | None) -> str:
    obs = Observation(
        run_id="rsr-precheck",
        proposal_id="precheck",
        proposal_sha256="" if payload is None else sha256_bytes(payload),
        predicate_version=predicate_version,
        scoring_world=world,
        physical_bytes=payload,
        physical_unreadable=False,
    )
    return semantics_oracle(obs)


def publish_allowed(oracle_result: str) -> bool:
    return oracle_result == "VALID"


def predicate_version() -> str:
    return PREDICATE_VERSION
