"""Deterministic second generator. Not a language model.

Thresholds live in comparison_thresholds.json and are read, not rewritten.
Week 4 lattice files are hashed and copied into the registry; those bytes stay put.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from frozen_proposals.oracles import ORACLE_VERSIONS
from frozen_proposals.predicate import PREDICATE_VERSION, legal_payload

from mechanism_validation.runtime.hashing import canonical_bytes, sha256_bytes, sha256_obj
from mechanism_validation.runtime.receiver_server import RECEIVER_VERSION

GENERATOR_ID = "structured_factor_generator_v2"
GENERATOR_VERSION = "structured_factor_generator_v2"
ROOT = Path(__file__).resolve().parents[2]
PROPOSALS = ROOT / "mechanism_validation" / "proposals"
BYTES = PROPOSALS / "bytes"
THRESHOLDS = PROPOSALS / "comparison_thresholds.json"
REGISTRY = PROPOSALS / "registry.jsonl"
COMPARISON = PROPOSALS / "generator_comparison.csv"
MANIFEST = ROOT / "mechanism_validation" / "experiments" / "replay_manifest.json"
WEEK4 = ROOT / "crash_recovery" / "proposals"
PATHS = ["Direct", "Admission", "Claim", "Outbox"]

# cluster, family, scenario, seed, sku, dest, qty, order_id, world_version,
# generation, forbidden, eligible, epoch, successor, stop, fault_op, fault_point,
# successor_delta, drift_delta
CLUSTERS = [
    ("w5c01", "authority", "W5-AUTH-01", 5201, "D", "west", 12, "WH-441", 4, 0, False, True, 1, "NOT_REQUIRED", "MUST_STOP", "revoke", "before_effect", 4, 9),
    ("w5c02", "authority", "W5-AUTH-02", 5202, "E", "north", 40, "PO-19-B", 1, 0, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", "stale_epoch", "before_effect", 2, 8),
    ("w5c03", "authority", "W5-AUTH-03", 5203, "A", "south", 3, "NX-7", 7, 0, False, False, 1, "NOT_REQUIRED", "MUST_STOP", None, None, 1, 5),
    ("w5c04", "authority", "W5-AUTH-04", 5204, "B", "east", 18, "KA-880", 2, 0, False, True, 2, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", "stale_epoch", "before_effect", 6, 3),
    ("w5c05", "authority", "W5-AUTH-05", 5205, "C", "port", 7, "UL-12", 9, 0, False, True, 1, "NOT_REQUIRED", "MUST_STOP", "revoke", "before_effect", 3, 11),
    ("w5c06", "authority", "W5-AUTH-06", 5206, "F", "west", 25, "MM-3", 3, 0, False, False, 2, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", None, None, 5, 4),
    ("w5c07", "semantic", "W5-SEM-01", 5207, "A", "west", 2, "SEM-DRIFT-7", 5, 0, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", "drift", "before_effect", 2, 7),
    ("w5c08", "semantic", "W5-SEM-02", 5208, "B", "port", 60, "OPEN-60", 8, 0, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", None, None, 1, 6),
    ("w5c09", "semantic", "W5-SEM-03", 5209, "C", "north", 9, "FORB-9", 1, 0, True, True, 1, "NOT_REQUIRED", "MUST_STOP", None, None, 2, 2),
    ("w5c10", "semantic", "W5-SEM-04", 5210, "D", "south", 15, "CONF-15", 6, 0, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", "conflict_delivery", "before_send", 4, 5),
    ("w5c11", "semantic", "W5-SEM-05", 5211, "E", "east", 33, "DRIFT-33", 2, 0, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", "drift", "before_effect", 8, 10),
    ("w5c12", "semantic", "W5-SEM-06", 5212, "A", "north", 4, "GEN-4", 4, 2, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", None, None, 3, 4),
    ("w5c13", "recovery", "W5-REC-01", 5213, "B", "west", 21, "REC-21", 11, 0, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", "restart_after_commit", "after_commit_before_send", 2, 6),
    ("w5c14", "recovery", "W5-REC-02", 5214, "C", "port", 8, "REC-8", 1, 4, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", "restart_after_commit", "after_commit_before_send", 1, 3),
    ("w5c15", "recovery", "W5-REC-03", 5215, "F", "south", 14, "REC-14", 3, 0, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", "restart_after_commit", "after_commit_before_send", 5, 8),
    ("w5c16", "recovery", "W5-REC-04", 5216, "D", "east", 27, "REC-27", 2, 0, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", "restart_after_commit", "after_commit_before_send", 7, 2),
    ("w5c17", "recovery", "W5-REC-05", 5217, "E", "north", 6, "REC-6", 5, 0, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", "restart_after_commit", "after_commit_before_send", 2, 9),
    ("w5c18", "recovery", "W5-REC-06", 5218, "B", "south", 11, "REC-11", 8, 0, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", "restart_after_commit", "after_commit_before_send", 3, 5),
    ("w5c19", "successor", "W5-SUC-01", 5219, "C", "west", 19, "SUC-19", 1, 0, False, True, 1, "REQUIRED", "NOT_A_STOP_SCENARIO", "supersede", "before_effect", 1, 4),
    ("w5c20", "successor", "W5-SUC-02", 5220, "A", "port", 5, "SUC-5", 4, 1, False, True, 1, "REQUIRED", "NOT_A_STOP_SCENARIO", "supersede", "before_effect", 2, 6),
    ("w5c21", "successor", "W5-SUC-03", 5221, "F", "east", 30, "SUC-30", 6, 0, False, True, 1, "REQUIRED", "NOT_A_STOP_SCENARIO", "supersede", "before_effect", 4, 7),
    ("w5c22", "successor", "W5-SUC-04", 5222, "E", "south", 16, "SUC-16", 2, 0, False, True, 1, "REQUIRED", "NOT_A_STOP_SCENARIO", "supersede", "before_effect", 3, 3),
    ("w5c23", "successor", "W5-SUC-05", 5223, "D", "north", 22, "STOP-22", 9, 0, False, True, 1, "NOT_REQUIRED", "MUST_STOP", "revoke", "before_effect", 6, 8),
    ("w5c24", "successor", "W5-SUC-06", 5224, "C", "east", 13, "HOLD-13", 3, 0, False, True, 1, "NOT_REQUIRED", "NOT_A_STOP_SCENARIO", None, None, 2, 5),
]


def semantic_class(world: dict[str, Any], auth: dict[str, Any]) -> str:
    if not auth["eligible"]:
        return "INELIGIBLE_AUTHORITY"
    if world.get("effect_forbidden"):
        return "EFFECT_FORBIDDEN"
    if int(world["generation"]) != 0:
        return "LATER_GENERATION"
    return "LEGAL_INITIAL"


def alias_locked(world: dict[str, Any]) -> bool:
    sku_order = ["A", "B", "C"]
    dest_order = ["north", "south", "east"]
    if world["sku"] not in sku_order or world["dest"] not in dest_order:
        return False
    return sku_order.index(world["sku"]) == dest_order.index(world["dest"])


def _schedule(op: str | None, point: str | None) -> dict[str, Any]:
    if op is None or point is None:
        return {"points": []}
    return {"points": [{"point": point, "actions": [{"op": op}]}]}


def _record_from_cluster(index: int, row: tuple[Any, ...]) -> dict[str, Any]:
    (
        proposal_id,
        family,
        scenario_id,
        seed,
        sku,
        dest,
        qty,
        order_id,
        world_version,
        generation,
        forbidden,
        eligible,
        epoch,
        successor,
        stop,
        fault_op,
        fault_point,
        successor_delta,
        drift_delta,
    ) = row
    world = {
        "order_id": order_id,
        "sku": sku,
        "qty": qty,
        "dest": dest,
        "generation": generation,
        "effect_forbidden": forbidden,
        "successor_qty": qty + successor_delta,
        "drift_qty": qty + drift_delta,
        "world_version": world_version,
    }
    authority = {"token": 1, "epoch": epoch, "eligible": eligible}
    payload = legal_payload(world)
    successor_world = dict(world)
    successor_world["qty"] = world["successor_qty"]
    successor_world["generation"] = int(world["generation"]) + 1
    successor_world["effect_forbidden"] = False
    successor_payload = legal_payload(successor_world)
    schedule = _schedule(fault_op, fault_point)
    shift = index % len(PATHS)
    parameters = {
        "sku": sku,
        "dest": dest,
        "qty": qty,
        "order_id": order_id,
        "world_version": world_version,
        "generation": generation,
        "effect_forbidden": forbidden,
        "eligible": eligible,
        "epoch": epoch,
        "successor_obligation": successor,
        "stop_condition": stop,
        "fault_op": fault_op,
        "successor_delta": successor_delta,
        "drift_delta": drift_delta,
    }
    return {
        "record_kind": "week5_generator_v2",
        "generator_id": GENERATOR_ID,
        "generator_version": GENERATOR_VERSION,
        "external_llm_called": False,
        "calls_make_world": False,
        "proposal_id": proposal_id,
        "scenario_id": scenario_id,
        "family": family,
        "seed": seed,
        "parameters": parameters,
        "parameter_sha256": sha256_obj(parameters),
        "payload_path": f"mechanism_validation/proposals/bytes/{proposal_id}.bin",
        "payload_sha256": sha256_bytes(payload),
        "payload_length": len(payload),
        "successor_payload_path": f"mechanism_validation/proposals/bytes/{proposal_id}.successor.bin",
        "successor_payload_sha256": sha256_bytes(successor_payload),
        "task_semantic_version": PREDICATE_VERSION,
        "idempotency_key": f"w5:{proposal_id}:{order_id}",
        "relay_id": f"relay:{proposal_id}",
        "authority_token": 1,
        "authority_epoch": epoch,
        "world_version": world_version,
        "authority_initial": authority,
        "authority_initial_hash": sha256_obj(authority),
        "world_before": world,
        "world_before_hash": sha256_obj(world),
        "fault_schedule": schedule,
        "fault_schedule_hash": sha256_obj(schedule),
        "successor_obligation": successor,
        "stop_condition": stop,
        "semantic_class": semantic_class(world, authority),
        "alias_locked": alias_locked(world),
        "oracle_adjudicable": True,
        "path_order": PATHS[shift:] + PATHS[:shift],
        "_payload": payload,
        "_successor_payload": successor_payload,
    }


def _week4_copies() -> list[dict[str, Any]]:
    copies = []
    for line in (WEEK4 / "second_lattice_registry.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        payload_path = WEEK4 / f"{row['proposal_id']}.bin"
        successor_path = WEEK4 / f"{row['proposal_id']}.successor.bin"
        payload_hash = sha256_bytes(payload_path.read_bytes())
        successor_hash = sha256_bytes(successor_path.read_bytes())
        if payload_hash != row["payload_sha256"] or successor_hash != row["successor_payload_sha256"]:
            raise RuntimeError(f"week4 hash mismatch for {row['proposal_id']}")
        copies.append(
            {
                "record_kind": "week4_lattice_hash_copy",
                "generator_id": row["generator_id"],
                "proposal_id": row["proposal_id"],
                "scenario_id": row["scenario_id"],
                "seed": row["seed"],
                "payload_sha256": row["payload_sha256"],
                "successor_payload_sha256": row["successor_payload_sha256"],
                "world_before_hash": row["world_before_hash"],
                "world_after_hash": row["world_after_hash"],
                "authority_initial_hash": row["authority_initial_hash"],
                "authority_after_hash": row["authority_after_hash"],
                "fault_schedule_hash": row["fault_schedule_hash"],
                "source_payload_path": f"crash_recovery/proposals/{row['proposal_id']}.bin",
                "bytes_modified": False,
                "external_llm_called": False,
            }
        )
    return copies


def _load_generator_1() -> list[dict[str, Any]]:
    rows = []
    for base in (ROOT / "frozen_proposals", ROOT / "fault_replay"):
        for line in (base / "proposal_registry.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            payload_path = base / row["payload_path"]
            payload = payload_path.read_bytes()
            row["payload_length"] = len(payload)
            row["payload_bytes_sha256"] = sha256_bytes(payload)
            row["semantic_class"] = semantic_class(row["world_before"], row["authority_initial"])
            row["alias_locked"] = alias_locked(row["world_before"])
            row["source_tree"] = base.name
            rows.append(row)
    return rows


def _dist(labels: list[str]) -> dict[str, float]:
    counts = Counter(labels)
    total = len(labels) or 1
    return {key: value / total for key, value in counts.items()}


def _tv(left: dict[str, float], right: dict[str, float]) -> float:
    keys = set(left) | set(right)
    return 0.5 * sum(abs(left.get(key, 0.0) - right.get(key, 0.0)) for key in keys)


def _adjudicable(row: dict[str, Any]) -> bool:
    world = row["world_before"]
    try:
        legal_payload(world)
    except (KeyError, TypeError):
        return False
    version = row.get("task_semantic_version")
    if version not in (None, PREDICATE_VERSION):
        return False
    if not row.get("idempotency_key"):
        return False
    auth = row.get("authority_initial") or {}
    return all(key in world for key in ("order_id", "sku", "qty", "dest", "generation")) and "eligible" in auth


def _world_signature(world: dict[str, Any]) -> str:
    return f"{int(bool(world.get('effect_forbidden')))}|{int(int(world['generation']) > 0)}|{world['dest']}"


def _authority_signature(auth: dict[str, Any]) -> str:
    return f"{int(bool(auth['eligible']))}|{int(auth['epoch'])}"


def compare(generated: list[dict[str, Any]]) -> str:
    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    if thresholds.get("frozen_before_comparison") is not True:
        raise RuntimeError("thresholds were not frozen")
    first = _load_generator_1()
    second = generated
    axes = []

    def add(name: str, left: float, right: float, difference: float, cutoff: float, exceeded: bool, detail: str) -> None:
        axes.append(
            {
                "axis": name,
                "generator_1": round(left, 6),
                "generator_2": round(right, 6),
                "difference": round(difference, 6),
                "threshold": cutoff,
                "exceeded": int(exceeded),
                "detail": detail,
            }
        )

    class_tv = _tv(_dist([row["semantic_class"] for row in first]), _dist([row["semantic_class"] for row in second]))
    add("semantic_class_tv", class_tv, class_tv, class_tv, thresholds["semantic_class_tv_gt"], class_tv > thresholds["semantic_class_tv_gt"], "total variation; generator_1 and generator_2 columns both show the distance")
    alias_1 = sum(1 for row in first if row["alias_locked"]) / len(first)
    alias_2 = sum(1 for row in second if row["alias_locked"]) / len(second)
    alias_diff = abs(alias_1 - alias_2)
    add("sku_dest_alias_rate", alias_1, alias_2, alias_diff, thresholds["sku_dest_alias_abs_diff_gt"], alias_diff > thresholds["sku_dest_alias_abs_diff_gt"], "alias means sku and dest share the Week 2 index lock A-north, B-south, C-east")
    len_1 = [row["payload_length"] for row in first]
    len_2 = [row["payload_length"] for row in second]
    mean_1 = sum(len_1) / len(len_1)
    mean_2 = sum(len_2) / len(len_2)
    rel = abs(mean_2 - mean_1) / mean_1
    disjoint = max(len_1) < min(len_2) or max(len_2) < min(len_1)
    length_exceeded = rel > thresholds["payload_length_rel_mean_diff_gt"] or (
        thresholds["payload_length_ranges_disjoint_counts"] and disjoint
    )
    add("payload_length", mean_1, mean_2, rel, thresholds["payload_length_rel_mean_diff_gt"], length_exceeded, f"mean bytes; ranges {min(len_1)}-{max(len_1)} vs {min(len_2)}-{max(len_2)}; disjoint={disjoint}")
    uniq_1 = len({row["idempotency_key"] for row in first}) / len(first)
    uniq_2 = len({row["idempotency_key"] for row in second}) / len(second)
    uniq_diff = abs(uniq_1 - uniq_2)
    add("idempotency_uniqueness", uniq_1, uniq_2, uniq_diff, thresholds["idempotency_uniqueness_abs_diff_gt"], uniq_diff > thresholds["idempotency_uniqueness_abs_diff_gt"], "unique keys divided by batch size")
    adj_1 = sum(1 for row in first if _adjudicable(row)) / len(first)
    adj_2 = sum(1 for row in second if _adjudicable(row)) / len(second)
    adj_diff = abs(adj_1 - adj_2)
    add("oracle_adjudicable_rate", adj_1, adj_2, adj_diff, thresholds["oracle_adjudicable_abs_diff_gt"], adj_diff > thresholds["oracle_adjudicable_abs_diff_gt"], "legal_payload can be evaluated and the predicate version is the shared scorer")
    world_tv = _tv(_dist([_world_signature(row["world_before"]) for row in first]), _dist([_world_signature(row["world_before"]) for row in second]))
    add("world_state_tv", world_tv, world_tv, world_tv, thresholds["world_state_tv_gt"], world_tv > thresholds["world_state_tv_gt"], "signature effect_forbidden|generation>0|dest")
    auth_tv = _tv(
        _dist([_authority_signature(row["authority_initial"]) for row in first]),
        _dist([_authority_signature(row["authority_initial"]) for row in second]),
    )
    add("authority_tv", auth_tv, auth_tv, auth_tv, thresholds["authority_tv_gt"], auth_tv > thresholds["authority_tv_gt"], "signature eligible|epoch")
    succ_tv = _tv(
        _dist([row["successor_obligation"] for row in first]),
        _dist([row["successor_obligation"] for row in second]),
    )
    add("successor_tv", succ_tv, succ_tv, succ_tv, thresholds["successor_tv_gt"], succ_tv > thresholds["successor_tv_gt"], "successor_obligation")
    conclusion = "DIFFERS" if any(row["exceeded"] for row in axes) else "INCONCLUSIVE"
    COMPARISON.parent.mkdir(parents=True, exist_ok=True)
    with COMPARISON.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["axis", "generator_1", "generator_2", "difference", "threshold", "exceeded", "detail", "conclusion"])
        writer.writeheader()
        for row in axes:
            row["conclusion"] = conclusion
            writer.writerow(row)
    return conclusion


def generate() -> list[dict[str, Any]]:
    BYTES.mkdir(parents=True, exist_ok=True)
    generated = []
    for index, cluster in enumerate(CLUSTERS):
        record = _record_from_cluster(index, cluster)
        (BYTES / f"{record['proposal_id']}.bin").write_bytes(record["_payload"])
        (BYTES / f"{record['proposal_id']}.successor.bin").write_bytes(record["_successor_payload"])
        if sha256_bytes((BYTES / f"{record['proposal_id']}.bin").read_bytes()) != record["payload_sha256"]:
            raise RuntimeError("payload hash drifted while writing")
        generated.append(record)
    copies = _week4_copies()
    with REGISTRY.open("w", encoding="utf-8", newline="\n") as handle:
        for row in copies + generated:
            public = {key: value for key, value in row.items() if not key.startswith("_")}
            handle.write(json.dumps(public, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = {
        "frozen_before_replay": True,
        "generator_id": GENERATOR_ID,
        "generator_version": GENERATOR_VERSION,
        "oracle_versions": ORACLE_VERSIONS,
        "predicate_version": PREDICATE_VERSION,
        "receiver_version": RECEIVER_VERSION,
        "receiver_contract": "week5 local test receiver; path may submit and query; auditor opens the receiver database; unfenced idempotent apply",
        "path_order_rule": "Direct, Admission, Claim, Outbox rotated by cluster index",
        "cluster_count": len(generated),
        "note": "Inputs only. Replay reads this file and does not rewrite it.",
        "clusters": [{key: value for key, value in row.items() if not key.startswith("_")} for row in generated],
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_hash = sha256_bytes(canonical_bytes(json.loads(MANIFEST.read_text(encoding="utf-8"))))
    sidecar = MANIFEST.with_suffix(".sha256")
    sidecar.write_text(manifest_hash + "\n", encoding="utf-8")
    return generated


def main() -> None:
    generated = generate()
    conclusion = compare(generated)
    print(f"clusters={len(generated)} conclusion={conclusion}")


if __name__ == "__main__":
    main()
