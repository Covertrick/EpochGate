"""Run or independently reconstruct the bounded submission-history experiment."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from epochgate_runtime.files import publish_file
from epochgate_runtime.ledger import RunLedger
from mechanism_validation.runtime.receiver_server import apply_control, apply_query, apply_submit, init_db
from mechanism_validation.runtime.sqlite_util import open_sqlite

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = Path(__file__).with_name("PROTOCOL.md")
SCHEDULES = ["before_check", "check_commit_gap", "after_commit"]
PORTFOLIOS = {
    "ordinary": ["before_check", "after_commit"],
    "paired": ["check_commit_gap", "after_commit"],
    "strong_existing": ["before_check", "check_commit_gap"],
}
FIXTURES = {
    "inventory": ["order 3", "order 0"],
    "routing": ["route east", "route west"],
    "irrelevant_update": ["order 3", "order 3"],
}
ARMS = ["sqlite", "sqlite_fenced", "file"]


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def cases():
    for arm in ARMS:
        for fixture, values in FIXTURES.items():
            for schedule in SCHEDULES:
                yield {
                    "id": f"{arm}-{fixture}-{schedule}",
                    "arm": arm,
                    "fixture": fixture,
                    "schedule": schedule,
                    "worlds": {"1": values[0], "2": values[1]},
                }


def run_case(directory, case):
    directory.mkdir()
    arm = case["arm"]
    schedule = case["schedule"]
    worlds = case["worlds"]
    version = 1
    trace = []
    responses = {}
    target = directory / "result.txt"
    db = directory / "target.sqlite"
    is_file = arm == "file"
    if is_file:
        store = RunLedger(db)
    else:
        store = open_sqlite(db)
    with closing(store):
        if is_file:
            store.create_run("task")
            store.advance("task", "running")
        else:
            init_db(store)

        def update():
            nonlocal version
            if not is_file:
                apply_control(store, {"op": "set_world_version", "version": 2})
            version = 2
            trace.append({"event": "update", "version": version})

        def submit(key, value, observed):
            payload = value.encode()
            trace.append({"event": "submit", "key": key, "version": version})
            if is_file:
                state = store.get_run("task")
                intent = store.record_intent(
                    "task", key, state.fencing_token, state.state_version,
                    effect_id=key, arguments_hash=digest(payload),
                )
                if not intent["allowed"]:
                    raise RuntimeError(f"Intent blocked: {intent}")
                staging = directory / f"{key}.stage"
                staging.write_bytes(payload)
                result = publish_file(
                    store, workspace=directory, run_id="task", idempotency_key=key,
                    fencing_token=state.fencing_token, relative_path=target.name, staging_path=staging,
                )
                if target.exists():
                    (directory / f"after-{key}.bin").write_bytes(target.read_bytes())
                query = store.effect_row(key)
            else:
                result = apply_submit(store, {
                    "attempt_id": key, "effect_key": key,
                    "payload_b64": base64.b64encode(payload).decode("ascii"),
                    "fence": arm == "sqlite_fenced", "expected_world_version": observed,
                    "expected_payload_hash": digest(payload),
                    "expected_authority": {"token": 1, "epoch": 1, "eligible": True},
                    "authority_token": 1, "authority_epoch": 1,
                })
                query = apply_query(store, {"effect_key": key})
            responses[key] = {"submit": result, "query": query}

        if schedule == "before_check":
            update()
        old = worlds["1"]
        precheck = old == worlds[str(version)]
        observed = version
        trace.append({"event": "precheck", "version": observed, "valid": precheck})
        if schedule == "check_commit_gap":
            update()
        if precheck:
            submit("old", old, observed)
        if schedule == "after_commit":
            update()
        submit("successor", worlds[str(version)], version)
        if is_file:
            state = store.get_run("task")
            authority = {"eligible": state.eligible, "token": state.fencing_token}
        else:
            authority = dict(store.execute("SELECT * FROM receiver_authority").fetchone())
    write_json(directory / "trace.json", {"trace": trace, "responses": responses, "authority": authority})


def score(directory, case):
    observed = json.loads((directory / "trace.json").read_bytes())
    version = 1
    versions = {}
    for event in observed["trace"]:
        if event["event"] == "update":
            version = event["version"]
        if event["event"] == "submit":
            if event["version"] != version or event["key"] in versions:
                raise ValueError("Inconsistent submit ordering")
            versions[event["key"]] = version
    with closing(sqlite3.connect((directory / "target.sqlite").as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if case["arm"] == "file":
            rows = conn.execute(
                "SELECT idempotency_key AS effect_key, result AS payload_hash, receipt AS receipt_id, "
                "fencing_token AS authority_token "
                "FROM effects WHERE phase = 'completed'"
            ).fetchall()
            events = [
                json.loads(r["payload"])
                for r in conn.execute("SELECT payload FROM runtime_events WHERE event_type = 'file_published'")
            ]
            unique = sorted((e["idempotency_key"], e["sha256"], e["receipt"]) for e in events) == sorted(
                (r["effect_key"], r["payload_hash"], r["receipt_id"]) for r in rows
            )
            authority = conn.execute("SELECT eligible, fencing_token AS token FROM runs WHERE run_id = 'task'").fetchone()
            commits = [
                (r, (directory / f"after-{r['effect_key']}.bin").read_bytes(), versions[r["effect_key"]])
                for r in rows
            ]
            final = (directory / "result.txt").read_bytes()
        else:
            rows = conn.execute("SELECT * FROM target_effects").fetchall()
            unique = all(r["logical_effect_count"] == 1 for r in rows)
            authority = conn.execute("SELECT eligible, token FROM receiver_authority").fetchone()
            attempts = {
                r["effect_key"]: (r["world_version_observed"], r["outcome"])
                for r in conn.execute("SELECT * FROM target_attempts")
            }
            expected_attempts = {
                key: (version, observed["responses"][key]["submit"]["outcome"])
                for key, version in versions.items()
            }
            if attempts != expected_attempts:
                raise ValueError("SQLite attempt log disagrees with trace")
            commits = [(r, r["payload"], r["world_version"]) for r in rows]
            final = next((p for r, p, _ in commits if r["effect_key"] == "successor"), b"")

    fail = []
    worlds = case["worlds"]
    for row, payload, version in commits:
        key = row["effect_key"]
        query = observed["responses"][key]["query"]
        if (
            digest(payload) != row["payload_hash"]
            or not query
            or (query.get("receipt") or "") != row["receipt_id"]
            or not row["receipt_id"]
            or versions.get(key) != version
        ):
            raise ValueError(f"Evidence mismatch for {key}")
        if payload != worlds[str(version)].encode():
            fail.append(key)
    actual_keys = sorted(r["effect_key"] for r in rows)
    success = final == worlds["2"].encode() and "successor" in actual_keys
    authority_valid = bool(authority["eligible"]) and all(
        row["authority_token"] == authority["token"] for row in rows
    )
    base_flags = not (success and unique and authority_valid)
    if base_flags:
        raise ValueError("Expected final completion, unique effects and valid authority")
    reported = observed["responses"]
    attempted_keys = sorted(reported)
    if attempted_keys != sorted(versions):
        raise ValueError("Submit events do not match responses")
    withheld = reported.get("old", {}).get("submit", {}).get("outcome")
    if "old" in attempted_keys and "old" not in actual_keys and withheld != "WORLD_VERSION_MISMATCH":
        raise ValueError("Attempted old effect has no commit or verified rejection")
    return {
        **case, "effects": actual_keys, "final_match": success,
        "baseline_flags": base_flags, "historical_flags": base_flags or bool(fail),
        "invalid_effects": fail, "old_rejection": withheld if "old" not in actual_keys else None,
    }


def analyze(output):
    manifest = json.loads((output / "manifest.json").read_bytes())
    if manifest["cases"] != list(cases()):
        raise ValueError("Case manifest does not match the protocol matrix")
    rows, errors = [], []
    for case in manifest["cases"]:
        directory = output / case["id"]
        try:
            rows.append(score(directory, case))
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            errors.append({"case": case["id"], "error": str(exc)})

    counts = {}
    for arm in ARMS:
        counts[arm] = {
            name: len({r["fixture"] for r in rows if r["arm"] == arm
                       and r["schedule"] in schedules and r["historical_flags"]})
            for name, schedules in PORTFOLIOS.items()
        }
    vulnerable = ("sqlite", "file")
    positives = [r for r in rows if r["historical_flags"]]
    expected = {
        (arm, fixture, "check_commit_gap")
        for arm in vulnerable for fixture in ("inventory", "routing")
    }
    observed = {(r["arm"], r["fixture"], r["schedule"]) for r in positives}
    fenced_negatives = [r for r in rows if r["arm"] == "sqlite_fenced" and r["fixture"] != "irrelevant_update"
                        and r["schedule"] == "check_commit_gap"]
    fenced_ok = len(fenced_negatives) == 2 and all(
        r["old_rejection"] == "WORLD_VERSION_MISMATCH" and r["effects"] == ["successor"]
        for r in fenced_negatives
    )
    late_controls = [r for r in rows if r["schedule"] == "after_commit"]
    late_ok = len(late_controls) == len(ARMS) * len(FIXTURES) and all(
        r["effects"] == ["old", "successor"] for r in late_controls
    )
    supported = not errors and observed == expected and fenced_ok and late_ok
    gains = all(counts[arm]["paired"] > counts[arm]["ordinary"] for arm in vulnerable)
    strong_gain = all(counts[arm]["paired"] > counts[arm]["strong_existing"] for arm in vulnerable)
    if errors:
        verdict = "EVIDENCE_INCOMPLETE"
    elif supported and gains:
        verdict = "SCHEDULING_GAIN_SUPPORTED" if strong_gain else "PHENOMENON_SUPPORTED_NO_NEW_SCHEDULING_GAIN"
    else:
        verdict = "PHENOMENON_NOT_CONFIRMED"
    return {
        "verdict": verdict, "cells": len(rows), "errors": errors,
        "final_flags": sum(not r["final_match"] for r in rows),
        "authorization_dedup_receipt_flags": sum(r["baseline_flags"] for r in rows),
        "hidden_counterexamples": len(positives), "controls": {"fenced": fenced_ok, "legal_late": late_ok},
        "equal_budget_detection": counts, "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["run", "audit"])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "run":
        output.mkdir(parents=True)
        inputs = list(cases())
        source_paths = [
            PROTOCOL, Path(__file__), Path(sys.modules[publish_file.__module__].__file__),
            Path(sys.modules[RunLedger.__module__].__file__),
            Path(sys.modules[apply_submit.__module__].__file__),
        ]
        write_json(output / "manifest.json", {
            "cases": inputs, "python": sys.version, "sqlite": sqlite3.sqlite_version,
            "sources": {p.resolve().relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in source_paths},
        })
        for case in inputs:
            directory = output / case["id"]
            try:
                run_case(directory, case)
            except Exception as exc:
                write_json(directory / "error.json", {"type": type(exc).__name__, "message": str(exc)})
        report = analyze(output)
        write_json(output / "report.json", report)
        evidence = {p.relative_to(output).as_posix(): digest(p.read_bytes()) for p in sorted(output.rglob("*"))
                    if p.is_file()}
        write_json(output / "sha256.json", evidence)
    else:
        evidence = json.loads((output / "sha256.json").read_bytes())
        for name, expected in evidence.items():
            if digest((output / name).read_bytes()) != expected:
                raise ValueError(f"Evidence hash mismatch: {name}")
        report = analyze(output)
        if report != json.loads((output / "report.json").read_bytes()):
            raise ValueError("Recomputed report differs from the frozen report")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
