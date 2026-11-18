"""E10：发布完整性。H0 只复现旧路径。H1 是封存字节。H2 是容器发布。

同一组故障、同一种目标前提。掉电不测。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from epochgate_runtime.files import publish_file, publish_frozen_file, recover_frozen_publish
from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.sandbox import (
    docker_available,
    exec_in,
    publish_container,
    sha256_file,
    start_container,
    stop_container,
)

PAYLOAD = b"order 3\n"
MUTATED = b"replaced-after-read\n"
FAULTS = (
    "staging_replace",
    "staging_truncate",
    "digest_conflict",
    "revoke_after_claim",
    "receipt_loss",
    "two_recoverers",
    "external_target",
)


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _open(root: Path, attempt: str) -> tuple[RunLedger, int]:
    root.mkdir(parents=True, exist_ok=True)
    ledger = RunLedger(root / "ledger.sqlite")
    ledger.create_run(attempt)
    ledger.advance(attempt, "running")
    return ledger, ledger.get_run(attempt).fencing_token


def _stage(root: Path) -> Path:
    path = root / ".eg-staging" / "order.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PAYLOAD)
    return path


def _score(root: Path, claim_digest: str, published: dict, *, prior_existed: bool) -> dict:
    target = root / "order.txt"
    physical = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
    return {
        "status": published.get("status"),
        "physical_payload_matches_claim": physical == claim_digest,
        "receipt_matches_physical_effect": published.get("receipt") == f"file:order.txt:{claim_digest}" and physical == claim_digest,
        "unintended_overwrite": bool(published.get("unintended_overwrite") or prior_existed),
        "unknown_outcome": published.get("status") == "unknown",
        "recoverable_claim": published.get("status") in {"completed", "crashed_before_replace", "crashed_before_receipt"},
        "duplicate_effect": False,
        "prior_version_checked": False,
        "durability": published.get("durability") or {
            "atomic_replace": None,
            "process_crash_recoverable": None,
            "power_loss_persistent": None,
        },
    }


def _host_fault(root: Path, publisher: str, fault: str, attempt: str) -> dict:
    ledger, token = _open(root, attempt)
    staging = _stage(root)
    claim_digest = _digest(PAYLOAD)
    prior = False
    notes = []

    def mutate(payload: bytes) -> None:
        staging.write_bytes(payload)

    def plant_target() -> None:
        nonlocal prior
        (root / "order.txt").write_bytes(b"external\n")
        prior = True

    if publisher == "H0":
        hook_payload = MUTATED if fault == "staging_replace" else b""
        after = None
        if fault in {"staging_replace", "staging_truncate"}:
            after = lambda: mutate(hook_payload)
        elif fault == "external_target":
            after = plant_target
        if fault == "digest_conflict":
            first = publish_file(ledger, workspace=root, run_id=attempt, idempotency_key=attempt, fencing_token=token, relative_path="order.txt", staging_path=staging)
            staging.write_bytes(MUTATED)
            second = publish_file(ledger, workspace=root, run_id=attempt, idempotency_key=attempt, fencing_token=token, relative_path="order.txt", staging_path=staging)
            scored = _score(root, claim_digest, first, prior_existed=False)
            scored["second_status"] = second.get("status")
            scored["digest_compared"] = False
            scored["conflict_kept_first"] = (root / "order.txt").read_bytes() == PAYLOAD
            scored["notes"] = ["H0 在第二次发布时看到已完成的键就返回 replayed，不再比较新的摘要。这是历史路径。"]
            ledger.close()
            return {"publisher": publisher, "fault": fault, "environment": "host", **scored}
        if fault in {"revoke_after_claim", "receipt_loss", "two_recoverers"}:
            ledger.close()
            return {
                "publisher": publisher,
                "fault": fault,
                "environment": "host",
                "status": "不适用",
                "process_crash_recoverable": False,
                "power_loss_persistent": None,
                "notes": ["H0 没有封存文件。崩溃之后不能从私有副本恢复，这项不记成发布成功。"],
            }
        published = publish_file(
            ledger, workspace=root, run_id=attempt, idempotency_key=attempt,
            fencing_token=token, relative_path="order.txt", staging_path=staging, after_hash=after,
        )
        scored = _score(root, claim_digest, published, prior_existed=prior)
        if fault in {"staging_replace", "staging_truncate"}:
            scored["physical_payload_matches_claim"] = hashlib.sha256((root / "order.txt").read_bytes()).hexdigest() == claim_digest
            scored["unknown_outcome"] = published.get("status") == "unknown"
        ledger.close()
        return {"publisher": publisher, "fault": fault, "environment": "host", **scored}

    crash = {"revoke_after_claim": "after_claim", "receipt_loss": "after_replace", "two_recoverers": "after_claim"}.get(fault)
    after = None
    if fault == "staging_replace":
        after = lambda: mutate(MUTATED)
    elif fault == "staging_truncate":
        after = lambda: mutate(b"")
    elif fault == "external_target":
        after = plant_target
    if fault == "digest_conflict":
        first = publish_frozen_file(ledger, workspace=root, run_id=attempt, idempotency_key=attempt, fencing_token=token, relative_path="order.txt", staging_path=staging)
        staging.write_bytes(MUTATED)
        second = publish_frozen_file(ledger, workspace=root, run_id=attempt, idempotency_key=attempt, fencing_token=token, relative_path="order.txt", staging_path=staging)
        scored = _score(root, claim_digest, first, prior_existed=False)
        scored["second_status"] = second.get("status")
        scored["conflict_kept_first"] = second.get("status") in {"conflict", "replayed"} and (root / "order.txt").read_bytes() == PAYLOAD
        ledger.close()
        return {"publisher": publisher, "fault": fault, "environment": "host", **scored}
    published = publish_frozen_file(
        ledger, workspace=root, run_id=attempt, idempotency_key=attempt,
        fencing_token=token, relative_path="order.txt", staging_path=staging,
        after_seal=after, crash_point=crash,
    )
    if fault == "revoke_after_claim":
        ledger.revoke(attempt)
        recovered = recover_frozen_publish(ledger, workspace=root, run_id=attempt, idempotency_key=attempt, relative_path="order.txt")
        scored = _score(root, claim_digest, recovered, prior_existed=False)
        scored["recoverable_claim"] = recovered.get("status") == "completed"
        scored["status"] = recovered.get("status")
    elif fault == "receipt_loss":
        reopened = RunLedger(root / "ledger.sqlite")
        recovered = recover_frozen_publish(reopened, workspace=root, run_id=attempt, idempotency_key=attempt, relative_path="order.txt")
        scored = _score(root, claim_digest, recovered, prior_existed=False)
        scored["recoverable_claim"] = recovered.get("status") == "completed"
        scored["receipt_matches_physical_effect"] = recovered.get("receipt") == f"file:order.txt:{claim_digest}"
        reopened.close()
    elif fault == "two_recoverers":
        outcomes: list[dict] = []

        def recover() -> None:
            other = RunLedger(root / "ledger.sqlite")
            outcomes.append(recover_frozen_publish(other, workspace=root, run_id=attempt, idempotency_key=attempt, relative_path="order.txt"))
            other.close()

        threads = [threading.Thread(target=recover) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        statuses = sorted(item.get("status") for item in outcomes)
        scored = _score(root, claim_digest, {"status": "completed", "receipt": f"file:order.txt:{claim_digest}", "durability": published.get("durability")}, prior_existed=False)
        scored["recover_statuses"] = statuses
        scored["duplicate_effect"] = statuses.count("completed") > 1
        scored["recoverable_claim"] = "completed" in statuses
    else:
        scored = _score(root, claim_digest, published, prior_existed=prior)
    durability = dict(scored["durability"])
    durability["power_loss_persistent"] = None
    scored["durability"] = durability
    scored["power_loss"] = "未测"
    ledger.close()
    return {"publisher": publisher, "fault": fault, "environment": "host", **scored}


def _container_faults(root: Path, attempt: str) -> list[dict]:
    if not docker_available():
        return [{"publisher": "H2", "fault": fault, "environment": "container", "status": "未测", "reason": "docker_unavailable"} for fault in FAULTS]
    name = f"eg-e10-{attempt}"
    rows = []
    start_container(name)
    try:
        from epochgate_runtime.sandbox import lock_workspace

        lock_workspace(name)
        ledger, token = _open(root, attempt + "-h2")
        staging = "/scratch/.eg-staging/order.txt"
        exec_in(name, ["python", "-c", "import pathlib; p=pathlib.Path('/scratch/.eg-staging/order.txt'); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'order 3\\n')"])
        claim = _digest(PAYLOAD)

        def plant(payload: bytes):
            def hook() -> None:
                exec_in(name, ["python", "-c", f"import pathlib; pathlib.Path('{staging}').write_bytes({payload!r})"])
            return hook

        published = publish_container(ledger, container=name, run_id=attempt + "-h2", idempotency_key=attempt + "-replace", fencing_token=token, relative_path="order.txt", staging_path=staging, after_read=plant(MUTATED))
        physical = sha256_file(name, "/workspace/order.txt")
        rows.append({
            "publisher": "H2", "fault": "staging_replace", "environment": "container",
            "status": published.get("status"),
            "reason": published.get("reason"),
            "physical_payload_matches_claim": physical == claim,
            "receipt_matches_physical_effect": published.get("status") == "completed" and physical == claim,
            "unintended_overwrite": False,
            "unknown_outcome": published.get("status") == "unknown",
            "environment_difference": "发布进程在容器内，字节来自一次读取后的内存，不是宿主私有文件",
        })
        exec_in(name, ["python", "-c", "import pathlib; pathlib.Path('/scratch/.eg-staging/order.txt').write_bytes(b'order 3\\n')"])
        truncated = publish_container(ledger, container=name, run_id=attempt + "-h2", idempotency_key=attempt + "-truncate", fencing_token=token, relative_path="trunc.txt", staging_path=staging, after_read=plant(b""))
        trunc_hash = sha256_file(name, "/workspace/trunc.txt")
        rows.append({
            "publisher": "H2", "fault": "staging_truncate", "environment": "container",
            "status": truncated.get("status"),
            "physical_payload_matches_claim": trunc_hash == claim,
            "unknown_outcome": truncated.get("status") == "unknown",
        })
        conflict = publish_container(ledger, container=name, run_id=attempt + "-h2", idempotency_key=attempt + "-replace", fencing_token=token, relative_path="order.txt", staging_path=staging)
        rows.append({
            "publisher": "H2", "fault": "digest_conflict", "environment": "container",
            "status": conflict.get("status"),
            "conflict_kept_first": conflict.get("status") in {"replayed", "conflict"},
            "physical_payload_matches_claim": sha256_file(name, "/workspace/order.txt") == claim,
        })
        external = exec_in(name, ["python", "-c", "import pathlib; pathlib.Path('/workspace/planted.txt').write_bytes(b'external')"])
        rows.append({
            "publisher": "H2", "fault": "external_target", "environment": "container",
            "status": "环境差异",
            "tool_could_create_target": external.returncode == 0,
            "note": "容器工作区对工具用户不可写时，这一格不是快照方式的胜利",
        })
        for fault in ("revoke_after_claim", "receipt_loss", "two_recoverers"):
            rows.append({
                "publisher": "H2", "fault": fault, "environment": "container",
                "status": "未测",
                "environment_difference": "容器发布函数没有与 H1 相同的崩溃钩子。不把这项缺失记成快照优势。",
            })
        ledger.close()
    finally:
        stop_container(name)
    return rows


def run(instance: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with TemporaryDirectory(prefix=f"e10-{instance}-") as temporary:
        parent = Path(temporary)
        for publisher in ("H0", "H1"):
            for fault in FAULTS:
                rows.append(_host_fault(parent / f"{publisher}-{fault}", publisher, fault, f"{instance}-{publisher}-{fault}"))
        rows.extend(_container_faults(parent / "h2", instance))
    h0_bad = [row for row in rows if row["publisher"] == "H0" and row["fault"] in {"staging_replace", "staging_truncate"}]
    h1_bad = [row for row in rows if row["publisher"] == "H1" and row["fault"] in {"staging_replace", "staging_truncate"}]
    summary = {
        "instance": instance,
        "role": "exploratory" if instance == "exploratory" else "confirmation",
        "protocol": "docs/开发文档/补实验协议.md",
        "rows": rows,
        "h3_h0_mismatches": sum(not row["physical_payload_matches_claim"] for row in h0_bad),
        "h3_h1_mismatches": sum(not row["physical_payload_matches_claim"] for row in h1_bad),
        "power_loss": "未测",
        "target_precondition": "故障开始时目标不存在；external_target 在提交前由外部写入者创建目标，且不核对预期旧版本",
    }
    (out / f"{instance}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", choices=("exploratory", "confirm"), required=True)
    parser.add_argument("--out", type=Path, default=Path("results/runtime/correctness/e10"))
    args = parser.parse_args()
    summary = run(args.instance, args.out)
    print(json.dumps({"instance": summary["instance"], "h0": summary["h3_h0_mismatches"], "h1": summary["h3_h1_mismatches"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
