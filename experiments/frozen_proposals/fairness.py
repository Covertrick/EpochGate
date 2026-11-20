"""Input equality across the four paths. Outcomes are not compared here."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

FAIRNESS_FIELDS = (
    "proposal_id",
    "payload_sha256",
    "task_semantic_version",
    "world_before_hash",
    "fault_schedule_hash",
    "authority_initial_hash",
    "receiver_impl",
    "receiver_version",
    "oracle_versions_canonical",
    "timeout_ms",
    "budget_units",
    "code_commit",
    "environment_hash",
    "implementation_hash",
)


def _norm(value: Any) -> str:
    return "" if value is None else str(value)


def check_fairness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("runner_status") == "ABANDONED":
            continue
        groups[str(row.get("proposal_id"))].append(row)
    for proposal_id, group in groups.items():
        if group and group[0].get("common_core") and len({row.get("path") for row in group}) < 4:
            mismatches.append(
                {
                    "proposal_id": proposal_id,
                    "field": "path_count",
                    "values": {"paths": sorted({str(row.get("path")) for row in group})},
                }
            )
        for field in FAIRNESS_FIELDS:
            values = {str(row.get("path")): _norm(row.get(field)) for row in group}
            if len(set(values.values())) > 1:
                mismatches.append({"proposal_id": proposal_id, "field": field, "values": values})
    return {"pass": not mismatches, "mismatches": mismatches}
