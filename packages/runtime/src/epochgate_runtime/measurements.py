"""确定性论文测量。不访问网络，不读取密钥。"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from epochgate_runtime.api import Runtime
from epochgate_runtime.files import publish_file
from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.report import run_chaos
from epochgate_runtime.sandbox import docker_available
from epochgate_runtime.sink import EffectSink


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def commit_matrix_rows(directory: Path) -> list[dict[str, Any]]:
    """重复提交、先撤销再提交、先提交再撤销，以及一次并发样本。"""
    directory.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    ledger = RunLedger(directory / "duplicate.sqlite")
    sink = EffectSink(directory / "duplicate-sink.sqlite")
    ledger.create_run("duplicate")
    ledger.advance("duplicate", "running")
    state = ledger.get_run("duplicate")
    first = ledger.commit_authorized(
        "duplicate",
        idempotency_key="k",
        fencing_token=state.fencing_token,
        state_version=state.state_version,
        sink=sink,
        payload="x",
    )
    second = ledger.commit_authorized(
        "duplicate",
        idempotency_key="k",
        fencing_token=state.fencing_token,
        state_version=state.state_version,
        sink=sink,
        payload="x",
    )
    rows.append(
        {
            "fault": "duplicate_commit",
            "first_status": first["status"],
            "second_status": second["status"],
            "sink_accepts": sink.accepted_count("k"),
            "gpu_reclaimed": False,
            "resource_observation": "not_observable",
        }
    )
    ledger.close()
    sink.close()

    ledger = RunLedger(directory / "revoke-then-commit.sqlite")
    sink = EffectSink(directory / "revoke-then-commit-sink.sqlite")
    ledger.create_run("revoke-first")
    ledger.advance("revoke-first", "running")
    state = ledger.get_run("revoke-first")
    ledger.revoke_with_sink("revoke-first", sink)
    rejected = ledger.commit_authorized(
        "revoke-first",
        idempotency_key="k",
        fencing_token=state.fencing_token,
        state_version=state.state_version,
        sink=sink,
        payload="x",
    )
    rows.append(
        {
            "fault": "revoke_then_commit",
            "commit_status": rejected["status"],
            "sink_accepts": sink.accepted_count("k"),
            "eligible_after_revoke": ledger.get_run("revoke-first").eligible,
            "gpu_reclaimed": False,
            "resource_observation": "not_observable",
        }
    )
    ledger.close()
    sink.close()

    ledger = RunLedger(directory / "commit-then-revoke.sqlite")
    sink = EffectSink(directory / "commit-then-revoke-sink.sqlite")
    ledger.create_run("commit-first")
    ledger.advance("commit-first", "running")
    state = ledger.get_run("commit-first")
    committed = ledger.commit_authorized(
        "commit-first",
        idempotency_key="k",
        fencing_token=state.fencing_token,
        state_version=state.state_version,
        sink=sink,
        payload="x",
    )
    ledger.revoke_with_sink("commit-first", sink)
    after = ledger.get_run("commit-first")
    retry = ledger.commit_authorized(
        "commit-first",
        idempotency_key="k2",
        fencing_token=state.fencing_token,
        state_version=state.state_version,
        sink=sink,
        payload="y",
    )
    effect = ledger.effect_row("k")
    rows.append(
        {
            "fault": "commit_then_revoke",
            "commit_status": committed["status"],
            "retry_with_old_token_status": retry["status"],
            "sink_accepts": sink.accepted_count("k"),
            "retry_sink_accepts": sink.accepted_count("k2"),
            "receipt_still_present": bool(effect and effect.get("receipt")),
            "eligible_after_revoke": after.eligible,
            "gpu_reclaimed": after.cancel.gpu_reclaimed,
            "resource_observation": "not_observable",
        }
    )
    ledger.close()
    sink.close()

    ledger = RunLedger(directory / "concurrent.sqlite")
    sink = EffectSink(directory / "concurrent-sink.sqlite")
    ledger.create_run("race")
    ledger.advance("race", "running")
    state = ledger.get_run("race")
    outcome: dict[str, str] = {}

    def _commit() -> None:
        result = ledger.commit_authorized(
            "race",
            idempotency_key="k",
            fencing_token=state.fencing_token,
            state_version=state.state_version,
            sink=sink,
            payload="x",
        )
        outcome["status"] = result["status"]

    left = threading.Thread(target=_commit)
    right = threading.Thread(target=lambda: ledger.revoke_with_sink("race", sink))
    left.start()
    right.start()
    left.join()
    right.join()
    accepts = sink.accepted_count("k")
    rows.append(
        {
            "fault": "revoke_vs_commit_concurrent",
            "n": 1,
            "observed_status": outcome.get("status"),
            "sink_accepts": accepts,
            "status_matches_accepts": (outcome.get("status") == "completed") is (accepts == 1),
            "gpu_reclaimed": False,
            "resource_observation": "not_observable",
            "note": "一次并发样本，不是全部交错的分布。",
        }
    )
    ledger.close()
    sink.close()
    return rows


def run_fault_matrix(directory: Path) -> dict[str, Any]:
    """崩溃点、取消、重复提交和撤销顺序。GPU 回收保持 not_observable。"""
    process_dir = directory / "process"
    process_dir.mkdir(parents=True, exist_ok=True)
    chaos = run_chaos(process_dir)
    rows: list[dict[str, Any]] = []
    for row in chaos["rows"]:
        copied = dict(row)
        copied["gpu_reclaimed"] = bool(copied.get("gpu_reclaimed", False))
        copied["resource_observation"] = "not_observable"
        rows.append(copied)
    commit_rows = commit_matrix_rows(directory / "commit")
    rows.extend(commit_rows)
    duplicate = next(item for item in commit_rows if item["fault"] == "duplicate_commit")
    revoke_first = next(item for item in commit_rows if item["fault"] == "revoke_then_commit")
    commit_first = next(item for item in commit_rows if item["fault"] == "commit_then_revoke")
    concurrent = next(item for item in commit_rows if item["fault"] == "revoke_vs_commit_concurrent")
    crashes_ok = chaos["passed"] is True
    commits_ok = (
        duplicate["second_status"] == "replayed"
        and duplicate["sink_accepts"] == 1
        and revoke_first["commit_status"] == "rejected"
        and revoke_first["sink_accepts"] == 0
        and commit_first["commit_status"] == "completed"
        and commit_first["sink_accepts"] == 1
        and commit_first["retry_with_old_token_status"] == "rejected"
        and commit_first["retry_sink_accepts"] == 0
        and commit_first["receipt_still_present"] is True
        and commit_first["gpu_reclaimed"] is False
    )
    return {
        "passed": crashes_ok and commits_ok and concurrent["status_matches_accepts"] is True,
        "gpu_reclaimed": False,
        "resource_observation": "not_observable",
        "distributed_exactly_once": False,
        "scope": "one local sqlite ledger and one local sink; not arbitrary HTTP",
        "rows": rows,
    }


def _mutate_publish(root: Path, kind: str) -> dict[str, Any]:
    import epochgate_runtime.files as files_mod

    original = b"hashed-bytes-v1"
    workspace = root / kind
    workspace.mkdir(parents=True, exist_ok=True)
    staging = workspace / ".eg-staging" / "note.txt"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(original)
    ledger = RunLedger(root / f"{kind}.sqlite")
    ledger.create_run(kind)
    ledger.advance(kind, "running")
    state = ledger.get_run(kind)
    mutated = b"" if kind == "truncate" else b"replaced-after-read"
    state_box = {"done": False}
    real = files_mod._sha256

    def wrapped(path: Path) -> str:
        digest = real(path)
        if not state_box["done"] and path.resolve() == staging.resolve():
            state_box["done"] = True
            path.write_bytes(mutated)
        return digest

    files_mod._sha256 = wrapped
    try:
        result = publish_file(
            ledger,
            workspace=workspace,
            run_id=kind,
            idempotency_key=kind,
            fencing_token=state.fencing_token,
            relative_path="note.txt",
            staging_path=staging,
        )
    finally:
        files_mod._sha256 = real
    target = workspace / "note.txt"
    target_bytes = target.read_bytes() if target.is_file() else None
    effect = ledger.effect_row(kind)
    ledger.close()
    published_digest = _sha256(target_bytes) if target_bytes is not None else None
    return {
        "publisher": "publish_file",
        "mutation": kind,
        "mutation_after": "sha256_read",
        "hashed_bytes": original.decode("ascii"),
        "hashed_sha256": _sha256(original),
        "mutated_sha256": _sha256(mutated),
        "status": result.get("status"),
        "published": result.get("published"),
        "target_present": target.is_file(),
        "target_sha256": published_digest,
        "published_bytes_match_hashed_bytes": target_bytes == original,
        "effect_state": None if effect is None else effect.get("effect_state"),
        "receipt": None if effect is None else effect.get("receipt"),
    }


def measure_host_read_race(root: Path) -> dict[str, Any]:
    """宿主 publish_file 在读完摘要后改文件。它按路径改名，不把内存里的字节写回去。"""
    cases = [_mutate_publish(root, "replace"), _mutate_publish(root, "truncate")]
    return {
        "execution": "host_publish_file",
        "kernel_toctou": False,
        "claim": "host publish_file hashes the path, then os.replace. A change after the hash is not published as a verified receipt.",
        "cases": cases,
    }


def _container_ready() -> bool:
    return docker_available()


def measure_container_read_race() -> dict[str, Any]:
    """容器发布端把读入内存的字节写回。读完之后再替换或截断暂存文件。"""
    if not _container_ready():
        return {"execution": "container", "docker": False, "cases": [], "error": "WSL Docker engine is not available"}
    from epochgate_runtime.sandbox import (
        exec_in,
        exec_root,
        lock_workspace,
        publish_container,
        sha256_file,
        start_container,
        stop_container,
    )

    name = "eg-rt-read-race"
    original = b"hashed-bytes-v1"
    ledger = RunLedger()
    ledger.create_run("read-race")
    ledger.advance("read-race", "running")
    token = ledger.get_run("read-race").fencing_token
    cases: list[dict[str, Any]] = []
    try:
        start_container(name)
        lock_workspace(name)
        prepared = exec_root(name, ["mkdir", "-p", "/scratch/.eg-staging"])
        if prepared.returncode != 0:
            return {
                "execution": "container",
                "docker": True,
                "cases": [],
                "error": prepared.stderr.decode("utf-8", "replace")[-300:],
            }
        for kind, mutated in (("replace", b"replaced-after-read"), ("truncate", b"")):
            staging = f"/scratch/.eg-staging/{kind}"
            relative = f"{kind}.txt"
            written = exec_in(
                name,
                ["python", "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())", staging],
                original,
            )
            if written.returncode != 0:
                cases.append({"mutation": kind, "error": written.stderr.decode("utf-8", "replace")[-300:]})
                continue

            def _mutate(path: str = staging, payload: bytes = mutated) -> None:
                changed = exec_in(
                    name,
                    ["python", "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())", path],
                    payload,
                )
                if changed.returncode != 0:
                    raise RuntimeError(changed.stderr.decode("utf-8", "replace")[-300:])

            result = publish_container(
                ledger,
                container=name,
                run_id="read-race",
                idempotency_key=kind,
                fencing_token=token,
                relative_path=relative,
                staging_path=staging,
                after_read=_mutate,
            )
            target = "/workspace/" + relative
            shown = exec_root(name, ["python", "-c", "import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())", target])
            published = shown.stdout if shown.returncode == 0 else b""
            staged_now = exec_in(
                name,
                ["python", "-c", "import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())", staging],
            )
            cases.append(
                {
                    "publisher": "publish_container",
                    "mutation": kind,
                    "mutation_after": "read_staging_bytes",
                    "status": result.get("status"),
                    "published": result.get("published"),
                    "hashed_sha256": _sha256(original),
                    "published_sha256": _sha256(published) if shown.returncode == 0 else None,
                    "workspace_sha256": sha256_file(name, target),
                    "published_bytes_match_hashed_bytes": published == original,
                    "staging_after_sha256": _sha256(staged_now.stdout) if staged_now.returncode == 0 else None,
                    "receipt_verified": result.get("published") is True and result.get("sha256") == _sha256(original),
                }
            )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"execution": "container", "docker": True, "cases": cases, "error": str(exc)[-400:]}
    finally:
        ledger.close()
        try:
            stop_container(name)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            pass
    return {
        "execution": "container",
        "docker": True,
        "kernel_toctou": False,
        "image": "python:3.10-slim",
        "cases": cases,
    }


def _publish_case(root: Path, run_id: str, *, supersede: bool) -> dict[str, Any]:
    runtime = Runtime.open(root / f"{run_id}.sqlite", data_dir=root / run_id)
    try:
        inventory = root / f"{run_id}-inventory.txt"
        inventory.write_text("3\n", encoding="utf-8")
        first = runtime.reserve(run_id, "workspace/order.txt")
        inventory.write_text("0\n", encoding="utf-8")
        runtime.ledger.append_event(
            run_id,
            "world_changed",
            {"inventory_at_grant": 3, "inventory_before_commit": 0, "supersede": supersede},
        )
        if not supersede:
            published = runtime.publish(first, b"reserve:1\n")
            target = runtime.workspace / run_id / "order.txt"
            body = target.read_bytes() if target.is_file() else b""
            return {
                "run_id": run_id,
                "supersede": False,
                "inventory_at_grant": 3,
                "inventory_before_commit": inventory.read_text(encoding="utf-8").strip(),
                "publish_status": published.get("status"),
                "published": published.get("published"),
                "bytes": body.decode("ascii", "replace"),
                "note": "Changing the inventory file does not by itself expire the fencing token.",
            }
        second = runtime.reserve(run_id, "workspace/order.txt")
        stale = runtime.publish(first, b"reserve:1\n")
        fresh = runtime.publish(second, b"inventory:0\n")
        target = runtime.workspace / run_id / "order.txt"
        body = target.read_bytes() if target.is_file() else b""
        replay = runtime.publish(second, b"should-not-replace")
        replay_body = target.read_bytes() if target.is_file() else b""
        return {
            "run_id": run_id,
            "supersede": True,
            "inventory_at_grant": 3,
            "inventory_before_commit": 0,
            "expired_token": first.fencing_token,
            "valid_token": second.fencing_token,
            "expired_status": stale.get("status"),
            "expired_published": stale.get("published"),
            "valid_status": fresh.get("status"),
            "valid_published": fresh.get("published"),
            "valid_sha256": fresh.get("sha256"),
            "bytes": body.decode("ascii", "replace"),
            "replay_status": replay.get("status"),
            "bytes_unchanged_after_replay": replay_body == body,
        }
    finally:
        runtime.close()


def _inventory_commit(root: Path) -> dict[str, Any]:
    ledger = RunLedger(root / "inventory.sqlite")
    sink = EffectSink(root / "inventory-sink.sqlite")
    try:
        ledger.create_run("inventory")
        ledger.advance("inventory", "running")
        granted = ledger.get_run("inventory")
        ledger.append_event(
            "inventory",
            "world_changed",
            {"inventory_at_grant": 3, "inventory_before_commit": 0},
        )
        current = ledger.supersede("inventory")
        stale = ledger.commit_authorized(
            "inventory",
            idempotency_key="sale",
            fencing_token=granted.fencing_token,
            state_version=granted.state_version,
            sink=sink,
            payload="decrement",
        )
        fresh = ledger.commit_authorized(
            "inventory",
            idempotency_key="hold",
            fencing_token=current.fencing_token,
            state_version=current.state_version,
            sink=sink,
            payload="hold",
        )
        ledger.create_run("inventory-after")
        ledger.advance("inventory-after", "running")
        aligned = ledger.get_run("inventory-after")
        aligned_commit = ledger.commit_authorized(
            "inventory-after",
            idempotency_key="hold-after",
            fencing_token=aligned.fencing_token,
            state_version=aligned.state_version,
            sink=sink,
            payload="hold",
        )
        duplicate = ledger.commit_authorized(
            "inventory-after",
            idempotency_key="hold-after",
            fencing_token=aligned.fencing_token,
            state_version=aligned.state_version,
            sink=sink,
            payload="hold",
        )
        return {
            "expired_token": granted.fencing_token,
            "expired_version": granted.state_version,
            "successor_token": current.fencing_token,
            "successor_version": current.state_version,
            "stale_status": stale["status"],
            "stale_sink_accepts": sink.accepted_count("sale"),
            "successor_commit_status": fresh["status"],
            "successor_sink_accepts": sink.accepted_count("hold"),
            "successor_note": "supersede increments the ledger state_version and does not bump EffectSink. commit_authorized then rejects the successor token because the sink version is still 1.",
            "aligned_token": aligned.fencing_token,
            "aligned_version": aligned.state_version,
            "aligned_status": aligned_commit["status"],
            "aligned_sink_accepts": sink.accepted_count("hold-after"),
            "duplicate_status": duplicate["status"],
            "gpu_reclaimed": False,
        }
    finally:
        ledger.close()
        sink.close()


def measure_stale_world(root: Path) -> dict[str, Any]:
    """资格发出后库存从 3 变成 0。过期 token 不能发布，新 token 可以。"""
    unchanged = _publish_case(root, "file-no-supersede", supersede=False)
    advanced = _publish_case(root, "file-supersede", supersede=True)
    inventory = _inventory_commit(root)
    return {
        "world": "inventory 3 then 0 before commit",
        "mechanism": "RunLedger.supersede raises the fencing token after the controller records the change. The ledger does not watch the inventory file by itself.",
        "without_epoch_advance": unchanged,
        "with_epoch_advance": advanced,
        "inventory_commit": inventory,
        "gpu_reclaimed": False,
        "resource_observation": "not_observable",
    }


def apply_host_arm(root: Path, arm: str, content: bytes) -> dict[str, Any]:
    """宿主上的 B0/B1/B2。在线容器对照另计，不把这里写成容器结果。"""
    arm_dir = root / arm
    arm_dir.mkdir(parents=True, exist_ok=True)
    target = arm_dir / "bug.py"
    if arm == "B0":
        target.write_bytes(content)
        return {
            "arm": "B0",
            "ledger": False,
            "receipt": None,
            "score_passed": _score_bug(arm_dir),
        }
    ledger = RunLedger(root / f"{arm}.sqlite")
    try:
        ledger.create_run(arm)
        ledger.advance(arm, "running")
        state = ledger.get_run(arm)
        reserved = ledger.record_intent(
            arm,
            f"{arm}-key",
            state.fencing_token,
            state.state_version,
            effect_id=f"{arm}-effect",
            arguments_hash="bug.py",
            tool_name="write_file",
        )
        if not reserved.get("allowed"):
            return {"arm": arm, "ledger": True, "allowed": False, "receipt": None, "score_passed": False}
        if arm == "B1":
            target.write_bytes(content)
            effect = ledger.effect_row(f"{arm}-key")
            return {
                "arm": "B1",
                "ledger": True,
                "allowed": True,
                "fencing_token": state.fencing_token,
                "effect_state": None if effect is None else effect.get("effect_state"),
                "receipt": None if effect is None else effect.get("receipt"),
                "score_passed": _score_bug(arm_dir),
            }
        staging = arm_dir / ".eg-staging" / "bug.py"
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_bytes(content)
        published = publish_file(
            ledger,
            workspace=arm_dir,
            run_id=arm,
            idempotency_key=f"{arm}-key",
            fencing_token=state.fencing_token,
            relative_path="bug.py",
            staging_path=staging,
        )
        effect = ledger.effect_row(f"{arm}-key")
        return {
            "arm": "B2",
            "ledger": True,
            "published": published.get("published"),
            "status": published.get("status"),
            "sha256": published.get("sha256"),
            "receipt": None if effect is None else effect.get("receipt"),
            "score_passed": _score_bug(arm_dir),
        }
    finally:
        ledger.close()


def _score_bug(directory: Path) -> bool:
    completed = subprocess.run(
        [sys.executable, "-c", "import bug; raise SystemExit(0 if bug.value() == 1 else 1)"],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0
