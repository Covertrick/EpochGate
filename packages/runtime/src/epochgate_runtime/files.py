"""在账本事务里发布暂存文件。成功要能对上文件摘要。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from epochgate_runtime.ledger import RunLedger


def _inside(root: Path, candidate: Path) -> bool:
    resolved = candidate.resolve()
    base = root.resolve()
    return resolved == base or base in resolved.parents


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish_file(
    ledger: RunLedger,
    *,
    workspace: Path,
    run_id: str,
    idempotency_key: str,
    fencing_token: int,
    relative_path: str,
    staging_path: Path,
) -> dict[str, Any]:
    if relative_path.startswith(("/", "\\")) or ".." in Path(relative_path).parts or ":" in relative_path:
        return {"published": False, "status": "rejected", "reason": "path"}
    existing = ledger.effect_row(idempotency_key)
    if existing is not None and existing["phase"] == "completed":
        return {"published": False, "status": "replayed", "receipt": existing["receipt"]}
    target = (workspace / relative_path)
    if not _inside(workspace, target) or not _inside(workspace, staging_path) or not staging_path.is_file():
        return {"published": False, "status": "rejected", "reason": "path"}
    digest = _sha256(staging_path)
    claim = ledger.claim_publish(run_id, idempotency_key, fencing_token, digest)
    if claim["status"] == "replayed":
        staging_path.unlink(missing_ok=True)
        return {"published": False, "status": "replayed", "receipt": claim.get("receipt"), "sha256": digest}
    if claim["status"] != "claimed":
        staging_path.unlink(missing_ok=True)
        return {"published": False, "status": "withheld", "sha256": digest}
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging_path, target)
    if _sha256(target) != digest:
        ledger.mark_effect_state(idempotency_key, "OUTCOME_UNKNOWN")
        return {"published": False, "status": "unknown", "sha256": digest}
    receipt = f"file:{relative_path}:{digest}"
    return ledger.complete_claimed_publish(run_id, idempotency_key, digest, receipt, path=relative_path)


def reconcile_file(
    ledger: RunLedger,
    *,
    workspace: Path,
    run_id: str,
    idempotency_key: str,
    relative_path: str,
) -> dict[str, Any]:
    target = workspace / relative_path
    if not target.is_file():
        return {"status": "unknown", "published": False}
    digest = _sha256(target)
    receipt = f"file:{relative_path}:{digest}"
    return ledger.complete_claimed_publish(run_id, idempotency_key, digest, receipt, path=relative_path)
