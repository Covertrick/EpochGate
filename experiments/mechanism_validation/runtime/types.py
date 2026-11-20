"""Request carrier used by the harness. It does not transition state."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EffectRequest:
    proposal_id: str
    idempotency_key: str
    relay_id: str
    payload: bytes
    payload_sha256: str
    authority_token: int
    authority_epoch: int
    world_version: int
