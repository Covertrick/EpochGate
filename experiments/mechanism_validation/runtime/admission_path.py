"""Admission path. Eligibility is latched once. The window until send stays open."""

from __future__ import annotations

from typing import Any

from mechanism_validation.runtime.faults import FaultState
from mechanism_validation.runtime.receiver_client import PathReceiverClient
from mechanism_validation.runtime.types import EffectRequest


def _blank(outcome: str, **extra: Any) -> dict[str, Any]:
    body = {
        "outcome": outcome,
        "published_new": False,
        "withheld": False,
        "recovery": "NOT_APPLICABLE",
        "local_ack": False,
        "receiver_log": [],
        "presented_token": None,
        "presented_epoch": None,
    }
    body.update(extra)
    return body


def run_admission_path(
    client: PathReceiverClient,
    request: EffectRequest,
    auth: dict[str, Any],
    faults: FaultState,
    world: dict[str, Any],
    *,
    attempt_id: str,
    conflict_payload: bytes,
) -> dict[str, Any]:
    faults.hit("before_effect", world, auth)
    faults.hit("authority_gate", world, auth)
    latched = (
        bool(auth["eligible"])
        and int(auth["token"]) == int(request.authority_token)
        and int(auth["epoch"]) == int(request.authority_epoch)
    )
    faults.hit("after_admit_before_send", world, auth)
    faults.hit("after_commit_before_send", world, auth)
    if faults.restart:
        return _blank("NO_DURABLE_STATE", withheld=True, recovery="NOT_RECOVERED")
    if not latched:
        return _blank("WITHHELD", withheld=True, presented_token=int(auth["token"]), presented_epoch=int(auth["epoch"]))
    faults.hit("before_send", world, auth)
    if faults.receiver_fail:
        return _blank(
            "RECEIVER_FAIL",
            presented_token=int(request.authority_token),
            presented_epoch=int(request.authority_epoch),
        )
    first = client.submit(
        effect_key=request.idempotency_key,
        payload=request.payload,
        attempt_id=attempt_id,
        fence=False,
        authority_token=int(request.authority_token),
        authority_epoch=int(request.authority_epoch),
    )
    log = [first]
    if faults.duplicate:
        log.append(
            client.submit(
                effect_key=request.idempotency_key,
                payload=request.payload,
                attempt_id=attempt_id + ":dup",
                fence=False,
                authority_token=int(request.authority_token),
                authority_epoch=int(request.authority_epoch),
            )
        )
    elif faults.conflict:
        log.append(
            client.submit(
                effect_key=request.idempotency_key,
                payload=conflict_payload,
                attempt_id=attempt_id + ":conflict",
                fence=False,
                authority_token=int(request.authority_token),
                authority_epoch=int(request.authority_epoch),
            )
        )
    if first.get("status") == "UNKNOWN" or first.get("outcome") == "UNKNOWN":
        return _blank(
            "UNKNOWN",
            receiver_log=log,
            presented_token=int(request.authority_token),
            presented_epoch=int(request.authority_epoch),
        )
    return _blank(
        str(first.get("outcome")),
        published_new=bool(first.get("applied")),
        local_ack=first.get("outcome") in {"ACCEPTED", "REPLAY"},
        receiver_log=log,
        presented_token=int(request.authority_token),
        presented_epoch=int(request.authority_epoch),
    )
