"""E9：机制与通知的对照，外加只作用于 Full 的消融。

第一批是探索。确认批用新的目录和新的 attempt 编号，规则不变。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from epochgate_runtime.files import publish_frozen_file, recover_frozen_publish
from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.sink import EffectSink

OLD = b"order 3\n"
NEW = b"order 0\n"
MECHANISMS = ("B0", "B1", "B2_nonatomic", "B2_strong", "B3")
NOTICES = ("delivered", "missing", "delayed")


def _fresh(root: Path, attempt: str) -> tuple[RunLedger, EffectSink, dict]:
    ledger = RunLedger(root / "ledger.sqlite")
    sink = EffectSink(root / "sink.sqlite")
    ledger.create_run(attempt)
    ledger.advance(attempt, "running")
    state = ledger.get_run(attempt)
    (root / "stock.txt").write_bytes(b"3\n")
    return ledger, sink, {
        "attempt": attempt,
        "token": state.fencing_token,
        "version": state.state_version,
        "payload": OLD.decode(),
        "issued_before_world_change": True,
    }


def _authority(ledger: RunLedger, sink: EffectSink, attempt: str, study: str) -> None:
    if study == "supersede":
        ledger.supersede(attempt, sink)
    else:
        ledger.revoke(attempt)


def _write(root: Path, payload: bytes) -> None:
    (root / "order.txt").write_bytes(payload)


class Clock:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._n = 0

    def mark(self, name: str) -> None:
        self._n += 1
        self.events.append({"order": self._n, "name": name, "t": time.perf_counter()})


def _stale(ledger: RunLedger, attempt: str, frozen: dict) -> bool:
    state = ledger.get_run(attempt)
    return (not state.eligible) or state.fencing_token != frozen["token"] or state.state_version != frozen["version"]


def _attempt_old(root: Path, ledger: RunLedger, attempt: str, frozen: dict, mechanism: str, between) -> dict:
    key = f"{attempt}-old"
    target_decision = {"B0": "none", "B1": "admit", "B2_nonatomic": "check_then_write", "B2_strong": "immediate_check_and_write", "B3": "claim_publish"}[mechanism]
    stale_at_decision = False
    stale_at_write = False

    def admitted_then_window() -> bool:
        nonlocal stale_at_decision, stale_at_write
        state = ledger.get_run(attempt)
        allowed = bool(state.eligible)
        stale_at_decision = not allowed
        if between is not None:
            between()
        stale_at_write = _stale(ledger, attempt, frozen)
        if allowed:
            _write(root, OLD)
        return allowed

    def check_then_window() -> bool:
        nonlocal stale_at_decision, stale_at_write
        stale_at_decision = _stale(ledger, attempt, frozen)
        allowed = not stale_at_decision
        if between is not None:
            between()
        stale_at_write = _stale(ledger, attempt, frozen)
        if allowed:
            _write(root, OLD)
        return allowed

    def strong() -> bool:
        nonlocal stale_at_decision, stale_at_write
        outcome = {"accepted": False}

        def work() -> bool:
            row = ledger._conn.execute(
                "SELECT eligible, fencing_token, state_version FROM runs WHERE run_id = ?",
                (attempt,),
            ).fetchone()
            fresh = row is not None and bool(row[0]) and row[1] == frozen["token"] and row[2] == frozen["version"]
            outcome["accepted"] = fresh
            if fresh:
                _write(root, OLD)
            return fresh

        ledger.with_immediate(work)
        stale_at_decision = not outcome["accepted"]
        stale_at_write = stale_at_decision
        return outcome["accepted"]

    if mechanism == "B0":
        stale_at_write = _stale(ledger, attempt, frozen)
        _write(root, OLD)
        accepted = True
    elif mechanism == "B1":
        accepted = admitted_then_window()
    elif mechanism == "B2_nonatomic":
        accepted = check_then_window()
    elif mechanism == "B2_strong":
        accepted = strong()
    else:
        stale_at_decision = _stale(ledger, attempt, frozen)
        published = publish_frozen_file(
            ledger,
            workspace=root,
            run_id=attempt,
            idempotency_key=key,
            fencing_token=frozen["token"],
            relative_path="order.txt",
            staging_path=_stage(root, OLD),
        )
        accepted = published.get("status") == "completed"
        stale_at_write = stale_at_decision
    return {
        "accepted": accepted,
        "decision": target_decision,
        "key": key,
        "stale_at_decision": stale_at_decision,
        "stale_at_write": stale_at_write,
    }


def _stage(root: Path, payload: bytes) -> Path:
    path = root / "staging" / "order.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _successor(root: Path, ledger: RunLedger, attempt: str) -> bool:
    state = ledger.get_run(attempt)
    if not state.eligible:
        return False
    published = publish_frozen_file(
        ledger,
        workspace=root,
        run_id=attempt,
        idempotency_key=f"{attempt}-next",
        fencing_token=state.fencing_token,
        relative_path="order.txt",
        staging_path=_stage(root, NEW),
    )
    return published.get("status") == "completed" and (root / "order.txt").read_bytes() == NEW


def one_cell(parent: Path, mechanism: str, notice: str, study: str, attempt: str) -> dict:
    root = parent / f"{study}__{mechanism}__{notice}"
    root.mkdir(parents=True)
    ledger, sink, frozen = _fresh(root, attempt)
    clock = Clock()
    clock.mark("world_changed")
    (root / "stock.txt").write_bytes(b"0\n")
    split = mechanism in {"B1", "B2_nonatomic"} and notice == "delayed"

    def deliver() -> None:
        clock.mark("notification_delivered")
        _authority(ledger, sink, attempt, study)
        clock.mark("authority_updated")

    between = None
    if notice == "delivered":
        clock.mark("notification_emitted")
        deliver()
    elif notice == "delayed":
        clock.mark("notification_emitted")
        if split:
            between = deliver
    clock.mark("commit_attempted")
    old = _attempt_old(root, ledger, attempt, frozen, mechanism, between)
    if notice == "delayed" and not split:
        deliver()
    file_path = root / "order.txt"
    file_bytes = file_path.read_bytes() if file_path.is_file() else None
    state = ledger.get_run(attempt)
    successor = False
    if study == "supersede" and notice != "missing":
        successor = _successor(root, ledger, attempt)
        if (root / "order.txt").is_file():
            file_bytes = (root / "order.txt").read_bytes()
    cell = {
        "study": study,
        "mechanism": mechanism,
        "notice": notice,
        "attempt": attempt,
        "frozen": frozen,
        "timeline": clock.events,
        "decision": old["decision"],
        "old_accepted": old["accepted"],
        "stale_at_decision": old["stale_at_decision"],
        "stale_at_write": old["stale_at_write"],
        "stale_credential_accepted": bool(old["accepted"] and (old["stale_at_decision"] or old["stale_at_write"])),
        "world_stale_payload": file_bytes == OLD,
        "successor_completed": successor,
        "task_terminal_match": file_bytes == NEW,
        "file_present": file_bytes is not None,
        "final_token": state.fencing_token,
        "eligible": state.eligible,
        "split_decision": split,
        "layer": "mechanism_notice",
    }
    ledger.close()
    sink.close()
    return cell


def _ablation(parent: Path, name: str, attempt: str) -> dict:
    root = parent / name
    root.mkdir(parents=True)
    ledger, sink, frozen = _fresh(root, attempt)
    (root / "stock.txt").write_bytes(b"0\n")
    key = f"{attempt}-old"
    base = {
        "name": name,
        "attempt": attempt,
        "frozen": frozen,
        "layer": "full_ablation",
        "inconstructable": False,
    }
    if name == "full_minus_version":
        ledger.supersede(attempt, sink)
        published = publish_frozen_file(
            ledger, workspace=root, run_id=attempt, idempotency_key=key,
            fencing_token=frozen["token"], relative_path="order.txt",
            staging_path=_stage(root, OLD), check_version=False,
        )
        base.update({"old_accepted": published.get("status") == "completed", "stale_credential_accepted": published.get("status") == "completed"})
    elif name == "full_minus_receipt":
        published = publish_frozen_file(
            ledger, workspace=root, run_id=attempt, idempotency_key=key,
            fencing_token=frozen["token"], relative_path="order.txt",
            staging_path=_stage(root, OLD), write_receipt=False,
        )
        row = ledger.effect_row(key)
        base.update({
            "old_accepted": published.get("status") == "published_without_receipt",
            "receipt": None if row is None else row.get("receipt"),
            "phase": None if row is None else row.get("phase"),
            "file_present": (root / "order.txt").is_file(),
        })
    elif name == "full_minus_recovery":
        published = publish_frozen_file(
            ledger, workspace=root, run_id=attempt, idempotency_key=key,
            fencing_token=frozen["token"], relative_path="order.txt",
            staging_path=_stage(root, OLD), crash_point="after_claim",
        )
        base.update({
            "old_accepted": False,
            "status": published.get("status"),
            "file_present": (root / "order.txt").is_file(),
            "recovered": False,
            "phase": ledger.effect_row(key)["phase"],
        })
    elif name == "full_revoke_after_claim":
        published = publish_frozen_file(
            ledger, workspace=root, run_id=attempt, idempotency_key=key,
            fencing_token=frozen["token"], relative_path="order.txt",
            staging_path=_stage(root, OLD), crash_point="after_claim",
        )
        ledger.revoke(attempt)
        recovered = recover_frozen_publish(ledger, workspace=root, run_id=attempt, idempotency_key=key, relative_path="order.txt")
        base.update({
            "claim_status": published.get("status"),
            "recovered": recovered.get("status") == "completed",
            "file_bytes": (root / "order.txt").read_bytes().decode() if (root / "order.txt").is_file() else None,
            "note": "决定点之后的撤销不撤回已授权发布",
        })
    else:
        base["inconstructable"] = True
        base["reason"] = "未指定的消融，不改其他机制来填格"
    ledger.close()
    sink.close()
    return base


def _composition(cells: list[dict]) -> dict:
    matched = []
    for index, left in enumerate(cells):
        for right in cells[index + 1:]:
            if left["notice"] != right["notice"] or left["study"] != right["study"]:
                continue
            same_terminal = left["task_terminal_match"] == right["task_terminal_match"]
            different = left["stale_credential_accepted"] != right["stale_credential_accepted"] or left["successor_completed"] != right["successor_completed"]
            if same_terminal and different:
                matched.append({
                    "study": left["study"],
                    "notice": left["notice"],
                    "left": left["mechanism"],
                    "right": right["mechanism"],
                })
    return {"pairs": len(matched), "examples": matched[:8]}


def run(instance: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f"e9-{instance}-") as temporary:
        parent = Path(temporary)
        cells = []
        for study in ("supersede", "revoke"):
            for mechanism in MECHANISMS:
                for notice in NOTICES:
                    attempt = f"{instance}-{study}-{mechanism}-{notice}"
                    cells.append(one_cell(parent, mechanism, notice, study, attempt))
        ablations = [
            _ablation(parent, name, f"{instance}-{name}")
            for name in ("full_minus_version", "full_minus_receipt", "full_minus_recovery", "full_revoke_after_claim")
        ]
    delivered = [cell for cell in cells if cell["study"] == "supersede" and cell["notice"] == "delivered"]
    by_name = {cell["mechanism"]: cell for cell in delivered}
    h2 = int(by_name["B1"]["stale_credential_accepted"]) - int(by_name["B2_strong"]["stale_credential_accepted"])
    missing = [cell for cell in cells if cell["notice"] == "missing" and cell["mechanism"] in {"B2_strong", "B3"}]
    summary = {
        "instance": instance,
        "role": "exploratory" if instance == "exploratory" else "confirmation",
        "protocol": "docs/开发文档/补实验协议.md",
        "same_frozen_snapshot": True,
        "shared_row": False,
        "note": "每个格子从同一份冻结快照复制账本。提交时不重新领取凭证。同一行不能被多个机制各接受一次。",
        "cells": cells,
        "ablations": ablations,
        "h1_composition": _composition(cells),
        "h2_stale_difference_b1_minus_b2_strong": h2,
        "missing_notice_still_accepts_frozen": all(cell["old_accepted"] for cell in missing),
        "inconstructable": [item["name"] for item in ablations if item.get("inconstructable")],
    }
    (out / f"{instance}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", choices=("exploratory", "confirm"), required=True)
    parser.add_argument("--out", type=Path, default=Path("results/runtime/correctness/e9"))
    args = parser.parse_args()
    summary = run(args.instance, args.out)
    print(json.dumps({
        "instance": summary["instance"],
        "h1_pairs": summary["h1_composition"]["pairs"],
        "h2_difference": summary["h2_stale_difference_b1_minus_b2_strong"],
        "missing_accepts": summary["missing_notice_still_accepts_frozen"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
