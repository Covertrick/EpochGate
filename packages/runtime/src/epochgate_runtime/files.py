"""在账本事务里发布暂存文件。成功要能对上文件摘要。"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
    after_hash: Any | None = None,
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
    if os.environ.get("EG_CRASH_AFTER_CLAIM") == "1":
        return {"published": False, "status": "crashed_before_receipt", "sha256": digest}
    if after_hash is not None:
        after_hash()
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging_path, target)
    if _sha256(target) != digest:
        ledger.mark_effect_state(idempotency_key, "OUTCOME_UNKNOWN")
        return {"published": False, "status": "unknown", "sha256": digest}
    receipt = f"file:{relative_path}:{digest}"
    return ledger.complete_claimed_publish(run_id, idempotency_key, digest, receipt, path=relative_path)


def _pause(point: str) -> None:
    if os.environ.get("EPOCHGATE_PAUSE_AT") == point:
        time.sleep(30)


def _private_name(idempotency_key: str) -> str:
    """Windows 文件名不能带冒号；用百分号编码保持可逆且不碰撞。"""
    return quote(idempotency_key, safe="")


def _seal_private(directory: Path, name: str, payload: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    _pause("after_private_create")
    private = directory / name
    with private.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return private


def publish_frozen_file(
    ledger: RunLedger,
    *,
    workspace: Path,
    run_id: str,
    idempotency_key: str,
    fencing_token: int,
    relative_path: str,
    staging_path: Path,
    after_seal: Any | None = None,
    crash_point: str | None = None,
    check_version: bool = True,
    write_receipt: bool = True,
) -> dict[str, Any]:
    """发布已经封存的字节，不在替换时再读不可信暂存路径。

    原子替换是 os.replace。进程崩溃后能否恢复由调用方重新打开账本决定。
    掉电后是否仍在磁盘上，这里不测，也不当作已经保证。
    """
    durability = {"atomic_replace": True, "process_crash_recoverable": True, "power_loss_persistent": None}
    if relative_path.startswith(("/", "\\")) or ".." in Path(relative_path).parts or ":" in relative_path:
        return {"published": False, "status": "rejected", "reason": "path", "durability": durability}
    if not _inside(workspace, staging_path) or not staging_path.is_file():
        return {"published": False, "status": "rejected", "reason": "path", "durability": durability}
    payload = staging_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    claim = ledger.claim_publish(run_id, idempotency_key, fencing_token, digest, check_version=check_version)
    if claim["status"] == "replayed":
        return {"published": False, "status": "replayed", "receipt": claim.get("receipt"), "sha256": digest, "durability": durability}
    if claim["status"] == "conflict":
        return {"published": False, "status": "conflict", "receipt": claim.get("receipt"), "sha256": digest, "durability": durability}
    if claim["status"] != "claimed":
        return {"published": False, "status": "withheld", "sha256": digest, "durability": durability}
    private = _seal_private(workspace / ".eg-private", _private_name(idempotency_key), payload)
    _pause("after_private_write")
    if hashlib.sha256(private.read_bytes()).hexdigest() != digest:
        ledger.mark_effect_state(idempotency_key, "OUTCOME_UNKNOWN")
        return {"published": False, "status": "unknown", "sha256": digest, "durability": durability}
    if after_seal is not None:
        after_seal()
    if crash_point == "after_claim":
        return {"published": False, "status": "crashed_before_replace", "sha256": digest, "durability": durability}
    target = workspace / relative_path
    if not _inside(workspace, target):
        return {"published": False, "status": "rejected", "reason": "path", "sha256": digest, "durability": durability}
    prior_existed = target.is_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(private, target)
    _pause("after_replace")
    if crash_point == "after_replace":
        return {
            "published": False,
            "status": "crashed_before_receipt",
            "sha256": digest,
            "prior_version_checked": False,
            "unintended_overwrite": prior_existed,
            "durability": durability,
        }
    if _sha256(target) != digest:
        ledger.mark_effect_state(idempotency_key, "OUTCOME_UNKNOWN")
        return {
            "published": False,
            "status": "unknown",
            "sha256": digest,
            "prior_version_checked": False,
            "unintended_overwrite": prior_existed,
            "durability": durability,
        }
    if not write_receipt:
        return {
            "published": True,
            "status": "published_without_receipt",
            "sha256": digest,
            "prior_version_checked": False,
            "unintended_overwrite": prior_existed,
            "durability": durability,
        }
    receipt = f"file:{relative_path}:{digest}"
    completed = ledger.complete_claimed_publish(run_id, idempotency_key, digest, receipt, path=relative_path)
    _pause("after_receipt")
    completed.update({
        "sha256": digest,
        "prior_version_checked": False,
        "unintended_overwrite": prior_existed,
        "durability": durability,
        "physical_payload_matches_claim": _sha256(target) == digest,
    })
    return completed


def recover_frozen_publish(
    ledger: RunLedger,
    *,
    workspace: Path,
    run_id: str,
    idempotency_key: str,
    relative_path: str,
) -> dict[str, Any]:
    """从私有封存文件恢复。不回到不可信暂存路径取字节。"""
    private = workspace / ".eg-private" / _private_name(idempotency_key)
    target = workspace / relative_path

    def work() -> dict[str, Any]:
        existing = ledger.effect_row(idempotency_key)
        if existing is None or existing.get("run_id") != run_id:
            return {"published": False, "status": "missing", "protocol": "frozen_private"}
        digest = existing.get("result")
        receipt = f"file:{relative_path}:{digest}"
        if existing["phase"] == "completed":
            return {"published": False, "status": "replayed", "receipt": existing.get("receipt"), "protocol": "frozen_private"}
        if target.is_file() and _sha256(target) == digest:
            ledger.stage_effect_open(idempotency_key, phase="completed", effect_state="RESULT_PERSISTED", receipt=receipt)
            ledger._event_open(run_id, "file_published", {"path": relative_path, "receipt": receipt, "sha256": digest, "recovered": "receipt"})
            return {"published": True, "status": "completed", "receipt": receipt, "sha256": digest, "replaced": False, "protocol": "frozen_private"}
        if target.is_file() and _sha256(target) != digest:
            return {"published": False, "status": "conflict", "protocol": "frozen_private", "prior_version_checked": False}
        if not private.is_file() or hashlib.sha256(private.read_bytes()).hexdigest() != digest:
            ledger.stage_effect_open(idempotency_key, phase="unknown", effect_state="OUTCOME_UNKNOWN")
            ledger._event_open(run_id, "outcome_unknown", {"idempotency_key": idempotency_key, "reason": "private_missing"})
            return {"published": False, "status": "unknown", "protocol": "frozen_private"}
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(private, target)
        if _sha256(target) != digest:
            ledger.stage_effect_open(idempotency_key, phase="unknown", effect_state="OUTCOME_UNKNOWN")
            return {"published": False, "status": "unknown", "protocol": "frozen_private"}
        ledger.stage_effect_open(idempotency_key, phase="completed", effect_state="RESULT_PERSISTED", receipt=receipt)
        ledger._event_open(run_id, "file_published", {"path": relative_path, "receipt": receipt, "sha256": digest, "recovered": "private"})
        return {"published": True, "status": "completed", "receipt": receipt, "sha256": digest, "replaced": True, "protocol": "frozen_private"}

    return ledger.with_immediate(work)


def recover_claimed_publish(
    ledger: RunLedger,
    *,
    workspace: Path,
    run_id: str,
    idempotency_key: str,
    relative_path: str,
    staging_path: Path,
    before_replace: Any | None = None,
) -> dict[str, Any]:
    """协作式 Publisher 的恢复。

    调用者要打开同一本账本，并给出对得上的 run 和幂等键。只拿着摘要来替换文件不算恢复。
    写事务包住检查、替换和回执，所以另一个走这本账本的恢复者会等到第一次提交。
    这把锁挡不住不走账本的外部写入者。``os.replace`` 会覆盖目标，没有“只在目标仍不存在时替换”的原子条件。
    """
    protocol = "cooperative_publisher"
    if relative_path.startswith(("/", "\\")) or ".." in Path(relative_path).parts or ":" in relative_path:
        return {"published": False, "status": "rejected", "reason": "path", "protocol": protocol}
    target = workspace / relative_path
    if not _inside(workspace, target) or not _inside(workspace, staging_path):
        return {"published": False, "status": "rejected", "reason": "path", "protocol": protocol}

    def work() -> dict[str, Any]:
        existing = ledger.effect_row(idempotency_key)
        if existing is None or existing.get("run_id") != run_id:
            return {"published": False, "status": "missing", "protocol": protocol}
        digest = existing.get("result")
        receipt = f"file:{relative_path}:{digest}"
        if existing["phase"] == "completed":
            presented = _sha256(staging_path) if staging_path.is_file() else None
            on_disk = _sha256(target) if target.is_file() else None
            if (presented is not None and presented != digest) or (on_disk is not None and on_disk != digest):
                return {"published": False, "status": "conflict", "receipt": existing["receipt"], "protocol": protocol}
            return {"published": False, "status": "replayed", "receipt": existing["receipt"], "protocol": protocol}
        if target.is_file():
            if _sha256(target) == digest:
                ledger.stage_effect_open(idempotency_key, phase="completed", effect_state="RESULT_PERSISTED", receipt=receipt)
                ledger._event_open(
                    run_id,
                    "file_published",
                    {"path": relative_path, "receipt": receipt, "sha256": digest, "receipt_verified": True, "idempotency_key": idempotency_key, "recovered": "receipt"},
                )
                return {"published": True, "status": "completed", "receipt": receipt, "sha256": digest, "replaced": False, "protocol": protocol}
            return {"published": False, "status": "conflict", "protocol": protocol}
        if not staging_path.is_file() or _sha256(staging_path) != digest:
            ledger.stage_effect_open(idempotency_key, phase="unknown", effect_state="OUTCOME_UNKNOWN")
            ledger._event_open(run_id, "outcome_unknown", {"idempotency_key": idempotency_key, "path": relative_path, "reason": "staging"})
            return {"published": False, "status": "unknown", "protocol": protocol}
        external_file_overwritten = False
        if before_replace is not None:
            before_replace()
            external_file_overwritten = target.is_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_path, target)
        if _sha256(target) != digest:
            ledger.stage_effect_open(idempotency_key, phase="unknown", effect_state="OUTCOME_UNKNOWN")
            ledger._event_open(run_id, "outcome_unknown", {"idempotency_key": idempotency_key, "path": relative_path, "reason": "replaced_digest"})
            return {
                "published": False,
                "status": "unknown",
                "external_file_overwritten": external_file_overwritten,
                "protocol": protocol,
            }
        ledger.stage_effect_open(idempotency_key, phase="completed", effect_state="RESULT_PERSISTED", receipt=receipt)
        ledger._event_open(
            run_id,
            "file_published",
            {
                "path": relative_path,
                "receipt": receipt,
                "sha256": digest,
                "receipt_verified": True,
                "idempotency_key": idempotency_key,
                "recovered": "publish",
                "external_file_overwritten": external_file_overwritten,
            },
        )
        return {
            "published": True,
            "status": "completed",
            "receipt": receipt,
            "sha256": digest,
            "replaced": True,
            "replace_is_unconditional": True,
            "external_file_overwritten": external_file_overwritten,
            "protocol": protocol,
        }

    return ledger.with_immediate(work)


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
