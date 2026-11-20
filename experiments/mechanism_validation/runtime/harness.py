"""Shared harness. It dispatches to separate path functions and then scores."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from frozen_proposals.predicate import PREDICATE_VERSION

from mechanism_validation.runtime.admission_path import run_admission_path
from mechanism_validation.runtime.auditor import effect_for
from mechanism_validation.runtime.claim_mechanism import run_claim_path
from mechanism_validation.runtime.direct_path import run_direct_path
from mechanism_validation.runtime.faults import FaultState
from mechanism_validation.runtime.hashing import sha256_bytes, sha256_obj
from mechanism_validation.runtime.outbox_mechanism import run_outbox_path
from mechanism_validation.runtime.receiver_client import PathReceiverClient
from mechanism_validation.runtime.scorer import score_observation
from mechanism_validation.runtime.types import EffectRequest


def execute_path(
    *,
    path_name: str,
    proposal: dict[str, Any],
    payload: bytes,
    client: PathReceiverClient,
    work_dir: Path,
    receiver_db: Path,
    attempt_id: str,
) -> dict[str, Any]:
    world = copy.deepcopy(proposal["world_before"])
    auth = copy.deepcopy(proposal["authority_initial"])
    faults = FaultState(proposal.get("fault_schedule"))
    request = EffectRequest(
        proposal_id=proposal["proposal_id"],
        idempotency_key=proposal["idempotency_key"],
        relay_id=proposal["relay_id"],
        payload=payload,
        payload_sha256=sha256_bytes(payload),
        authority_token=int(proposal["authority_token"]),
        authority_epoch=int(proposal["authority_epoch"]),
        world_version=int(proposal["world_version"]),
    )
    conflict_payload = f"CONFLICT|{proposal['proposal_id']}".encode("utf-8")
    if path_name == "Direct":
        result = run_direct_path(
            client, request, auth, faults, world, attempt_id=attempt_id, conflict_payload=conflict_payload
        )
    elif path_name == "Admission":
        result = run_admission_path(
            client, request, auth, faults, world, attempt_id=attempt_id, conflict_payload=conflict_payload
        )
    elif path_name == "Claim":
        result = run_claim_path(
            work_dir / "claim.sqlite",
            client,
            request,
            auth,
            faults,
            world,
            attempt_id=attempt_id,
            conflict_payload=conflict_payload,
        )
    elif path_name == "Outbox":
        result = run_outbox_path(
            work_dir / "outbox.sqlite",
            client,
            request,
            auth,
            faults,
            world,
            attempt_id=attempt_id,
            conflict_payload=conflict_payload,
        )
    else:
        raise ValueError(path_name)
    ledger = effect_for(receiver_db, proposal["idempotency_key"])
    physical = None if ledger is None else ledger["payload"]
    effect_count = 0 if ledger is None else int(ledger["logical_effect_count"])
    authority_log = [
        {
            "seq": 1,
            "token": int(proposal["authority_initial"]["token"]),
            "epoch": int(proposal["authority_initial"]["epoch"]),
            "eligible": bool(proposal["authority_initial"]["eligible"]),
            "event": "init",
        }
    ]
    if auth != proposal["authority_initial"]:
        authority_log.append(
            {
                "seq": 2,
                "token": int(auth["token"]),
                "epoch": int(auth["epoch"]),
                "eligible": bool(auth["eligible"]),
                "event": "fault",
            }
        )
    receiver_log = []
    for index, item in enumerate(result.get("receiver_log") or [], start=1):
        if item.get("outcome") not in {"ACCEPTED", "REPLAY"}:
            continue
        receiver_log.append(
            {
                "seq": len(authority_log),
                "token": result.get("presented_token"),
                "epoch": result.get("presented_epoch"),
                "accepted": bool(item.get("applied")) or item.get("outcome") == "REPLAY",
                "digest": item.get("payload_hash"),
            }
        )
        _ = index
    scores = score_observation(
        run_id=attempt_id,
        proposal_id=proposal["proposal_id"],
        proposal_sha256=request.payload_sha256,
        predicate_version=PREDICATE_VERSION,
        scoring_world=world,
        physical_bytes=physical,
        receiver_log=receiver_log,
        authority_log=authority_log,
        withheld=bool(result.get("withheld")),
        successor_obligation=proposal.get("successor_obligation", "NOT_REQUIRED"),
        stop_condition=proposal.get("stop_condition", "NOT_A_STOP_SCENARIO"),
        recovery_attempted=result.get("recovery") == "RESTORED_BYTES",
        recovery_bytes_sha256=request.payload_sha256 if result.get("recovery") == "RESTORED_BYTES" else None,
    )
    conflict_observed = any(
        item.get("outcome") == "CONFLICT" for item in (result.get("receiver_log") or [])
    )
    return {
        "path_name": path_name,
        "outcome": result.get("outcome"),
        "conflict_observed": conflict_observed,
        "published_new": bool(result.get("published_new")),
        "withheld": bool(result.get("withheld")),
        "recovery": result.get("recovery"),
        "local_ack": bool(result.get("local_ack")),
        "effect_count": effect_count,
        "duplicate_effect": effect_count > 1,
        "attempt_count": result.get("attempt_count"),
        "faults_fired": list(faults.fired),
        "world_after_hash": sha256_obj(world),
        "authority_after_hash": sha256_obj(auth),
        "scores": scores,
        "confirmed_effect": scores["physical_effect_state"] == "CONFIRMED_EFFECT",
    }
