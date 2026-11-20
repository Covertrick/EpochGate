"""Execute frozen configuration-selection batches and audit them offline."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import random
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from mechanism_validation.runtime.admission_path import run_admission_path
from mechanism_validation.runtime.claim_mechanism import run_claim_path
from mechanism_validation.runtime.direct_path import run_direct_path
from mechanism_validation.runtime.faults import FaultState
from mechanism_validation.runtime.receiver_server import apply_control, apply_submit, init_db
from mechanism_validation.runtime.sqlite_util import open_sqlite
from mechanism_validation.runtime.types import EffectRequest

ARMS = ("Direct", "Admission", "Claim")
CONDITIONS = ("delivered", "dropped")
LEVELS = {"S0": ("T",), "S1": ("T", "A"), "S2-Sem": ("T", "A", "S"),
          "S2-Succ": ("T", "A", "N"), "S2": ("T", "A", "S", "N")}
POLICY = {"terminal_min": 1, "violation_max": 0, "unknown_max": 0,
          "pareto_cost": ["submits", "durable_claims"], "shuffle_seed": 20261121}


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def save(path, value):
    with path.open("x", encoding="utf-8") as file:
        json.dump(value, file, indent=2, sort_keys=True)
        file.write("\n")


def load(path):
    return json.loads(path.read_bytes())


def fixtures(batch):
    groups = {"invalidating": 5, "benign": 4, "obligation": 3, "revocation": 2, "clean": 2}
    if batch == "acceptance":
        groups = dict.fromkeys(("invalidating", "revocation", "clean"), 1)
    for group, count in groups.items():
        for instance in range(count):
            units = 3 + instance
            stock = units + 5
            remaining = {
                "invalidating": units - 1, "benign": units + 2, "obligation": units + 1,
                "revocation": stock, "clean": stock,
            }[group]
            yield {
                "id": f"{batch}-{group}-{instance + 1:02}", "stratum": group,
                "units": units, "initial_stock": stock, "target_stock": remaining,
                "target_permits_reservation": group != "revocation",
                "ack_required_if_withheld": group == "obligation",
            }


class Receiver:
    """Adapt the actual target transaction to a local, synchronous test boundary."""

    def __init__(self, connection):
        self.connection = connection

    def submit(self, *, payload, **request):
        return apply_submit(self.connection, {
            **request, "payload_b64": base64.b64encode(payload).decode("ascii"),
        })


class Delivery(FaultState):
    def __init__(self, apply):
        super().__init__(None)
        self.apply = apply

    def hit(self, point, world, auth):
        if point == "authority_gate" and point not in self._applied:
            self.apply(auth)
        super().hit(point, world, auth)


def execute(case, arm, condition, directory):
    directory.mkdir()
    events = []
    run_id = f"{case['id']}:{arm}:{condition}"

    def event(name, **values):
        events.append({"sequence": len(events) + 1, "event": name, **values})

    local = {"eligible": True, "epoch": 1, "token": 1}
    old_payload = f"RESERVE:{case['units']}".encode()
    changed = case["initial_stock"] != case["target_stock"]
    revoked = not case["target_permits_reservation"]
    version = 2 if changed or revoked else 1
    notice = {"event_id": f"{case['id']}:notice", "stock": case["target_stock"],
              "generation": version, "eligible": not revoked}
    save(directory / "input.json", {**case, "run_id": run_id, "arm": arm,
                                    "condition": condition, "notice": notice,
                                    "proposal_sha256": sha(old_payload)})

    with closing(open_sqlite(directory / "target.sqlite")) as conn:
        init_db(conn)
        conn.execute("CREATE TABLE world_policy (version INTEGER PRIMARY KEY, stock INTEGER, can_reserve INTEGER)")
        conn.execute("INSERT INTO world_policy VALUES (1, ?, 1)", (case["initial_stock"],))
        client = Receiver(conn)

        def delivery(auth):
            if version != 1:
                conn.execute("INSERT INTO world_policy VALUES (?, ?, ?)",
                             (version, case["target_stock"], int(not revoked)))
                apply_control(conn, {"op": "set_world_version", "version": version})
            if changed:
                event("BUSINESS_STATE_COMMITTED", version=version)
            if revoked:
                event("AUTHORITY_POLICY_CHANGED", version=version)
            event("NOTIFICATION_EMITTED", **notice)
            if condition == "delivered":
                event("TRANSPORT_DELIVERED", event_id=notice["event_id"])
                event("CONTROL_PLANE_RECEIVED", event_id=notice["event_id"])
                auth.update(eligible=not revoked, token=version, epoch=version)
                event("CONTROL_PLANE_APPLIED", event_id=notice["event_id"], observed=auth.copy())
            else:
                event("TRANSPORT_DROPPED", event_id=notice["event_id"])

        def action(key, payload, auth, faults):
            request = EffectRequest(case["id"], key, key, payload, sha(payload), 1, 1, 1)
            args = (client, request, auth, faults, {})
            kwargs = {"attempt_id": key, "conflict_payload": b""}
            if arm == "Claim":
                result = run_claim_path(directory / "claim.sqlite", *args, **kwargs)
            else:
                path = run_direct_path if arm == "Direct" else run_admission_path
                result = path(*args, **kwargs)
            event("PATH_RETURNED", action=key, outcome=result["outcome"], ack=result["local_ack"])
            return result["outcome"]

        started = time.perf_counter_ns()
        reservation = action("reserve", old_payload, local, Delivery(delivery))
        if reservation in {"ACCEPTED", "WITHHELD"}:
            disposition = f"DISPOSITION:{reservation}".encode()
            action("disposition", disposition, {"eligible": True, "token": 1, "epoch": 1}, FaultState(None))
        elapsed = time.perf_counter_ns() - started
    save(directory / "runtime.json", {"events": events, "elapsed_ns": elapsed})


def audit_cell(directory, case, arm, condition):
    spec = load(directory / "input.json")
    trace = load(directory / "runtime.json")
    expected = {**case, "run_id": f"{case['id']}:{arm}:{condition}",
                "arm": arm, "condition": condition}
    if any(spec.get(k) != v for k, v in expected.items()):
        raise ValueError(f"Mismatched input in {directory.name}")
    if spec["proposal_sha256"] != sha(f"RESERVE:{case['units']}".encode()):
        raise ValueError(f"Wrong proposal in {directory.name}")
    events = trace["events"]
    event_types = [e["event"] for e in events]
    required = (["NOTIFICATION_EMITTED", "TRANSPORT_DELIVERED", "CONTROL_PLANE_RECEIVED",
                 "CONTROL_PLANE_APPLIED"] if condition == "delivered"
                else ["NOTIFICATION_EMITTED", "TRANSPORT_DROPPED"])
    if [n for n in event_types if n.startswith(("NOTIFICATION", "TRANSPORT", "CONTROL_PLANE"))] != required:
        raise ValueError(f"Invalid notification trace in {directory.name}")
    expected_notice = {"event_id": f"{case['id']}:notice", "stock": case["target_stock"],
                       "generation": 2 if case["initial_stock"] != case["target_stock"]
                       or not case["target_permits_reservation"] else 1,
                       "eligible": case["target_permits_reservation"]}
    if spec["notice"] != expected_notice or any(
        e["event_id"] != expected_notice["event_id"] for e in events if e["event"] in required
    ):
        raise ValueError(f"Invalid event binding in {directory.name}")

    with closing(sqlite3.connect((directory / "target.sqlite").as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        effects = {r["effect_key"]: dict(r) for r in conn.execute(
            "SELECT e.*, w.stock, w.can_reserve FROM target_effects e "
            "JOIN world_policy w ON e.world_version = w.version"
        )}
        attempts = dict(conn.execute("SELECT effect_key, outcome FROM target_attempts").fetchall())
    for key, effect in effects.items():
        if (sha(effect["payload"]) != effect["payload_hash"] or attempts.get(key) != "ACCEPTED"
                or effect["logical_effect_count"] != 1 or not effect["receipt_id"]):
            raise ValueError(f"Inconsistent target evidence for {key} in {directory.name}")
        if effect["world_version"] != expected_notice["generation"]:
            raise ValueError(f"Unexpected commit world in {directory.name}")
    if set(attempts) != set(effects):
        raise ValueError(f"Unreconciled attempt in {directory.name}")
    reserve = effects.get("reserve")
    disposition = effects.get("disposition")
    if reserve and reserve["payload"] != f"RESERVE:{case['units']}".encode():
        raise ValueError(f"Committed reservation did not match frozen proposal in {directory.name}")
    logical_outcome = "ACCEPTED" if reserve else "WITHHELD"
    terminal = bool(disposition and disposition["payload"] == f"DISPOSITION:{logical_outcome}".encode())
    authority = not reserve or bool(reserve["can_reserve"])
    semantic = not reserve or case["units"] <= reserve["stock"]
    obligation_applies = case["ack_required_if_withheld"] and not reserve
    successor = not obligation_applies or "ack" in effects
    claims = 0
    if arm == "Claim":
        with closing(sqlite3.connect((directory / "claim.sqlite").as_uri() + "?mode=ro", uri=True)) as conn:
            claims = conn.execute("SELECT COUNT(*) FROM claim_rows").fetchone()[0]
    scores = {"T": terminal, "A": authority, "S": semantic, "N": successor}
    return {
        "id": case["id"], "arm": arm, "condition": condition, "stratum": case["stratum"],
        "scores": scores, "evidence": "CONFIRMED",
        "reservation": "CONFIRMED_APPLIED" if reserve else "CONFIRMED_NOT_APPLIED",
        "successor": "NOT_APPLICABLE" if not obligation_applies else
        "REQUIRED_SATISFIED" if successor else "REQUIRED_MISSED",
        "levels": {name: all(scores[axis] for axis in dims) for name, dims in LEVELS.items()},
        "submits": len(attempts), "durable_claims": claims, "elapsed_ns": trace["elapsed_ns"],
        "effects": {key: {"digest": row["payload_hash"], "version": row["world_version"]}
                    for key, row in effects.items()},
    }


def recommend(rows, level):
    costs = {}
    rejected = {}
    for arm in ARMS:
        cells = [r for r in rows if r["arm"] == arm]
        failed = [c["id"] for c in cells if not c["levels"][level]]
        rejected[arm] = failed
        if not failed:
            costs[arm] = [sum(c[k] for c in cells) for k in POLICY["pareto_cost"]]
    front = [
        arm for arm, value in costs.items()
        if not any(other != value and all(a <= b for a, b in zip(other, value))
                   for other in costs.values())
    ]
    return {"eligible": list(costs), "recommend": front, "costs": costs, "rejected": rejected}


def paired(rows):
    indexed = {(r["id"], r["condition"], r["arm"]): r for r in rows}
    cases = sorted({r["id"] for r in rows})
    counts = {}
    for condition in CONDITIONS:
        differences = {}
        for key in cases:
            values = []
            for arm in ("Claim", "Admission"):
                cell = indexed[key, condition, arm]["levels"]
                values.append(int(cell["S1"] and not cell["S2"]))
            differences[key] = values[0] - values[1]
        plus, minus = list(differences.values()).count(1), list(differences.values()).count(-1)
        discordance = plus + minus
        p = min(1.0, 2 * sum(math.comb(discordance, n) for n in range(min(plus, minus) + 1))
                / 2 ** discordance)
        counts[condition] = {"claim_only": plus, "admission_only": minus, "difference":
                             (plus - minus) / len(cases), "mcnemar_p_descriptive": p}
    return {"conditions": counts, "interaction":
            counts["delivered"]["difference"] - counts["dropped"]["difference"]}


def analyze(output, batch):
    rows = [
        audit_cell(output / f"{case['id']}-{arm}-{condition}", case, arm, condition)
        for case in fixtures(batch) for arm in ARMS for condition in CONDITIONS
    ]
    results = {}
    for condition in CONDITIONS:
        subset = [r for r in rows if r["condition"] == condition]
        results[condition] = {level: recommend(subset, level) for level in LEVELS}
    changes = [c for c in CONDITIONS if results[c]["S1"]["recommend"] != results[c]["S2"]["recommend"]]
    primary = paired(rows)
    primary_results = [
        condition for condition in changes
        if any((arm in results[condition]["S1"]["eligible"]) !=
               (arm in results[condition]["S2"]["eligible"]) for arm in ("Claim", "Admission"))
        and any(primary["conditions"][condition][key] for key in ("claim_only", "admission_only"))
    ]
    if batch == "acceptance":
        checks = {
            (r["stratum"], r["condition"], r["arm"]): r for r in rows
        }
        for arm in ARMS:
            for condition in CONDITIONS:
                clean = checks["clean", condition, arm]["scores"]
                if not all(clean.values()):
                    raise ValueError("Clean control failed")
                stale_commits = condition == "dropped" or arm == "Direct"
                if checks["revocation", condition, arm]["scores"]["A"] == stale_commits:
                    raise ValueError("Revocation control failed")
                business = checks["invalidating", condition, arm]["scores"]
                if not business["A"] or business["S"] == stale_commits:
                    raise ValueError("Business/authority separation failed")
        verdict = "G1_PASS"
    elif primary_results:
        verdict = "G2_PROCEED_TO_CONFIRMATION_DESIGN"
    else:
        verdict = "G2_NO_GO_ORIGINAL_CLAIM"
    return {"verdict": verdict, "cells": len(rows), "paired_primary": primary,
            "recommendation_changes": changes, "primary_decision_conditions": primary_results,
            "decisions": results, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "audit"))
    parser.add_argument("batch", choices=("acceptance", "pilot"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--acceptance", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "run":
        if args.batch == "pilot" and (
            args.acceptance is None or analyze(args.acceptance.resolve(), "acceptance")["verdict"] != "G1_PASS"
        ):
            parser.error("Pilot requires an audited acceptance directory")
        output.mkdir(parents=True, exist_ok=False)
        root = Path(__file__).resolve().parent
        sources = [*root.glob("*.py"), root / "PROTOCOL.md",
                   *(root.parent / "mechanism_validation" / "runtime").glob("*.py"),
                   root.parent / "mechanism_validation" / "architecture" / "target_ledger_schema.sql"]
        save(output / "manifest.json", {"batch": args.batch, "policy": POLICY,
             "fixtures": list(fixtures(args.batch)),
             "source_sha256": {p.relative_to(root.parent).as_posix(): sha(p.read_bytes()) for p in sources}})
        rng = random.Random(POLICY["shuffle_seed"])
        for case in fixtures(args.batch):
            order = [(arm, cond) for arm in ARMS for cond in CONDITIONS]
            rng.shuffle(order)
            save(output / f"{case['id']}-order.json", order)
            for arm, condition in order:
                execute(case, arm, condition, output / f"{case['id']}-{arm}-{condition}")
        result = analyze(output, args.batch)
        save(output / "report.json", result)
        save(output / "sha256.json", {p.relative_to(output).as_posix(): sha(p.read_bytes())
                                     for p in output.rglob("*") if p.is_file()})
    else:
        for file, checksum in load(output / "sha256.json").items():
            if sha((output / file).read_bytes()) != checksum:
                raise ValueError(f"Content hash mismatch: {file}")
        result = analyze(output, args.batch)
        if result != load(output / "report.json"):
            raise ValueError("Recomputed report differs")
    print(json.dumps({key: result[key] for key in (
        "verdict", "cells", "paired_primary", "recommendation_changes", "primary_decision_conditions"
    )}, indent=2))


if __name__ == "__main__":
    main()
