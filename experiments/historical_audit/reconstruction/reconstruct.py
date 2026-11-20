"""Deterministic reader for the archived M4 confirm matrix.

Original experiment files are read and hashed only. This module does not
impute joint cells from marginal totals, and it does not rewrite sources.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "outcome.schema.json"

CONFIRM_REL = Path("results/runtime/correctness/m4-confirm")
DEDICATED_REL = Path("results/runtime/correctness/m4-dedicated")
M6_REL = Path("results/runtime/correctness/m6/summary.json")

INVENTORY_ROOTS = [
    Path("results/runtime/correctness"),
    Path("docs/现行/论文"),
    Path("docs/档案/v1.6/设计原文.md"),
    Path("docs/实验/M4-M6结果与审计.md"),
    Path("docs/实验/M4-M6-v2结果与验收.md"),
    Path("docs/实验/M4-M6-v2-blocked审计.md"),
    Path("docs/实验/M4-M6独立确认协议.md"),
    Path("docs/实验/M4-M6-v2协议.md"),
    Path("docs/实验/实验方法.md"),
    Path("docs/实验/补实验结果.md"),
    Path("docs/实验/正确性修复与补充实验.md"),
    Path("scripts/m4_matrix.py"),
    Path("scripts/m4_tasks.py"),
    Path("scripts/m6_score.py"),
    Path("scripts/reliability_contract.py"),
]

FAILURE_FLAGS = (
    "stale_credential_accepted",
    "wrong_physical",
    "duplicate_effect",
)

CSV_COLUMNS = [
    "source_summary_ref",
    "source_evidence_ref",
    "source_artifact_ref",
    "cohort",
    "proposal_id",
    "proposal_id_origin",
    "proposal_cluster_key",
    "run_id",
    "task_id",
    "path",
    "path_origin",
    "session_id",
    "session_id_origin",
    "fault_label",
    "terminal_outcome",
    "authority_decision",
    "physical_effect_state",
    "evidence_state",
    "semantic_validity",
    "recovery_state",
    "successor_obligation",
    "successor_status",
    "stop_correctness",
    "outdated_scenario_label",
    "adjudication",
    "adjudication_basis",
    "joint_observed",
    "unobserved_reason",
    "terminal_adjudicated",
    "effect_history_adjudicated",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def rel(path: Path, workspace: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def load_schema(path: Path | None = None) -> dict[str, Any]:
    return read_json(path or SCHEMA_PATH)


def _type_ok(value: Any, annotation: Any) -> bool:
    if isinstance(annotation, list):
        return any(_type_ok(value, item) for item in annotation)
    if annotation == "string":
        return isinstance(value, str)
    if annotation == "boolean":
        return isinstance(value, bool)
    if annotation == "null":
        return value is None
    if annotation == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if annotation == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def validate_outcome(record: dict[str, Any], schema: dict[str, Any] | None = None) -> list[str]:
    """Validate one normalized record against the frozen schema and invariants."""
    schema = schema or load_schema()
    errors: list[str] = []
    allowed = set(schema["properties"])
    required = set(schema["required"])
    extra = set(record) - allowed
    missing = required - set(record)
    if extra:
        errors.append("additional_properties:" + ",".join(sorted(extra)))
    if missing:
        errors.append("missing_required:" + ",".join(sorted(missing)))
    for key, spec in schema["properties"].items():
        if key not in record:
            continue
        value = record[key]
        if "enum" in spec and value not in spec["enum"]:
            errors.append(f"enum:{key}={value!r}")
        elif "type" in spec and not _type_ok(value, spec["type"]):
            errors.append(f"type:{key}")
    if record.get("joint_observed") is True:
        if record.get("adjudication") not in {"CONFIRMED_FAILURE", "NO_CONFIRMED_FAILURE"}:
            errors.append("joint_without_adjudication")
        if record.get("unobserved_reason") is not None:
            errors.append("joint_with_unobserved_reason")
        if record.get("terminal_adjudicated") is not True or record.get("effect_history_adjudicated") is not True:
            errors.append("joint_without_both_dimensions")
    if record.get("joint_observed") is False:
        if record.get("adjudication") != "UNRESOLVED":
            errors.append("unobserved_not_unresolved")
        if not record.get("unobserved_reason"):
            errors.append("unobserved_without_reason")
    if record.get("terminal_outcome") == "UNKNOWN" and record.get("adjudication") == "CONFIRMED_FAILURE":
        errors.append("unknown_classified_as_failure")
    if record.get("recovery_state") == "RESTORED_BYTES" and record.get("physical_effect_state") == "CONFIRMED_EFFECT":
        if record.get("adjudication_basis", "").startswith("RESTORED"):
            errors.append("restored_bytes_used_as_confirmed_effect_basis")
    if record.get("stop_correctness") == "CORRECT_STOP" and record.get("successor_status") not in {
        "NOT_APPLICABLE",
        "UNKNOWN",
        "COMPLETED",
    }:
        errors.append("correct_stop_became_successor_failure_status")
    if record.get("stop_correctness") == "CORRECT_STOP" and record.get("adjudication") == "CONFIRMED_FAILURE":
        if "STALE" not in record.get("adjudication_basis", "") and "WRONG" not in record.get("adjudication_basis", "") and "DUPLICATE" not in record.get("adjudication_basis", ""):
            errors.append("correct_stop_classified_as_failure")
    return errors


def is_marginal_only(payload: Any) -> bool:
    """True when the input has counts but no run-level joint records."""
    if not isinstance(payload, dict):
        return True
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return True
    for row in rows:
        if not isinstance(row, dict):
            continue
        has_terminal = "task_terminal_match" in row or "terminal_outcome" in row
        has_effect = any(key in row for key in ("stale_credential_accepted", "wrong_physical", "physical_digest", "receiver"))
        has_identity = any(key in row for key in ("run_id", "task_id"))
        if has_identity and has_terminal and has_effect:
            return False
    return True


def scenario_label(fault: str | None, cohort: str) -> str:
    if cohort == "m4-dedicated":
        return "DEDICATED_NOT_A_MATRIX_FAULT"
    return {
        "S1": "TIMELY_NOTICE",
        "S2": "NOTICE_MISSING_OR_DELAYED",
        "S3": "INTERRUPT_BEFORE_ACCEPT",
    }.get(fault or "", "UNKNOWN")


def internal_conflicts(raw: dict[str, Any], artifact_matches: bool | None) -> list[str]:
    issues: list[str] = []
    oracle = raw.get("oracle")
    if isinstance(oracle, dict):
        for key in (
            "task_terminal_match",
            "stale_credential_accepted",
            "wrong_physical",
            "interrupted_recovered",
            "verifiable_effect",
            "physical_digest",
            "legal_digest",
            "successor_completed",
            "correct_stop",
            "duplicate_effect",
        ):
            if key in oracle and key in raw and oracle.get(key) != raw.get(key):
                issues.append(f"oracle_toplevel_mismatch:{key}")
    receiver = raw.get("receiver")
    if isinstance(receiver, dict) and "physical_digest" in raw:
        if receiver.get("physical_digest") != raw.get("physical_digest"):
            issues.append("receiver_physical_digest_mismatch")
    if raw.get("task_terminal_match") is True and raw.get("wrong_physical") is True:
        issues.append("terminal_match_and_wrong_physical")
    if raw.get("verifiable_effect") is True and raw.get("physical_digest") is None:
        issues.append("verifiable_without_physical")
    physical = raw.get("physical_digest")
    legal = raw.get("legal_digest")
    if raw.get("wrong_physical") is True and physical and legal and physical == legal:
        issues.append("wrong_physical_but_digests_equal")
    if raw.get("wrong_physical") is False and physical and legal and physical != legal:
        issues.append("not_wrong_physical_but_digests_differ")
    if raw.get("stale_credential_accepted") is True:
        log = receiver.get("accept_log") if isinstance(receiver, dict) else None
        accepted = isinstance(log, list) and any(isinstance(item, dict) and item.get("accepted") is True for item in log)
        if not accepted:
            issues.append("stale_accepted_without_accept_log")
    if artifact_matches is False:
        issues.append("artifact_sha256_ne_physical_digest")
    if artifact_matches is True and physical is None:
        issues.append("artifact_present_but_physical_digest_null")
    return issues


def normalize_raw(
    raw: dict[str, Any],
    *,
    cohort: str,
    source_summary_ref: str | None,
    source_evidence_ref: str | None,
    source_artifact_ref: str | None,
    artifact_matches: bool | None,
    conflicts: list[str] | None = None,
) -> dict[str, Any]:
    """Map one archived object onto the frozen outcome contract.

    Scenario labels never write authority_decision. A blocked terminal flag
    stays UNKNOWN. Recovery never promotes physical state to CONFIRMED_EFFECT.
    CORRECT_STOP never becomes a successor failure status.
    """
    conflicts = list(conflicts if conflicts is not None else internal_conflicts(raw, artifact_matches))
    blocked = raw.get("blocked") is True
    run_id = raw.get("run_id") if isinstance(raw.get("run_id"), str) else None
    task_id = raw.get("task_id") if isinstance(raw.get("task_id"), str) else None
    arm = raw.get("arm") if isinstance(raw.get("arm"), str) else None
    fault = raw.get("fault") if isinstance(raw.get("fault"), str) else None
    session_id = raw.get("session_id") if isinstance(raw.get("session_id"), str) else None

    if cohort == "m4-confirm" and task_id and fault:
        cluster = f"{task_id}|{fault}"
        proposal_origin = "DERIVED_COMPOSITE_TASK_FAULT"
    elif cohort == "m4-dedicated" and raw.get("authority") and arm:
        cluster = f"dedicated|{raw.get('authority')}|{arm}"
        proposal_origin = "DERIVED_COMPOSITE_AUTHORITY_ARM"
    else:
        cluster = None
        proposal_origin = "ABSENT"

    receiver = raw.get("receiver") if isinstance(raw.get("receiver"), dict) else None
    has_effect_fields = all(isinstance(raw.get(key), bool) for key in FAILURE_FLAGS) and receiver is not None
    terminal_usable = isinstance(raw.get("task_terminal_match"), bool) and not blocked and not conflicts
    effect_usable = has_effect_fields and not blocked and not conflicts and run_id is not None

    if blocked or not isinstance(raw.get("task_terminal_match"), bool) or conflicts:
        terminal = "UNKNOWN"
        semantic = "UNKNOWN"
    elif raw.get("task_terminal_match") is True:
        terminal = "MATCH"
        semantic = "VALID"
    else:
        terminal = "MISMATCH"
        semantic = "INVALID"

    # Authority is read only from stale_credential_accepted. Fault labels are not consulted.
    if conflicts or "stale_credential_accepted" not in raw or not isinstance(raw.get("stale_credential_accepted"), bool):
        authority = "UNKNOWN"
    elif raw.get("stale_credential_accepted") is True:
        authority = "STALE_ACCEPTED"
    else:
        authority = "NO_STALE_ACCEPTANCE_RECORDED"

    physical = raw.get("physical_digest")
    legal = raw.get("legal_digest")
    if conflicts:
        physical_state = "UNKNOWN"
    elif raw.get("wrong_physical") is True and physical and legal and physical != legal:
        physical_state = "NOT_LEGAL"
    elif (
        physical
        and legal
        and physical == legal
        and raw.get("verifiable_effect") is True
        and raw.get("wrong_physical") is False
        and artifact_matches is True
    ):
        physical_state = "CONFIRMED_EFFECT"
    elif physical is None and raw.get("wrong_physical") is False and receiver is not None:
        physical_state = "ABSENT"
    elif physical is None and not has_effect_fields:
        physical_state = "UNKNOWN"
    elif physical:
        physical_state = "PRESENT_UNVERIFIED"
    else:
        physical_state = "UNKNOWN"

    if blocked or receiver is None:
        evidence_state = "INSUFFICIENT" if blocked or cohort == "m4-dedicated" else "UNKNOWN"
    elif conflicts:
        evidence_state = "INSUFFICIENT"
    else:
        evidence_state = "SUFFICIENT"

    if "interrupted_recovered" not in raw or conflicts:
        recovery = "UNKNOWN"
    elif raw.get("interrupted_recovered") is True:
        recovery = "RESTORED_BYTES"
    elif raw.get("interrupted_recovered") is False:
        recovery = "NOT_RECOVERED"
    elif raw.get("interrupted_recovered") is None:
        recovery = "NOT_APPLICABLE"
    else:
        recovery = "UNKNOWN"

    if not isinstance(raw.get("successor_applicable"), bool) or conflicts:
        obligation = "UNKNOWN"
    elif raw.get("successor_applicable") is True:
        obligation = "OBLIGATED"
    else:
        obligation = "NOT_OBLIGATED"

    if conflicts or "successor_completed" not in raw:
        successor = "UNKNOWN"
    elif raw.get("correct_stop") is True and raw.get("successor_applicable") is False:
        successor = "NOT_APPLICABLE"
    elif raw.get("successor_completed") is True:
        successor = "COMPLETED"
    elif raw.get("successor_applicable") is False:
        successor = "NOT_APPLICABLE"
    elif raw.get("successor_completed") is False:
        successor = "NOT_COMPLETED"
    else:
        successor = "UNKNOWN"

    if conflicts or not isinstance(raw.get("correct_stop"), bool):
        stop = "UNKNOWN"
    elif raw.get("correct_stop") is True:
        stop = "CORRECT_STOP"
    else:
        stop = "NOT_CORRECT_STOP"

    bases: list[str] = []
    if isinstance(raw.get("stale_credential_accepted"), bool) and raw.get("stale_credential_accepted") is True:
        bases.append("STALE_CREDENTIAL_ACCEPTED")
    if isinstance(raw.get("wrong_physical"), bool) and raw.get("wrong_physical") is True:
        bases.append("WRONG_PHYSICAL")
    if isinstance(raw.get("duplicate_effect"), bool) and raw.get("duplicate_effect") is True:
        bases.append("DUPLICATE_EFFECT")

    joint = bool(terminal_usable and effect_usable and run_id and evidence_state == "SUFFICIENT")
    if joint and bases and stop == "CORRECT_STOP" and bases == []:
        joint = False
    if joint and bases:
        adjudication = "CONFIRMED_FAILURE"
        basis = "+".join(bases)
        reason = None
    elif joint and not bases and all(raw.get(key) is False for key in FAILURE_FLAGS):
        adjudication = "NO_CONFIRMED_FAILURE"
        basis = "NO_EFFECT_HISTORY_FAILURE_FLAG"
        reason = None
    else:
        adjudication = "UNRESOLVED"
        joint = False
        if conflicts:
            reason = "CONFLICT:" + ",".join(conflicts)
            basis = "CONFLICT"
        elif blocked:
            reason = "BLOCKED_NO_EFFECT_HISTORY"
            basis = "INSUFFICIENT_EVIDENCE"
        elif run_id is None:
            reason = "MISSING_RUN_ID"
            basis = "INSUFFICIENT_EVIDENCE"
        elif not has_effect_fields:
            reason = "EFFECT_HISTORY_NOT_ADJUDICABLE"
            basis = "INSUFFICIENT_EVIDENCE"
        else:
            reason = "JOINT_NOT_OBSERVED"
            basis = "INSUFFICIENT_EVIDENCE"
        terminal_usable = False if blocked or conflicts or not isinstance(raw.get("task_terminal_match"), bool) else terminal_usable
        effect_usable = False if not joint else effect_usable

    if not joint:
        terminal_usable = False
        effect_usable = False

    return {
        "source_summary_ref": source_summary_ref,
        "source_evidence_ref": source_evidence_ref,
        "source_artifact_ref": source_artifact_ref,
        "cohort": cohort,
        "proposal_id": cluster,
        "proposal_id_origin": proposal_origin,
        "proposal_cluster_key": cluster,
        "run_id": run_id,
        "task_id": task_id,
        "path": arm,
        "path_origin": "ORIGINAL_FIELD_arm" if arm else "ABSENT",
        "session_id": session_id,
        "session_id_origin": "ORIGINAL_FIELD" if session_id else "ABSENT",
        "fault_label": fault,
        "terminal_outcome": terminal,
        "authority_decision": authority,
        "physical_effect_state": physical_state,
        "evidence_state": evidence_state,
        "semantic_validity": semantic if joint or (terminal != "UNKNOWN" and not blocked and not conflicts) else "UNKNOWN",
        "recovery_state": recovery,
        "successor_obligation": obligation,
        "successor_status": successor,
        "stop_correctness": stop,
        "outdated_scenario_label": scenario_label(fault, cohort),
        "adjudication": adjudication,
        "adjudication_basis": basis,
        "joint_observed": joint,
        "unobserved_reason": reason,
        "terminal_adjudicated": bool(joint and terminal_usable),
        "effect_history_adjudicated": bool(joint and effect_usable),
    }


def cell_dirname(task_id: str, arm: str, fault: str) -> str:
    return f"{task_id}__{arm}__{fault}"


def artifact_in(cell: Path) -> Path | None:
    found = [path for path in cell.glob("*.json") if path.name not in {"evidence.json"}]
    if not found:
        return None
    found.sort(key=lambda path: path.name)
    return found[0]


def build_confirm(workspace: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
    root = workspace / CONFIRM_REL
    summary_path = root / "summary.json"
    anomalies: list[dict[str, str]] = []
    meta: dict[str, Any] = {"summary_path": summary_path.as_posix(), "readable": False}
    if not summary_path.is_file():
        anomalies.append({
            "anomaly_type": "MISSING_SOURCE",
            "cohort": "m4-confirm",
            "key": "",
            "detail": "summary.json absent",
        })
        meta["error"] = "summary.json absent"
        return [], anomalies, meta
    try:
        summary = read_json(summary_path)
    except json.JSONDecodeError as exc:
        anomalies.append({
            "anomaly_type": "CORRUPT_JSON",
            "cohort": "m4-confirm",
            "key": rel(summary_path, workspace),
            "detail": str(exc),
        })
        meta["error"] = "summary.json corrupt"
        return [], anomalies, meta
    meta["readable"] = True
    rows = summary.get("rows")
    if not isinstance(rows, list):
        meta["error"] = "rows absent"
        return [], anomalies, meta
    meta["header_n"] = summary.get("n")
    meta["header_blocked"] = summary.get("blocked")
    meta["row_count"] = len(rows)

    evidence_paths = {path.parent.name: path for path in root.glob("*/evidence.json")}
    seen_keys: set[tuple[str, str, str]] = set()
    run_ids: dict[str, str] = {}
    normalized: list[dict[str, Any]] = []
    preliminary: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            anomalies.append({
                "anomaly_type": "MALFORMED_ROW",
                "cohort": "m4-confirm",
                "key": str(index),
                "detail": "summary row is not an object",
            })
            continue
        task_id = row.get("task_id")
        arm = row.get("arm")
        fault = row.get("fault")
        summary_ref = f"{rel(summary_path, workspace)}#rows[{index}]"
        if not all(isinstance(value, str) for value in (task_id, arm, fault)):
            anomalies.append({
                "anomaly_type": "MISSING_IDENTIFIER",
                "cohort": "m4-confirm",
                "key": summary_ref,
                "detail": "task_id, arm, or fault absent; no identifier invented",
            })
            record = normalize_raw(
                row,
                cohort="m4-confirm",
                source_summary_ref=summary_ref,
                source_evidence_ref=None,
                source_artifact_ref=None,
                artifact_matches=None,
            )
            normalized.append(record)
            preliminary.append({**record, "raw_blocked": row.get("blocked"), "raw_task_terminal_match": row.get("task_terminal_match")})
            continue
        key = (task_id, arm, fault)
        if key in seen_keys:
            anomalies.append({
                "anomaly_type": "DUPLICATE_KEY",
                "cohort": "m4-confirm",
                "key": "|".join(key),
                "detail": "duplicate task_id, arm, fault in summary.json",
            })
        seen_keys.add(key)
        dirname = cell_dirname(task_id, arm, fault)
        evidence_path = evidence_paths.get(dirname)
        if row.get("run_id") is None:
            anomalies.append({
                "anomaly_type": "MISSING_RUN_ID",
                "cohort": "m4-confirm",
                "key": "|".join(key),
                "detail": "summary row has no run_id",
            })
        if row.get("blocked") is True and row.get("task_terminal_match") is False:
            anomalies.append({
                "anomaly_type": "TERMINAL_LABEL_ON_INSUFFICIENT_EVIDENCE",
                "cohort": "m4-confirm",
                "key": "|".join(key),
                "detail": "blocked row stores task_terminal_match false; not used as MISMATCH or FAILURE",
            })
        artifact_path = None
        artifact_matches = None
        evidence_ref = None
        canonical = row
        conflicts: list[str] = []
        if evidence_path is None:
            if row.get("blocked") is not True:
                anomalies.append({
                    "anomaly_type": "MISSING_LOG",
                    "cohort": "m4-confirm",
                    "key": "|".join(key),
                    "detail": "unblocked summary row has no evidence.json",
                })
            else:
                anomalies.append({
                    "anomaly_type": "MISSING_EVIDENCE_DIRECTORY",
                    "cohort": "m4-confirm",
                    "key": "|".join(key),
                    "detail": "blocked cell has no evidence.json directory",
                })
        else:
            evidence = read_json(evidence_path)
            evidence_ref = rel(evidence_path, workspace)
            if not isinstance(evidence, dict):
                anomalies.append({
                    "anomaly_type": "CORRUPT_JSON",
                    "cohort": "m4-confirm",
                    "key": evidence_ref,
                    "detail": "evidence.json is not an object",
                })
            else:
                if evidence.get("task_id") != task_id or evidence.get("arm") != arm or evidence.get("fault") != fault:
                    anomalies.append({
                        "anomaly_type": "CONFLICTING_MAPPING",
                        "cohort": "m4-confirm",
                        "key": "|".join(key),
                        "detail": "directory keys disagree with evidence.json keys",
                    })
                    conflicts.append("directory_key_mismatch")
                if evidence != row:
                    anomalies.append({
                        "anomaly_type": "CONFLICTING_SUMMARY_EVIDENCE",
                        "cohort": "m4-confirm",
                        "key": "|".join(key),
                        "detail": "summary row is not identical to evidence.json",
                    })
                    conflicts.append("summary_evidence_mismatch")
                else:
                    anomalies.append({
                        "anomaly_type": "DUPLICATE_ROLLUP",
                        "cohort": "m4-confirm",
                        "key": "|".join(key),
                        "detail": "summary.json repeats evidence.json; one observation retained",
                    })
                canonical = evidence
                artifact = artifact_in(evidence_path.parent)
                if artifact is not None:
                    artifact_path = rel(artifact, workspace)
                    file_sha = sha256_file(artifact)
                    physical = canonical.get("physical_digest")
                    artifact_matches = file_sha == physical
                    if artifact_matches is False:
                        anomalies.append({
                            "anomaly_type": "ARTIFACT_DIGEST_MISMATCH",
                            "cohort": "m4-confirm",
                            "key": "|".join(key),
                            "detail": f"sha256 {file_sha} != physical_digest {physical}",
                        })
                run_id = canonical.get("run_id")
                if isinstance(run_id, str):
                    if run_id in run_ids:
                        anomalies.append({
                            "anomaly_type": "DUPLICATE_RUN_ID",
                            "cohort": "m4-confirm",
                            "key": run_id,
                            "detail": f"also used by {run_ids[run_id]}",
                        })
                    else:
                        run_ids[run_id] = "|".join(key)
        record = normalize_raw(
            canonical,
            cohort="m4-confirm",
            source_summary_ref=summary_ref,
            source_evidence_ref=evidence_ref,
            source_artifact_ref=artifact_path,
            artifact_matches=artifact_matches,
            conflicts=conflicts + internal_conflicts(canonical, artifact_matches),
        )
        normalized.append(record)
        preliminary.append({
            **record,
            "raw_blocked": row.get("blocked"),
            "raw_task_terminal_match": row.get("task_terminal_match"),
            "raw_stale_credential_accepted": canonical.get("stale_credential_accepted"),
            "raw_interrupted_recovered": canonical.get("interrupted_recovered"),
            "raw_wrong_physical": canonical.get("wrong_physical"),
            "artifact_matches_physical_digest": artifact_matches,
        })

    for dirname, evidence_path in sorted(evidence_paths.items()):
        parts = dirname.split("__")
        if len(parts) != 3 or (parts[0], parts[1], parts[2]) not in seen_keys:
            anomalies.append({
                "anomaly_type": "ORPHAN_EVIDENCE",
                "cohort": "m4-confirm",
                "key": dirname,
                "detail": rel(evidence_path, workspace),
            })
    meta["preliminary"] = preliminary
    return normalized, anomalies, meta


def build_dedicated(workspace: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    root = workspace / DEDICATED_REL
    summary_path = root / "summary.json"
    anomalies: list[dict[str, str]] = []
    if not summary_path.is_file():
        anomalies.append({
            "anomaly_type": "MISSING_SOURCE",
            "cohort": "m4-dedicated",
            "key": "",
            "detail": "summary.json absent",
        })
        return [], anomalies
    summary = read_json(summary_path)
    rows = summary.get("rows") if isinstance(summary, dict) else None
    if not isinstance(rows, list):
        return [], anomalies
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        authority = row.get("authority")
        arm = row.get("arm")
        summary_ref = f"{rel(summary_path, workspace)}#rows[{index}]"
        artifact_ref = None
        artifact_matches = None
        if isinstance(authority, str) and isinstance(arm, str):
            candidate = root / f"{authority}-{arm}"
            artifact = artifact_in(candidate) if candidate.is_dir() else None
            if artifact is not None:
                artifact_ref = rel(artifact, workspace)
                artifact_matches = False if row.get("physical_digest") else None
                anomalies.append({
                    "anomaly_type": "ARTIFACT_WITHOUT_RUN_RECORD",
                    "cohort": "m4-dedicated",
                    "key": f"{authority}|{arm}",
                    "detail": artifact_ref,
                })
        else:
            anomalies.append({
                "anomaly_type": "MISSING_IDENTIFIER",
                "cohort": "m4-dedicated",
                "key": summary_ref,
                "detail": "authority or arm absent",
            })
        if not row.get("run_id"):
            anomalies.append({
                "anomaly_type": "MISSING_RUN_ID",
                "cohort": "m4-dedicated",
                "key": f"{authority}|{arm}",
                "detail": "dedicated summary row has no run_id and no terminal field",
            })
        record = normalize_raw(
            row,
            cohort="m4-dedicated",
            source_summary_ref=summary_ref,
            source_evidence_ref=None,
            source_artifact_ref=artifact_ref,
            artifact_matches=artifact_matches,
        )
        normalized.append(record)
    return normalized, anomalies


def collapse_duplicate_runs(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Keep one row per run_id. Conflicting copies stay unresolved and are not merged."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    passthrough: list[dict[str, Any]] = []
    anomalies: list[dict[str, str]] = []
    for record in records:
        run_id = record.get("run_id")
        if not run_id:
            passthrough.append(record)
            continue
        grouped[run_id].append(record)
    kept: list[dict[str, Any]] = []
    for run_id, copies in grouped.items():
        if len(copies) == 1:
            kept.append(copies[0])
            continue
        fingerprints = {json.dumps(copy, sort_keys=True, ensure_ascii=False) for copy in copies}
        if len(fingerprints) == 1:
            anomalies.append({
                "anomaly_type": "DUPLICATE_RUN_ID",
                "cohort": copies[0].get("cohort") or "",
                "key": run_id,
                "detail": f"{len(copies)} identical copies collapsed to one row",
            })
            kept.append(copies[0])
            continue
        anomalies.append({
            "anomaly_type": "CONFLICT",
            "cohort": copies[0].get("cohort") or "",
            "key": run_id,
            "detail": "duplicate run_id with disagreeing outcomes; joint not manufactured",
        })
        conflicted = dict(copies[0])
        conflicted["joint_observed"] = False
        conflicted["adjudication"] = "UNRESOLVED"
        conflicted["adjudication_basis"] = "CONFLICT"
        conflicted["unobserved_reason"] = "CONFLICT:duplicate_run_id_disagreement"
        conflicted["terminal_outcome"] = "UNKNOWN"
        conflicted["terminal_adjudicated"] = False
        conflicted["effect_history_adjudicated"] = False
        conflicted["authority_decision"] = "UNKNOWN"
        conflicted["physical_effect_state"] = "UNKNOWN"
        conflicted["semantic_validity"] = "UNKNOWN"
        kept.append(conflicted)
    return passthrough + kept, anomalies


def reconstruct_marginal_only(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": "NO-GO FOR ARCHIVED EFE",
        "joint_recoverable": False,
        "rows": [],
        "reason": "marginal aggregates only; intersections were not estimated",
        "input_kind": "marginal_only",
        "ignored_counts": {key: payload.get(key) for key in payload if key != "rows"},
    }


def classify_cell(record: dict[str, Any], raw_blocked: Any) -> str:
    if raw_blocked is True or record.get("unobserved_reason") == "BLOCKED_NO_EFFECT_HISTORY":
        return "BLOCKED"
    if record.get("unobserved_reason") and str(record.get("unobserved_reason")).startswith("MISSING"):
        return "MISSING_LOG"
    if record.get("cohort") == "m4-dedicated":
        return "PARTIAL"
    if record.get("joint_observed") is True and record.get("evidence_state") == "SUFFICIENT":
        return "COMPLETED"
    if record.get("source_evidence_ref"):
        return "PARTIAL"
    return "MISSING_LOG"


def proposal_rows(records: list[dict[str, Any]], preliminary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    blocked_lookup = {
        (item.get("proposal_cluster_key"), item.get("path")): item.get("raw_blocked")
        for item in preliminary
    }
    for record in records:
        if record.get("cohort") != "m4-confirm":
            continue
        by_key[str(record.get("proposal_cluster_key"))].append(record)
    output = []
    for key in sorted(by_key):
        group = by_key[key]
        statuses = [classify_cell(item, blocked_lookup.get((key, item.get("path")))) for item in group]
        output.append({
            "proposal_cluster_key": key,
            "proposal_id_origin": group[0].get("proposal_id_origin"),
            "task_id": group[0].get("task_id") or "",
            "fault_label": group[0].get("fault_label") or "",
            "n_cells": len(group),
            "n_completed": sum(status == "COMPLETED" for status in statuses),
            "n_blocked": sum(status == "BLOCKED" for status in statuses),
            "n_partial": sum(status == "PARTIAL" for status in statuses),
            "n_missing_log": sum(status == "MISSING_LOG" for status in statuses),
            "n_not_run": 0,
            "paths": " ".join(str(item.get("path")) for item in group),
            "blocked_proposal": all(status == "BLOCKED" for status in statuses) and len(statuses) > 0,
        })
    return output


def cell_rows(records: list[dict[str, Any]], preliminary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocked_lookup = {
        (item.get("cohort"), item.get("proposal_cluster_key"), item.get("path"), item.get("source_summary_ref")): item.get("raw_blocked")
        for item in preliminary
    }
    output = []
    for record in records:
        raw_blocked = blocked_lookup.get((
            record.get("cohort"),
            record.get("proposal_cluster_key"),
            record.get("path"),
            record.get("source_summary_ref"),
        ))
        output.append({
            "cohort": record.get("cohort"),
            "proposal_cluster_key": record.get("proposal_cluster_key") or "",
            "task_id": record.get("task_id") or "",
            "path": record.get("path") or "",
            "fault_label": record.get("fault_label") or "",
            "run_id": record.get("run_id") or "",
            "status": classify_cell(record, raw_blocked),
            "joint_observed": record.get("joint_observed"),
            "source_evidence_ref": record.get("source_evidence_ref") or "",
            "source_artifact_ref": record.get("source_artifact_ref") or "",
            "unobserved_reason": record.get("unobserved_reason") or "",
        })
    return output


def linkage_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for record in records:
        if record.get("source_evidence_ref") and record.get("run_id"):
            status = "LINKED_EVIDENCE"
        elif record.get("unobserved_reason") == "BLOCKED_NO_EFFECT_HISTORY":
            status = "SUMMARY_ONLY_BLOCKED"
        else:
            status = "UNLINKED"
        output.append({
            "cohort": record.get("cohort"),
            "link_status": status,
            "proposal_cluster_key": record.get("proposal_cluster_key") or "",
            "task_id": record.get("task_id") or "",
            "run_id": record.get("run_id") or "",
            "path": record.get("path") or "",
            "fault_label": record.get("fault_label") or "",
            "session_id": record.get("session_id") or "",
            "source_summary_ref": record.get("source_summary_ref") or "",
            "source_evidence_ref": record.get("source_evidence_ref") or "",
            "source_artifact_ref": record.get("source_artifact_ref") or "",
        })
    return output


def historical_pairs(preliminary: list[dict[str, Any]]) -> dict[str, Any]:
    """Recompute the archived H4/H5/H6 pair counts from stored fields.

    Blocked rows are excluded. Their stored terminal false is not a sample.
    """
    usable = [
        row for row in preliminary
        if row.get("cohort") == "m4-confirm" and row.get("raw_blocked") is not True and row.get("joint_observed") is True
    ]
    by_task: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in usable:
        by_task[(row.get("task_id"), row.get("fault_label"))][row.get("path")] = row

    def flag(row: dict[str, Any] | None, name: str) -> bool | None:
        if row is None:
            return None
        value = row.get(name)
        if isinstance(value, bool):
            return value
        return None

    h4_tasks = []
    for (task_id, fault), arms in sorted(by_task.items()):
        if fault != "S1":
            continue
        if not {"B0", "B1", "B2", "B3"} <= set(arms):
            continue
        terminals = {arm: flag(arms[arm], "raw_task_terminal_match") for arm in ("B0", "B1", "B2", "B3")}
        safeties = {arm: flag(arms[arm], "raw_stale_credential_accepted") for arm in ("B0", "B1", "B2", "B3")}
        if any(value is None for value in terminals.values()) or any(value is None for value in safeties.values()):
            continue
        same_terminal = len(set(terminals.values())) == 1
        different_safety = len(set(safeties.values())) > 1
        h4_tasks.append({
            "task_id": task_id,
            "same_terminal": same_terminal,
            "different_safety": different_safety,
        })
    h5 = []
    h6 = []
    for (task_id, fault), arms in sorted(by_task.items()):
        if fault == "S1" and "B1" in arms and "B2" in arms:
            left = flag(arms["B1"], "raw_stale_credential_accepted")
            right = flag(arms["B2"], "raw_stale_credential_accepted")
            if left is not None and right is not None:
                h5.append({"task_id": task_id, "b1_minus_b2": int(left) - int(right)})
        if fault == "S3" and "B3" in arms and "B2" in arms:
            left = flag(arms["B3"], "raw_interrupted_recovered")
            right = flag(arms["B2"], "raw_interrupted_recovered")
            if left is not None and right is not None:
                h6.append({"task_id": task_id, "b3_minus_b2": int(left) - int(right)})
    h4_hits = sum(1 for item in h4_tasks if item["same_terminal"] and item["different_safety"])
    return {
        "h4_task_pairs": h4_tasks,
        "h4_rate": (h4_hits / len(h4_tasks)) if h4_tasks else None,
        "h4_numerator": h4_hits,
        "h4_denominator_tasks": len(h4_tasks),
        "h5_pair_diffs": h5,
        "h6_pair_diffs": h6,
        "note": "H6 uses the archived interrupted_recovered flag. That flag is recovery state, not CONFIRMED_EFFECT.",
    }


def efe_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    joint = [row for row in records if row.get("cohort") == "m4-confirm" and row.get("joint_observed") is True]
    failures = [row for row in joint if row.get("adjudication") == "CONFIRMED_FAILURE"]
    successes = [row for row in joint if row.get("terminal_outcome") == "MATCH"]
    success_failures = [row for row in successes if row.get("adjudication") == "CONFIRMED_FAILURE"]
    return {
        "definition": "EFE counts jointly observed confirm runs whose adjudication is CONFIRMED_FAILURE. EFE@success restricts that count to terminal_outcome MATCH. Blocked rows are outside both denominators.",
        "joint_n": len(joint),
        "efe_numerator": len(failures),
        "efe_denominator": len(joint),
        "efe": (len(failures) / len(joint)) if joint else None,
        "efe_at_success_numerator": len(success_failures),
        "efe_at_success_denominator": len(successes),
        "efe_at_success": (len(success_failures) / len(successes)) if successes else None,
        "identifiable_on_joint_subset": bool(joint) and bool(successes),
        "basis_counts": dict(Counter(row.get("adjudication_basis") for row in failures)),
        "success_basis_counts": dict(Counter(row.get("adjudication_basis") for row in success_failures)),
    }


def joint_tables(records: list[dict[str, Any]]) -> dict[str, Any]:
    joint = [row for row in records if row.get("cohort") == "m4-confirm" and row.get("joint_observed") is True]
    def table(left: str, right: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in joint:
            key = f"{row.get(left)}|{row.get(right)}"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))
    return {
        "terminal_by_authority": table("terminal_outcome", "authority_decision"),
        "terminal_by_physical": table("terminal_outcome", "physical_effect_state"),
        "terminal_by_recovery": table("terminal_outcome", "recovery_state"),
        "fault_by_authority": table("fault_label", "authority_decision"),
        "note": "Counts use jointly observed confirm rows only. Blocked cells are not filled in as MISMATCH or as NO_STALE_ACCEPTANCE_RECORDED.",
    }


def decide(records: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    confirm = [row for row in records if row.get("cohort") == "m4-confirm"]
    joint = [row for row in confirm if row.get("joint_observed") is True]
    unresolved = [row for row in confirm if row.get("joint_observed") is not True]
    if not meta.get("readable"):
        decision = "NO-GO FOR ARCHIVED EFE"
        reason = "confirm manifest unreadable; joint distribution was not estimated"
    elif not confirm:
        decision = "NO-GO FOR ARCHIVED EFE"
        reason = "no confirm rows; joint distribution was not estimated"
    elif not joint:
        decision = "NO-GO FOR ARCHIVED EFE"
        reason = "no run-level joint of terminal outcome and effect history"
    elif len(joint) < len(confirm):
        decision = "CONDITIONAL GO"
        reason = "a subset of confirm cells has a run-level joint; blocked cells do not, and those intersections were not imputed"
    else:
        decision = "GO"
        reason = "every confirm cell has a run-level joint of terminal outcome and effect history"
    return {
        "decision": decision,
        "reason": reason,
        "confirm_rows": len(confirm),
        "joint_observed": len(joint),
        "unresolved_confirm": len(unresolved),
        "header_n": meta.get("header_n"),
        "header_blocked": meta.get("header_blocked"),
        "header_matches_rows": meta.get("header_n") == meta.get("row_count"),
    }


def select_examples(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def first(predicate: Any) -> dict[str, Any] | None:
        for record in records:
            if predicate(record):
                return record
        return None

    chosen = [
        ("archived", "joint terminal match with recorded stale acceptance", first(
            lambda row: row.get("joint_observed") is True and row.get("terminal_outcome") == "MATCH" and row.get("authority_decision") == "STALE_ACCEPTED"
        )),
        ("archived", "blocked cell stays UNRESOLVED; stored terminal false is not FAILURE", first(
            lambda row: row.get("unobserved_reason") == "BLOCKED_NO_EFFECT_HISTORY"
        )),
        ("archived", "S3/B3 restored bytes that are not the legal digest", first(
            lambda row: row.get("recovery_state") == "RESTORED_BYTES" and row.get("physical_effect_state") == "NOT_LEGAL"
        )),
        ("archived", "restored bytes that also match the archived legal digest; recovery flag is not the basis", first(
            lambda row: row.get("recovery_state") == "RESTORED_BYTES" and row.get("physical_effect_state") == "CONFIRMED_EFFECT"
        )),
        ("archived", "dedicated revoke records CORRECT_STOP without a successor failure", first(
            lambda row: row.get("cohort") == "m4-dedicated" and row.get("stop_correctness") == "CORRECT_STOP"
        )),
    ]
    examples = []
    for origin, note, record in chosen:
        if record is not None:
            examples.append({"example_origin": origin, "illustrates": note, "record": record})
    examples.extend(synthetic_examples())
    return examples


def synthetic_examples() -> list[dict[str, Any]]:
    """Constraint illustrations. These rows are not archived observations."""
    outdated = normalize_raw(
        {"fault": "S2", "arm": "B1", "task_id": "synthetic-outdated", "blocked": True, "task_terminal_match": False},
        cohort="m4-confirm",
        source_summary_ref="synthetic:outdated",
        source_evidence_ref=None,
        source_artifact_ref=None,
        artifact_matches=None,
    )
    unknown = normalize_raw(
        {
            "task_id": "synthetic-unknown",
            "arm": "B0",
            "fault": "S1",
            "blocked": True,
            "error_type": "model_no_effect",
            "task_terminal_match": False,
        },
        cohort="m4-confirm",
        source_summary_ref="synthetic:unknown",
        source_evidence_ref=None,
        source_artifact_ref=None,
        artifact_matches=None,
    )
    restored = normalize_raw(
        {
            "task_id": "synthetic-restored",
            "arm": "B3",
            "fault": "S3",
            "run_id": "synthetic-restored-run",
            "blocked": False,
            "task_terminal_match": False,
            "stale_credential_accepted": False,
            "wrong_physical": True,
            "duplicate_effect": False,
            "verifiable_effect": True,
            "physical_digest": "old",
            "legal_digest": "legal",
            "interrupted_recovered": True,
            "successor_applicable": True,
            "successor_completed": False,
            "correct_stop": False,
            "receiver": {"accept_log": [], "physical_digest": "old", "accepted_count": 1},
        },
        cohort="m4-confirm",
        source_summary_ref="synthetic:restored",
        source_evidence_ref="synthetic:restored",
        source_artifact_ref=None,
        artifact_matches=None,
    )
    correct_stop = normalize_raw(
        {
            "authority": "revoke",
            "arm": "B2",
            "successor_applicable": False,
            "successor_completed": False,
            "correct_stop": True,
            "stale_credential_accepted": False,
        },
        cohort="m4-dedicated",
        source_summary_ref="synthetic:correct-stop",
        source_evidence_ref=None,
        source_artifact_ref=None,
        artifact_matches=None,
    )
    return [
        {"example_origin": "synthetic", "illustrates": "OUTDATED scenario label does not become stale authorization acceptance", "record": outdated},
        {"example_origin": "synthetic", "illustrates": "UNKNOWN blocked terminal false does not become FAILURE", "record": unknown},
        {"example_origin": "synthetic", "illustrates": "RESTORED_BYTES with a non-legal digest does not become CONFIRMED_EFFECT", "record": restored},
        {"example_origin": "synthetic", "illustrates": "CORRECT_STOP does not become a successor failure", "record": correct_stop},
    ]


def inventory_files(workspace: Path) -> list[dict[str, Any]]:
    files: list[Path] = []
    for item in INVENTORY_ROOTS:
        target = workspace / item
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            for path in target.rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    files.append(path)
    rows = []
    for path in sorted(set(files), key=lambda item: rel(item, workspace)):
        stat = path.stat()
        suffix = path.suffix.lower().lstrip(".") or "none"
        notes = ""
        readable = True
        try:
            if suffix == "json":
                read_json(path)
            elif suffix in {"md", "txt", "csv", "log", "py", "jsonl"}:
                path.read_text(encoding="utf-8")
            else:
                path.read_bytes()
                notes = "binary_or_non_text"
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            readable = False
            notes = f"unreadable:{type(exc).__name__}"
        rows.append({
            "relative_path": rel(path, workspace),
            "format": suffix,
            "size_bytes": stat.st_size,
            "mtime_iso": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "sha256": sha256_file(path) if readable or path.is_file() else "",
            "readable": str(readable).lower(),
            "notes": notes,
        })
    rows.append({
        "relative_path": "EpochGate-conference.pdf",
        "format": "pdf",
        "size_bytes": "",
        "mtime_iso": "",
        "sha256": "",
        "readable": "false",
        "notes": "NOT_FOUND. Recursive search of the workspace found no .pdf file. No page text was extracted.",
    })
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            rendered = {}
            for column in columns:
                value = row.get(column)
                if isinstance(value, bool):
                    rendered[column] = "true" if value else "false"
                elif value is None:
                    rendered[column] = ""
                else:
                    rendered[column] = value
            writer.writerow(rendered)


def write_manifest(workspace: Path, out_dir: Path) -> dict[str, Any]:
    rows = inventory_files(workspace)
    manifest_path = out_dir / "raw_manifest.csv"
    write_csv(
        manifest_path,
        rows,
        ["relative_path", "format", "size_bytes", "mtime_iso", "sha256", "readable", "notes"],
    )
    digest = sha256_file(manifest_path)
    hash_path = out_dir / "raw_manifest.sha256"
    hash_path.write_text(f"{digest}  raw_manifest.csv\n", encoding="utf-8")
    unreadable = [row for row in rows if row["readable"] != "true"]
    return {
        "files_inventoried": len(rows),
        "unreadable": unreadable,
        "manifest_sha256": digest,
    }


def s3_b3_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        row for row in records
        if row.get("cohort") == "m4-confirm" and row.get("fault_label") == "S3" and row.get("path") == "B3"
    ]
    return [
        {
            "task_id": row.get("task_id"),
            "run_id": row.get("run_id"),
            "recovery_state": row.get("recovery_state"),
            "physical_effect_state": row.get("physical_effect_state"),
            "terminal_outcome": row.get("terminal_outcome"),
            "semantic_validity": row.get("semantic_validity"),
            "adjudication": row.get("adjudication"),
            "adjudication_basis": row.get("adjudication_basis"),
            "joint_observed": row.get("joint_observed"),
            "source_evidence_ref": row.get("source_evidence_ref"),
        }
        for row in selected
    ]


def load_m6(workspace: Path) -> dict[str, Any] | None:
    path = workspace / M6_REL
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except json.JSONDecodeError:
        return {"unreadable": True, "path": rel(path, workspace)}
    if isinstance(payload, dict):
        payload = dict(payload)
        payload["_path"] = rel(path, workspace)
    return payload


def build_validation(
    records: list[dict[str, Any]],
    anomalies: list[dict[str, str]],
    meta: dict[str, Any],
    schema_errors: list[dict[str, Any]],
    inventory: dict[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    decision = decide(records, meta)
    preliminary = meta.get("preliminary") or []
    proposals = proposal_rows(records, preliminary)
    blocked_proposals = [row for row in proposals if row["blocked_proposal"]]
    cells = cell_rows(records, preliminary)
    confirm_cells = [row for row in cells if row["cohort"] == "m4-confirm"]
    m6 = load_m6(workspace)
    pairs = historical_pairs(preliminary)
    comparison = {
        "m6_path": None if not m6 else m6.get("_path"),
        "m6_confirm_n": None if not m6 else m6.get("confirm_n"),
        "m6_h4": None if not m6 else m6.get("h4_same_terminal_different_safety"),
        "m6_h5_n": None if not isinstance(m6, dict) or not isinstance(m6.get("h5"), dict) else m6["h5"].get("n"),
        "m6_h6_n": None if not isinstance(m6, dict) or not isinstance(m6.get("h6"), dict) else m6["h6"].get("n"),
        "recomputed_h4_rate": pairs.get("h4_rate"),
        "recomputed_h5_n": len(pairs.get("h5_pair_diffs") or []),
        "recomputed_h6_n": len(pairs.get("h6_pair_diffs") or []),
        "h4_matches_m6": pairs.get("h4_rate") == (None if not m6 else m6.get("h4_same_terminal_different_safety")),
        "h5_n_matches_m6": len(pairs.get("h5_pair_diffs") or []) == (None if not isinstance(m6, dict) or not isinstance(m6.get("h5"), dict) else m6["h5"].get("n")),
        "h6_n_matches_m6": len(pairs.get("h6_pair_diffs") or []) == (None if not isinstance(m6, dict) or not isinstance(m6.get("h6"), dict) else m6["h6"].get("n")),
    }
    return {
        "decision": decision["decision"],
        "decision_reason": decision["reason"],
        "joint_recoverable": decision["joint_observed"] > 0,
        "partial_coverage": decision["decision"] == "CONDITIONAL GO",
        "counts": {
            "files_inventoried": inventory.get("files_inventoried"),
            "confirm_header_n": meta.get("header_n"),
            "confirm_header_blocked": meta.get("header_blocked"),
            "confirm_rows": decision["confirm_rows"],
            "joint_observed": decision["joint_observed"],
            "unresolved_confirm": decision["unresolved_confirm"],
            "dedicated_rows": sum(1 for row in records if row.get("cohort") == "m4-dedicated"),
            "shared_proposals": len(proposals),
            "blocked_proposals": len(blocked_proposals),
            "cells": len(confirm_cells),
            "blocked_cells": sum(1 for row in confirm_cells if row["status"] == "BLOCKED"),
            "completed_cells": sum(1 for row in confirm_cells if row["status"] == "COMPLETED"),
            "partial_cells": sum(1 for row in confirm_cells if row["status"] == "PARTIAL"),
            "missing_log_cells": sum(1 for row in confirm_cells if row["status"] == "MISSING_LOG"),
            "not_run_cells": sum(1 for row in confirm_cells if row["status"] == "NOT_RUN"),
            "linkage_anomaly_rows": len(anomalies),
            "schema_errors": len(schema_errors),
        },
        "anomalies_by_type": dict(sorted(Counter(item["anomaly_type"] for item in anomalies).items())),
        "blocked_proposal_keys": [row["proposal_cluster_key"] for row in blocked_proposals],
        "cell_status_counts": dict(sorted(Counter(row["status"] for row in confirm_cells).items())),
        "joint_tables": joint_tables(records),
        "efe": efe_summary(records),
        "historical_pairs": pairs,
        "m6_comparison": comparison,
        "s3_b3": s3_b3_rows(records),
        "figure2": {
            "pdf": "NOT_FOUND",
            "published_joint_table": "ABSENT",
            "comparison_target": "results/runtime/correctness/m6/summary.json marginal pair statistics, not a figure cell table",
            "imputation": "none",
        },
        "field_absence": {
            "proposal_id": "ABSENT on original records; proposal_id column is a derived cluster key",
            "session_id": "ABSENT",
            "timestamp": "ABSENT as a wall-clock field; native.timeline[].t is a monotonic counter and was not used for linkage",
            "token_was_stale": "ABSENT; stale_credential_accepted false is not rewritten as a stale rejection",
        },
        "process_kill_is_not_power_loss": True,
        "schema_valid": not schema_errors,
        "schema_errors": schema_errors,
        "inaccessible_sources": inventory.get("unreadable"),
        "header_matches_rows": decision["header_matches_rows"],
    }


def run(workspace: Path, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    inventory = write_manifest(workspace, out_dir)
    confirm_records, confirm_anomalies, meta = build_confirm(workspace)
    dedicated_records, dedicated_anomalies = build_dedicated(workspace)
    records = confirm_records + dedicated_records
    anomalies = confirm_anomalies + dedicated_anomalies
    schema = load_schema()
    schema_errors = []
    for record in records:
        errors = validate_outcome(record, schema)
        if errors:
            schema_errors.append({"run_id": record.get("run_id"), "task_id": record.get("task_id"), "path": record.get("path"), "errors": errors})
    report = build_validation(records, anomalies, meta, schema_errors, inventory, workspace)
    preliminary = meta.get("preliminary") or []
    for record in dedicated_records:
        preliminary.append({
            **record,
            "raw_blocked": None,
            "raw_task_terminal_match": None,
            "raw_stale_credential_accepted": None,
            "raw_interrupted_recovered": None,
            "raw_wrong_physical": None,
            "artifact_matches_physical_digest": None,
        })
    write_csv(out_dir / "archived_joint.csv", records, CSV_COLUMNS)
    preliminary_columns = CSV_COLUMNS + [
        "raw_blocked",
        "raw_task_terminal_match",
        "raw_stale_credential_accepted",
        "raw_interrupted_recovered",
        "raw_wrong_physical",
        "artifact_matches_physical_digest",
    ]
    write_csv(out_dir / "archived_joint_preliminary.csv", preliminary, preliminary_columns)
    write_csv(out_dir / "proposal_registry.csv", proposal_rows(records, preliminary), [
        "proposal_cluster_key", "proposal_id_origin", "task_id", "fault_label", "n_cells",
        "n_completed", "n_blocked", "n_partial", "n_missing_log", "n_not_run", "paths", "blocked_proposal",
    ])
    write_csv(out_dir / "cell_status.csv", cell_rows(records, preliminary), [
        "cohort", "proposal_cluster_key", "task_id", "path", "fault_label", "run_id", "status",
        "joint_observed", "source_evidence_ref", "source_artifact_ref", "unobserved_reason",
    ])
    write_csv(out_dir / "run_linkage.csv", linkage_rows(records), [
        "cohort", "link_status", "proposal_cluster_key", "task_id", "run_id", "path", "fault_label",
        "session_id", "source_summary_ref", "source_evidence_ref", "source_artifact_ref",
    ])
    write_csv(out_dir / "linkage_anomalies.csv", anomalies, ["anomaly_type", "cohort", "key", "detail"])
    examples = select_examples(records)
    example_errors = []
    for item in examples:
        errors = validate_outcome(item["record"], schema)
        if errors:
            example_errors.append({"illustrates": item["illustrates"], "errors": errors})
    report["example_schema_errors"] = example_errors
    (out_dir / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (out_dir / "outcome_examples.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for item in examples:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    report["wrote"] = str(out_dir)
    return report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Reconstruct archived EpochGate joint outcomes without imputation.")
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    report = run(args.workspace, args.out)
    print(json.dumps({
        "decision": report["decision"],
        "counts": report["counts"],
        "efe": report["efe"],
        "schema_valid": report["schema_valid"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
