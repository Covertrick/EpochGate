"""Run and audit the archived-workflow recovery obligation experiment."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from mechanism_validation.runtime.admission_path import run_admission_path
from mechanism_validation.runtime.claim_mechanism import ClaimMechanism
from mechanism_validation.runtime.faults import FaultState
from mechanism_validation.runtime.receiver_server import apply_control, apply_query, apply_submit, init_db
from mechanism_validation.runtime.sqlite_util import open_sqlite
from mechanism_validation.runtime.types import EffectRequest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.m4_tasks import CONFIRM, legal_payload  # noqa: E402

ARCHIVE = ROOT / "results/runtime/correctness/m4-confirm/ful-config-c4__B3__S3"
ARMS = ("claim", "admission_query", "admission_intent", "claim_checked")
CLAIM_ARMS = {"claim", "claim_checked"}
CHECKED_ARMS = {"admission_intent", "claim_checked"}
KEY = "ful-config-c4-publish"
AUTHORITY = {"token": 1, "epoch": 1, "eligible": True}
EXIT_AT_CHECKPOINT = 86


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def save(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")


def fixture():
    task = next(task for task in CONFIRM if task["task_id"] == "ful-config-c4")
    historical = read(ARCHIVE / "evidence.json")
    old = (ARCHIVE / "release.json").read_bytes()
    world_after = copy.deepcopy(task["initial"])
    world_after["config"]["version"] = task["event"]["set"]["config.version"]
    initial_legal = legal_payload(task, task["initial"]).encode()
    updated_legal = legal_payload(task, world_after).encode()
    if not (digest(old) == historical["old_digest"] == historical["physical_digest"]
            and digest(updated_legal) == historical["legal_digest"]):
        raise ValueError("Historical payload reconstruction hash mismatch")
    return {
        "task_id": task["task_id"], "archive_run_id": historical["run_id"],
        "worlds": {"1": task["initial"], "2": world_after},
        "legal": {"1": initial_legal.decode(), "2": updated_legal.decode()},
        "payloads": {"controlled": initial_legal.decode(), "archived": old.decode()},
        "archived_valid_before_update": old == initial_legal,
        "archived_valid_after_update": old == updated_legal,
        "archive_evidence_sha256": digest((ARCHIVE / "evidence.json").read_bytes()),
    }


def cases():
    conditions = [
        ("controlled", window, changed) for window in ("before_commit", "after_commit")
        for changed in (False, True)
    ] + [("archived", "before_commit", True)]
    for payload, window, changed in conditions:
        for arm in ARMS:
            yield {"id": f"{payload}-{window}-{int(changed)}-{arm}", "payload": payload,
                   "window": window, "changed": changed, "arm": arm}


class Receiver:
    def __init__(self, conn):
        self.conn = conn

    def query(self, effect_key):
        return apply_query(self.conn, {"effect_key": effect_key})

    def submit(self, payload, attempt):
        return apply_submit(self.conn, {
            "effect_key": KEY, "attempt_id": attempt,
            "payload_b64": base64.b64encode(payload).decode(),
            "authority_token": 1, "authority_epoch": 1,
        })


def prepare_and_exit(path):
    spec = read(path / "input.json")
    payload = (path / "proposal.bin").read_bytes()
    conn = open_sqlite(path / "target.sqlite")
    init_db(conn)
    arm = spec["arm"]
    req = EffectRequest("ful-config-c4", KEY, KEY, payload, digest(payload), 1, 1, 1)
    if arm in CLAIM_ARMS:
        claim = ClaimMechanism(path / "claim.sqlite")
        claim.record_authority(AUTHORITY)
        if not claim.commit_claim(req):
            raise RuntimeError("Initial claim rejected")
        if spec["window"] == "after_commit":
            claim.transition_to_sending(KEY)
    else:
        class InterruptingAdmissionClient:
            def submit(self, *, effect_key, payload, attempt_id, **_):
                if arm == "admission_intent":
                    with closing(open_sqlite(path / "intent.sqlite")) as intent:
                        intent.execute("CREATE TABLE intent (effect_key TEXT PRIMARY KEY, digest TEXT NOT NULL)")
                        intent.execute("INSERT INTO intent VALUES (?, ?)", (effect_key, digest(payload)))
                if spec["window"] == "after_commit":
                    result = Receiver(conn).submit(payload, attempt_id)
                    if result["outcome"] != "ACCEPTED":
                        raise RuntimeError("Original target commit failed")
                os._exit(EXIT_AT_CHECKPOINT)

        run_admission_path(
            InterruptingAdmissionClient(), req, AUTHORITY.copy(), FaultState(None), {},
            attempt_id="original", conflict_payload=b"",
        )
        raise RuntimeError("Admission did not reach target boundary")

    if spec["window"] == "after_commit":
        result = Receiver(conn).submit(payload, "original")
        if result["outcome"] != "ACCEPTED":
            raise RuntimeError("Original target commit failed")
    os._exit(EXIT_AT_CHECKPOINT)


def recover(path, spec, expected):
    with closing(open_sqlite(path / "target.sqlite")) as conn:
        apply_control(conn, {"op": "set_world_version", "version": 2 if spec["changed"] else 1})
        receiver = Receiver(conn)
        arm = spec["arm"]
        if arm in CLAIM_ARMS:
            with closing(ClaimMechanism(path / "claim.sqlite")) as claim:
                outcome = claim.recover(receiver, KEY)
                if outcome["outcome"] != "RESTORED_BYTES":
                    return outcome["outcome"]
                payload = outcome["payload"]
                if arm in CHECKED_ARMS and payload != expected:
                    return "WITHHELD_INVALID"
                claim.transition_to_sending(KEY)
                result = receiver.submit(payload, "recovery")
                if result["outcome"] in {"ACCEPTED", "REPLAY"}:
                    claim.acknowledge_claim(KEY, result["receipt"])
                return result["outcome"]

        found = receiver.query(KEY)
        if arm == "admission_query":
            return "OBSERVED_TARGET" if found["status"] == "PRESENT" else "ABSENT_NO_DURABLE_INTENT"
        with closing(sqlite3.connect((path / "intent.sqlite").as_uri() + "?mode=ro", uri=True)) as intent:
            intention = intent.execute("SELECT digest FROM intent WHERE effect_key = ?", (KEY,)).fetchone()[0]
        if found["status"] == "PRESENT":
            return "CONFIRMED_EXISTING" if found["payload_hash"] == intention else "CONFLICT"
        if found["status"] != "ABSENT":
            return "UNKNOWN"
        payload = (path / "proposal.bin").read_bytes()
        if digest(payload) != intention:
            return "CONFLICT"
        if payload != expected:
            return "WITHHELD_INVALID"
        return receiver.submit(payload, "recovery")["outcome"]


def run_case(path, spec, frozen):
    path.mkdir()
    save(path / "input.json", spec)
    (path / "proposal.bin").write_bytes(frozen["payloads"][spec["payload"]].encode())
    child = subprocess.run(
        [sys.executable, "-m", "recovery_obligation.run", "worker", str(path)],
        cwd=ROOT / "experiments", capture_output=True, timeout=20,
    )
    if child.returncode != EXIT_AT_CHECKPOINT or child.stdout or child.stderr:
        raise RuntimeError(f"Worker did not exit at checkpoint: {child.returncode}, {child.stderr!r}")
    expected = frozen["legal"]["2" if spec["changed"] else "1"].encode()
    save(path / "recovery.json", {"worker_exit": child.returncode, "outcome": recover(path, spec, expected)})


def score(path, spec, frozen):
    if read(path / "input.json") != spec:
        raise ValueError("Case inputs differ from the frozen matrix")
    report = read(path / "recovery.json")
    if report["worker_exit"] != EXIT_AT_CHECKPOINT:
        raise ValueError("Worker did not reach the exit boundary")
    payload = (path / "proposal.bin").read_bytes()
    if payload != frozen["payloads"][spec["payload"]].encode():
        raise ValueError("Proposal differs from the frozen input")
    with closing(sqlite3.connect((path / "target.sqlite").as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM target_effects").fetchall()
        attempts = conn.execute("SELECT * FROM target_attempts ORDER BY rowid").fetchall()
        final_version = conn.execute("SELECT version FROM receiver_world").fetchone()[0]
    expected_version = 2 if spec["changed"] else 1
    if len(rows) > 1 or len(attempts) != len(rows) or final_version != expected_version:
        raise ValueError("Target evidence does not match the one-effect contract")
    effect = rows[0] if rows else None
    intent_digest = None
    arm = spec["arm"]
    if arm in CLAIM_ARMS:
        with closing(sqlite3.connect((path / "claim.sqlite").as_uri() + "?mode=ro", uri=True)) as conn:
            intent_digest = conn.execute("SELECT payload_sha256 FROM claim_rows WHERE idempotency_key = ?", (KEY,)).fetchone()[0]
    elif arm == "admission_intent":
        with closing(sqlite3.connect((path / "intent.sqlite").as_uri() + "?mode=ro", uri=True)) as conn:
            intent_digest = conn.execute("SELECT digest FROM intent WHERE effect_key = ?", (KEY,)).fetchone()[0]
    if intent_digest is not None and intent_digest != digest(payload):
        raise ValueError("Durable intent does not match original proposal")
    new_commit = False
    valid_at_commit = None
    current_goal = False
    if effect:
        attempt = attempts[0]
        expected_attempt = "original" if spec["window"] == "after_commit" else "recovery"
        expected_commit_version = 1 if expected_attempt == "original" else final_version
        if not (
            attempt["attempt_id"] == expected_attempt and attempt["outcome"] == "ACCEPTED"
            and effect["effect_key"] == attempt["effect_key"] == KEY
            and effect["payload"] == payload
            and digest(effect["payload"]) == effect["payload_hash"] == attempt["payload_hash"]
            and effect["world_version"] == attempt["world_version_observed"] == expected_commit_version
            and effect["logical_effect_count"] == 1 and effect["receipt_id"]
        ):
            raise ValueError("Target commit cannot be independently reconciled")
        valid_at_commit = effect["payload"].decode() == frozen["legal"][str(effect["world_version"])]
        current_goal = effect["payload"].decode() == frozen["legal"][str(final_version)]
        new_commit = expected_attempt == "recovery"
    elif spec["window"] == "after_commit":
        raise ValueError("Pre-exit commit is missing")
    return {
        **spec, "outcome": report["outcome"], "worker_exit": report["worker_exit"],
        "target_present": bool(effect), "new_commit": new_commit, "valid_at_commit": valid_at_commit,
        "current_goal": current_goal, "bound_to_intent": bool(effect and intent_digest == effect["payload_hash"]),
        "target_digest": effect["payload_hash"] if effect else None,
    }


def analyze(output):
    manifest = read(output / "manifest.json")
    matrix = list(cases())
    if manifest["cases"] != matrix or manifest["fixture"] != fixture():
        raise ValueError("Manifest differs from protocol inputs")
    rows = [score(output / case["id"], case, manifest["fixture"]) for case in matrix]
    contrasts = []
    for key in sorted({(r["payload"], r["window"], r["changed"]) for r in rows}):
        group = {r["arm"]: r for r in rows if (r["payload"], r["window"], r["changed"]) == key}
        metrics = ("target_present", "new_commit", "valid_at_commit", "current_goal", "bound_to_intent", "target_digest")
        equal = all(group["admission_intent"][m] == group["claim_checked"][m] for m in metrics)
        contrasts.append({"condition": list(key), "checked_paths_equal": equal})
    verdict = ("NO_GO_CLAIM_SPECIFIC_ADVANTAGE" if all(r["checked_paths_equal"] for r in contrasts)
               else "CHECKED_PATHS_DIFFER_REVIEW_REQUIRED")
    return {"verdict": verdict, "cells": len(rows), "contrasts": contrasts,
            "invalid_new_commits": [r["id"] for r in rows if r["new_commit"] and not r["valid_at_commit"]],
            "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "audit", "worker"))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "worker":
        prepare_and_exit(output)
    elif args.mode == "run":
        output.mkdir(parents=True, exist_ok=False)
        frozen = fixture()
        sources = [
            Path(__file__), Path(__file__).with_name("PROTOCOL.md"), ROOT / "scripts/m4_tasks.py",
            *sorted((ROOT / "experiments/mechanism_validation/runtime").glob("*.py")),
            ROOT / "experiments/mechanism_validation/architecture/target_ledger_schema.sql",
        ]
        save(output / "manifest.json", {"fixture": frozen, "cases": list(cases()),
            "python": sys.version, "sqlite": sqlite3.sqlite_version,
            "source_sha256": {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in sources}})
        for spec in cases():
            run_case(output / spec["id"], spec, frozen)
        result = analyze(output)
        save(output / "report.json", result)
        save(output / "sha256.json", {p.relative_to(output).as_posix(): digest(p.read_bytes())
                                      for p in sorted(output.rglob("*")) if p.is_file()})
    else:
        hashes = read(output / "sha256.json")
        files = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
        if files != set(hashes) | {"sha256.json"}:
            raise ValueError("Archive file inventory mismatch")
        for name, checksum in hashes.items():
            if digest((output / name).read_bytes()) != checksum:
                raise ValueError(f"Evidence hash mismatch: {name}")
        result = analyze(output)
        if result != read(output / "report.json"):
            raise ValueError("Recomputed report differs from frozen report")
    print(json.dumps({key: result[key] for key in result if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
