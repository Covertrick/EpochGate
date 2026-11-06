"""EpochGate 2.0 的 checkpoint、泄漏和复现审计。不调用模型。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

REQUIRED_CHECKPOINT = (
    "checkpoint_id",
    "parent_episode_id",
    "branch_id",
    "session_id",
    "event_prefix_hash",
    "boundary_seq",
    "base_commit",
    "workspace_hash",
    "image_digest",
    "config_hash",
    "evidence_read_set",
)
PREFIX_FIELDS = ("event_prefix_hash", "workspace_hash", "image_digest", "config_hash", "base_commit", "boundary_seq")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SECRET_ENV = {"DS_API_KEY", "DEEPSEEK_API_KEY"}
LEAK_MARKERS = ("reference_recovery_witness", "gold_patch", "FAIL_TO_PASS")


def audit_checkpoint(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        for key in REQUIRED_CHECKPOINT:
            if key not in record:
                errors.append(f"{record.get('branch_id', '?')} missing {key}")
        if not SHA40.match(str(record.get("base_commit", ""))):
            errors.append(f"{record.get('branch_id', '?')} base_commit is not a full sha")
        for item in record.get("evidence_read_set") or []:
            for field in ("path", "content_hash", "read_at", "repo_version"):
                if field not in item:
                    errors.append(f"evidence missing {field}")
            if not SHA40.match(str(item.get("repo_version", ""))):
                errors.append("evidence repo_version is not a full sha")
        if record.get("open_side_effects"):
            errors.append(f"{record.get('branch_id')} checkpoint has open side effects")
        checkpoint_id = str(record.get("checkpoint_id"))
        previous = groups.get(checkpoint_id)
        if previous is None:
            groups[checkpoint_id] = record
            continue
        for field in PREFIX_FIELDS:
            if previous.get(field) != record.get(field):
                errors.append(f"fork prefix mismatch on {field}")
        if previous.get("session_id") != record.get("session_id"):
            errors.append("fork session_id mismatch")
    return errors


def audit_gold_leakage(view: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    env = view.get("env") or {}
    for name in SECRET_ENV:
        if name in env and env[name]:
            errors.append(f"secret env visible: {name}")
    blob = "\n".join(
        [
            str(view.get("prompt", "")),
            "\n".join(view.get("mounts") or []),
            "\n".join(view.get("tool_dirs") or []),
            "\n".join(view.get("retrieval") or []),
            "\n".join(view.get("git_objects") or []),
        ]
    )
    for marker in LEAK_MARKERS:
        if marker in blob:
            errors.append(f"private marker visible: {marker}")
    future = set(view.get("future_commits") or [])
    visible = set(view.get("git_objects") or [])
    leaked = sorted(future & visible)
    if leaked:
        errors.append(f"future commits visible: {','.join(leaked)}")
    for mount in view.get("mounts") or []:
        normalized = str(mount).replace("\\", "/").lower()
        if normalized.endswith("/.env") or "/gold/" in normalized or normalized.endswith(".env"):
            errors.append(f"forbidden mount: {mount}")
    return errors


def audit_reproducibility(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("dsh_version") != "0.2.2-rc.1":
        errors.append("dsh_version is not pinned to 0.2.2-rc.1")
    digest = str(manifest.get("image_digest", ""))
    if not digest.startswith("sha256:"):
        errors.append("image_digest missing")
    for name in ("base_commit", "change_commit"):
        if not SHA40.match(str(manifest.get(name, ""))):
            errors.append(f"{name} is not a full sha")
    if manifest.get("model_alias") != "deepseek-flash":
        errors.append("model_alias must be deepseek-flash")
    for name in ("reported_model", "run_date", "raw_response_sha256", "score_sha256"):
        if not manifest.get(name):
            errors.append(f"{name} missing")
    kind = manifest.get("sample_kind")
    if kind not in {"trace_replay", "fresh_api_rerun"}:
        errors.append("sample_kind must separate replay from fresh sampling")
    if kind == "trace_replay" and manifest.get("counts_as_new_model_sample"):
        errors.append("trace replay is not a new model sample")
    return errors


def fork_checkpoint(snapshot: dict[str, Any], branches: list[str]) -> list[dict[str, Any]]:
    if snapshot.get("open_side_effects"):
        raise ValueError("checkpoint has open side effects")
    rows = []
    for branch in branches:
        row = dict(snapshot)
        row["branch_id"] = branch
        row["open_side_effects"] = False
        rows.append(row)
    errors = audit_checkpoint(rows)
    if errors:
        raise ValueError("; ".join(errors))
    return rows


def canonical_hash(payload: dict[str, Any]) -> str:
    import json

    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
