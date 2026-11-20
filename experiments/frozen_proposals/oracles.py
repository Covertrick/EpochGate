"""Five independent oracles.

Each function reads the observation only. None of them reads another oracle's
return value. Path self-report fields on the observation are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from frozen_proposals.hashing import sha256_bytes
from frozen_proposals.predicate import PREDICATE_VERSION, legal_payload

ORACLE_VERSIONS = {
    "terminal_oracle": "1.0.0",
    "authority_oracle": "1.0.0",
    "evidence_oracle": "1.0.0",
    "semantics_oracle": "1.0.0",
    "successor_oracle": "1.0.0",
}


@dataclass
class Observation:
    run_id: str
    proposal_id: str
    proposal_sha256: str
    predicate_version: str | None
    scoring_world: dict | None
    physical_bytes: bytes | None
    physical_unreadable: bool = False
    receiver_log: list[dict[str, Any]] | None = None
    receiver_provenance_missing: bool = False
    authority_log: list[dict[str, Any]] | None = None
    claim_record: dict[str, Any] | None = None
    outbox_record: dict[str, Any] | None = None
    recovery_attempted: bool = False
    recovery_bytes_sha256: str | None = None
    successor_obligation: str = "UNKNOWN"
    stop_condition: str = "NOT_A_STOP_SCENARIO"
    successor_effect_bytes: bytes | None = None
    withheld: bool = False
    duplicate_effect: bool = False
    outdated_scenario_label: str = "UNKNOWN"
    path_self_report_semantic_valid: bool | None = None
    path_self_report_terminal_match: bool | None = None
    path_self_report_successor_failed: bool | None = None
    successor_unobservable: bool = False
    notes: dict[str, Any] = field(default_factory=dict)


def _authority_at(log: list[dict[str, Any]], seq: int) -> dict[str, Any] | None:
    prior = [row for row in log if int(row["seq"]) <= seq]
    if not prior:
        return None
    return max(prior, key=lambda row: int(row["seq"]))


def terminal_oracle(obs: Observation) -> str:
    if obs.physical_unreadable or obs.scoring_world is None or obs.predicate_version != PREDICATE_VERSION:
        return "UNKNOWN"
    legal = legal_payload(obs.scoring_world)
    physical = obs.physical_bytes
    if physical is None:
        return "MATCH" if legal == b"" else "MISMATCH"
    return "MATCH" if physical == legal else "MISMATCH"


def physical_effect_state(obs: Observation) -> str:
    """Contract field. Does not read terminal_oracle or recovery_state."""
    if obs.physical_unreadable or obs.scoring_world is None or obs.predicate_version != PREDICATE_VERSION:
        return "UNKNOWN"
    if obs.physical_bytes is None:
        return "ABSENT"
    legal = legal_payload(obs.scoring_world)
    if legal == b"":
        return "NOT_LEGAL"
    if obs.physical_bytes == legal:
        return "CONFIRMED_EFFECT"
    return "NOT_LEGAL"


def recovery_state(obs: Observation) -> str:
    """Contract field. RESTORED_BYTES does not imply CONFIRMED_EFFECT."""
    if not obs.recovery_attempted:
        return "NOT_APPLICABLE"
    physical_sha = None if obs.physical_bytes is None else sha256_bytes(obs.physical_bytes)
    if obs.recovery_bytes_sha256 and physical_sha == obs.recovery_bytes_sha256:
        return "RESTORED_BYTES"
    if obs.physical_bytes is None:
        return "NOT_RECOVERED"
    return "UNKNOWN"


def authority_oracle(obs: Observation) -> str:
    if obs.receiver_provenance_missing or obs.receiver_log is None or obs.authority_log is None:
        return "UNKNOWN"
    accepts = [row for row in obs.receiver_log if row.get("accepted")]
    if not accepts:
        return "NO_STALE_ACCEPTANCE_RECORDED"
    for row in accepts:
        if "seq" not in row or "token" not in row or "epoch" not in row:
            return "UNKNOWN"
        current = _authority_at(obs.authority_log, int(row["seq"]))
        if current is None:
            return "UNKNOWN"
        stale = (
            (not current["eligible"])
            or int(current["token"]) != int(row["token"])
            or int(current["epoch"]) != int(row["epoch"])
        )
        if stale:
            return "STALE_ACCEPTED"
    return "NO_STALE_ACCEPTANCE_RECORDED"


def evidence_oracle(obs: Observation) -> str:
    if obs.receiver_provenance_missing or obs.receiver_log is None:
        return "UNKNOWN"
    log = obs.receiver_log
    if any(row.get("detail") == "conflict" for row in log):
        return "CONFLICT"
    if obs.claim_record and obs.claim_record.get("payload_sha256") not in (None, obs.proposal_sha256):
        return "CONFLICT"
    if obs.outbox_record and obs.outbox_record.get("payload_sha256") not in (None, obs.proposal_sha256):
        return "CONFLICT"
    applied = [row for row in log if row.get("accepted")]
    physical_sha = None if obs.physical_bytes is None else sha256_bytes(obs.physical_bytes)
    if applied and physical_sha and applied[-1].get("digest") != physical_sha:
        return "CONFLICT"
    if applied and physical_sha and applied[-1].get("digest") == physical_sha:
        return "SUFFICIENT"
    if obs.withheld and not applied and obs.physical_bytes is None:
        return "SUFFICIENT"
    if any(row.get("detail") == "receiver_fail" for row in log) and obs.physical_bytes is None:
        return "SUFFICIENT"
    if not applied and obs.physical_bytes is None:
        return "SUFFICIENT"
    if obs.physical_bytes is not None and not applied:
        return "INSUFFICIENT"
    return "INSUFFICIENT"


def semantics_oracle(obs: Observation) -> str:
    if obs.scoring_world is None or obs.predicate_version != PREDICATE_VERSION or obs.physical_unreadable:
        return "UNKNOWN"
    if obs.physical_bytes is None:
        return "UNKNOWN"
    legal = legal_payload(obs.scoring_world)
    if legal != b"" and obs.physical_bytes == legal:
        return "VALID"
    return "INVALID"


def successor_oracle(obs: Observation) -> dict[str, str]:
    unknown = {
        "obligation": "UNKNOWN",
        "status": "UNKNOWN",
        "week1_successor_obligation": "UNKNOWN",
        "week1_successor_status": "UNKNOWN",
        "week1_stop_correctness": "UNKNOWN",
    }
    if obs.successor_unobservable or obs.successor_obligation not in {"REQUIRED", "NOT_REQUIRED", "UNKNOWN"}:
        return unknown
    if obs.successor_obligation == "UNKNOWN":
        return unknown
    week1_obligation = "OBLIGATED" if obs.successor_obligation == "REQUIRED" else "NOT_OBLIGATED"
    if obs.successor_obligation == "NOT_REQUIRED":
        if obs.stop_condition != "MUST_STOP":
            return {
                "obligation": "NOT_REQUIRED",
                "status": "NOT_APPLICABLE",
                "week1_successor_obligation": week1_obligation,
                "week1_successor_status": "NOT_APPLICABLE",
                "week1_stop_correctness": "UNKNOWN",
            }
        stopped = obs.withheld and obs.physical_bytes is None and not obs.successor_effect_bytes
        if stopped:
            return {
                "obligation": "NOT_REQUIRED",
                "status": "CORRECT_STOP",
                "week1_successor_obligation": week1_obligation,
                "week1_successor_status": "NOT_APPLICABLE",
                "week1_stop_correctness": "CORRECT_STOP",
            }
        return {
            "obligation": "NOT_REQUIRED",
            "status": "INCORRECT_STOP",
            "week1_successor_obligation": week1_obligation,
            "week1_successor_status": "NOT_APPLICABLE",
            "week1_stop_correctness": "NOT_CORRECT_STOP",
        }
    legal = None if obs.scoring_world is None else legal_payload(obs.scoring_world)
    if legal is not None and obs.successor_effect_bytes == legal and legal != b"":
        return {
            "obligation": "REQUIRED",
            "status": "COMPLETED",
            "week1_successor_obligation": week1_obligation,
            "week1_successor_status": "COMPLETED",
            "week1_stop_correctness": "UNKNOWN",
        }
    return {
        "obligation": "REQUIRED",
        "status": "FAILED",
        "week1_successor_obligation": week1_obligation,
        "week1_successor_status": "NOT_COMPLETED",
        "week1_stop_correctness": "NOT_CORRECT_STOP",
    }


def adjudicate(
    *,
    terminal: str,
    authority: str,
    physical: str,
    evidence: str,
    duplicate_effect: bool,
    runner_status: str,
) -> tuple[str, str]:
    if runner_status in {"NOT_APPLICABLE", "INVALID", "TIMEOUT", "BLOCKED", "ABANDONED"}:
        return "UNRESOLVED", f"runner_{runner_status}"
    if evidence not in {"SUFFICIENT", "CONFLICT"}:
        return "UNRESOLVED", f"evidence_{evidence}"
    if terminal == "UNKNOWN" or authority == "UNKNOWN" or physical == "UNKNOWN":
        return "UNRESOLVED", "oracle_unknown"
    bases: list[str] = []
    if authority == "STALE_ACCEPTED":
        bases.append("STALE_ACCEPTED")
    if physical == "NOT_LEGAL":
        bases.append("WRONG_PHYSICAL")
    if duplicate_effect:
        bases.append("DUPLICATE_EFFECT")
    if bases:
        return "CONFIRMED_FAILURE", "+".join(bases)
    return "NO_CONFIRMED_FAILURE", "NONE"


def effect_history_adjudicated(authority: str, physical: str, evidence: str) -> bool:
    return authority != "UNKNOWN" and physical != "UNKNOWN" and evidence in {"SUFFICIENT", "CONFLICT"}
