"""Shared constants. Scenario roles below override week2 labels where Week 3 requires it.

S06 is NON_DISCRIMINATIVE even if a week2 file still says MAIN_COMPARISON.
S09 stays MECHANISM_SPECIFIC_DIAGNOSTIC. Neither enters the Week 3 main comparison.
"""

from __future__ import annotations

from pathlib import Path

EXPERIMENTS = Path(__file__).resolve().parents[1]

WEEK3 = EXPERIMENTS / "fault_replay"
WEEK2 = EXPERIMENTS / "frozen_proposals"
ARCHIVED_JOINT = EXPERIMENTS / "historical_audit" / "archived_joint.csv"
ARCHIVED_SHA256 = "d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda"

COHORT = "WEEK3_FORMAL"
PATHS = ("Direct", "Admission", "Claim", "Outbox")
RECOVERY_PATHS = ("Claim", "Outbox")

STUDY_CHECKPOINTS = (
    "after_claim_before_sealed",
    "after_sealed_before_replace",
    "after_replace_before_receipt",
    "after_receipt",
)
LOST_RECEIPT_CHECKPOINT = "lost_receipt"

EVIDENCE_STATES = (
    "UNKNOWN",
    "PUBLISH_SEALED",
    "CONFIRM_TARGET",
    "REPLAY_RECEIPT",
    "CONFLICT",
)

SEPARATION_SCENARIOS = ("S13", "S14", "S15", "S16")
FREEZE_SCENARIOS = ("S17", "S18")
EXCLUDED_SCENARIOS = ("S06", "S09")

# Interpretive roles. These are Week 3 facts, not a rewrite of week2 files.
SCENARIO_ROLE = {
    "S01": "MAIN_COMPARISON",
    "S02": "MAIN_COMPARISON",
    "S03": "MAIN_COMPARISON",
    "S04": "MAIN_COMPARISON",
    "S05": "MAIN_COMPARISON",
    "S06": "NON_DISCRIMINATIVE",
    "S07": "MAIN_COMPARISON",
    "S08": "MAIN_COMPARISON",
    "S09": "MECHANISM_SPECIFIC_DIAGNOSTIC",
    "S10": "MAIN_COMPARISON",
    "S11": "MAIN_COMPARISON",
    "S12": "MAIN_COMPARISON",
    "S13": "SEPARATION",
    "S14": "SEPARATION",
    "S15": "SEPARATION",
    "S16": "SEPARATION",
    "S17": "NOTIFIED_FREEZE",
    "S18": "NOTIFIED_FREEZE",
}

SCENARIO_FAMILY = {
    "S01": "authority",
    "S02": "authority",
    "S03": "semantic",
    "S04": "authority",
    "S05": "other",
    "S06": "excluded",
    "S07": "other",
    "S08": "recovery",
    "S09": "excluded",
    "S10": "other",
    "S11": "authority",
    "S12": "successor",
    "S13": "recovery",
    "S14": "recovery",
    "S15": "recovery",
    "S16": "recovery",
    "S17": "successor",
    "S18": "successor",
}

SEPARATION_CHECKPOINT = {
    "S13": "after_claim_before_sealed",
    "S14": "after_sealed_before_replace",
    "S15": "after_replace_before_receipt",
    "S16": LOST_RECEIPT_CHECKPOINT,
}

MEMORY_SENTINEL = b"MEMORY_SENTINEL_NOT_DURABLE"
BOOTSTRAP_SEED = 20261120
BOOTSTRAP_DRAWS = 4000
BLIND_AUDIT_SEED = 20261120

TERMINAL_VALUES = {"MATCH", "MISMATCH", "UNKNOWN"}
AUTHORITY_VALUES = {"STALE_ACCEPTED", "NO_STALE_ACCEPTANCE_RECORDED", "UNKNOWN"}
PHYSICAL_VALUES = {"CONFIRMED_EFFECT", "NOT_LEGAL", "ABSENT", "PRESENT_UNVERIFIED", "UNKNOWN"}
EVIDENCE_ORACLE_VALUES = {"SUFFICIENT", "INSUFFICIENT", "UNKNOWN", "CONFLICT"}
SEMANTIC_VALUES = {"VALID", "INVALID", "UNKNOWN"}
RECOVERY_VALUES = {"RESTORED_BYTES", "NOT_RECOVERED", "NOT_APPLICABLE", "UNKNOWN"}
SUCCESSOR_STATUS_VALUES = {
    "COMPLETED",
    "COMPLETED_VALID",
    "FAILED",
    "NOT_COMPLETED",
    "CORRECT_STOP",
    "INCORRECT_STOP",
    "NOT_APPLICABLE",
    "UNKNOWN",
}
ADJUDICATION_VALUES = {"CONFIRMED_FAILURE", "NO_CONFIRMED_FAILURE", "UNRESOLVED"}


def in_main_comparison(scenario_id: str, mechanism_role: str) -> bool:
    if scenario_id in EXCLUDED_SCENARIOS:
        return False
    if mechanism_role in {"NON_DISCRIMINATIVE", "MECHANISM_SPECIFIC_DIAGNOSTIC"}:
        return False
    return True
