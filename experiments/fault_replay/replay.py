"""Replay every proposal-scenario cluster once per path."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from fault_replay.contract import PATHS, WEEK3
from fault_replay.execute import run_cell
from fault_replay.proposals import load_records

JOINT_FIELDS = [
    "run_id",
    "cluster_key",
    "cohort",
    "proposal_id",
    "scenario_id",
    "scenario_family",
    "mechanism_role",
    "in_main_comparison",
    "path",
    "task_id",
    "runner_status",
    "scenario_behavior",
    "fault_evidence_class",
    "terminal_outcome",
    "authority_decision",
    "physical_effect_state",
    "physical_basis",
    "week2_physical_effect_state",
    "evidence_state",
    "semantic_validity",
    "recovery_state",
    "recovery_evidence_state",
    "successor_obligation",
    "successor_required",
    "successor_status",
    "week1_successor_obligation",
    "week1_successor_status",
    "stop_correctness",
    "outdated_scenario_label",
    "published_old_bytes",
    "adjudication",
    "adjudication_basis",
    "joint_observed",
    "unobserved_reason",
    "terminal_adjudicated",
    "effect_history_adjudicated",
    "duplicate_effect",
    "payload_sha256",
    "payload_sha256_before",
    "payload_sha256_after",
    "payload_hash_match",
    "world_before_hash",
    "world_after_hash",
    "authority_initial_hash",
    "world_state_id_initial",
    "world_state_id_observed",
    "predicate_version",
    "oracle_versions",
    "killed_pid",
    "checkpoint",
    "latency_ms",
    "provenance_ref",
    "journal_mode",
    "synchronous",
    "receiver_impl",
    "receiver_version",
]


def _cell(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return value


def write_joint(rows: list[dict[str, Any]], path: Path | None = None) -> Path:
    target = path or (WEEK3 / "week3_replay_joint.csv")
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=JOINT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _cell(row.get(key, "")) for key in JOINT_FIELDS})
    return target


def replay_all(run_root: Path | None = None) -> list[dict[str, Any]]:
    records = load_records()
    rows: list[dict[str, Any]] = []
    for record in records:
        for path_name in PATHS:
            row = run_cell(record, path_name, run_root=run_root)
            rows.append(row)
            print(
                f"{row['run_id']} {row['runner_status']} {row['physical_effect_state']} {row['semantic_validity']}",
                flush=True,
            )
    return rows


def write_rows_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
        encoding="utf-8",
    )
