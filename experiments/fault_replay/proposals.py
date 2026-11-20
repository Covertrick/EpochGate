"""Load Week 2 frozen proposals and generate Week 3 proposals once.

Week 2 payload files are read and never rewritten. New payload files are
written only when absent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from frozen_proposals.faults import apply_op, simulate
from frozen_proposals.freeze_artifacts import INITIAL_AUTHORITY, make_world, schedule_for
from frozen_proposals.freeze_artifacts import SCENARIOS as WEEK2_SCENARIOS
from frozen_proposals.hashing import sha256_bytes, sha256_obj
from frozen_proposals.oracles import ORACLE_VERSIONS
from frozen_proposals.predicate import PREDICATE_VERSION, legal_payload
from fault_replay.contract import (
    FREEZE_SCENARIOS,
    SCENARIO_FAMILY,
    SCENARIO_ROLE,
    SEPARATION_CHECKPOINT,
    SEPARATION_SCENARIOS,
    WEEK2,
    WEEK3,
)
from fault_replay.kill_runner import load_week2_registry
from fault_replay.yaml_io import dump

NEW_SPECS = (
    ("p16", "S03", 2101, 0),
    ("p17", "S03", 2102, 1),
    ("p18", "S03", 2103, 2),
    ("p19", "S13", 2113, 3),
    ("p20", "S14", 2114, 4),
    ("p21", "S15", 2115, 5),
    ("p22", "S16", 2116, 6),
    ("p23", "S12", 2112, 7),
    ("p24", "S17", 2117, 8),
    ("p25", "S17", 2118, 9),
    ("p26", "S18", 2119, 10),
    ("p27", "S18", 2120, 11),
)

FIXTURE_PROPOSAL = {
    "S13": "p19",
    "S14": "p20",
    "S15": "p21",
    "S16": "p22",
    "S17": "p24",
    "S18": "p26",
}


def _mutate(scenario_id: str, world: dict[str, Any], auth: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    world_after = json.loads(json.dumps(world))
    auth_after = json.loads(json.dumps(auth))
    if scenario_id in SEPARATION_SCENARIOS:
        apply_op(world_after, auth_after, "drift")
        schedule = {
            "points": [
                {
                    "point": "after_claim",
                    "actions": [{"op": "drift"}],
                    "checkpoint": SEPARATION_CHECKPOINT[scenario_id],
                }
            ]
        }
        return world_after, auth_after, schedule
    if scenario_id == "S17":
        apply_op(world_after, auth_after, "supersede")
        schedule = {"points": [{"point": "after_claim_before_publish", "actions": [{"op": "supersede"}]}]}
        return world_after, auth_after, schedule
    if scenario_id == "S18":
        apply_op(world_after, auth_after, "revoke")
        schedule = {"points": [{"point": "after_claim_before_publish", "actions": [{"op": "revoke"}]}]}
        return world_after, auth_after, schedule
    if scenario_id not in WEEK2_SCENARIOS:
        raise KeyError(scenario_id)
    schedule = schedule_for(WEEK2_SCENARIOS[scenario_id], "template")
    world_after, auth_after = simulate(world, auth, schedule)
    return world_after, auth_after, schedule


def _labels(scenario_id: str) -> dict[str, str]:
    if scenario_id in SEPARATION_SCENARIOS:
        return {
            "successor_obligation": "NOT_REQUIRED",
            "stop_condition": "NOT_A_STOP_SCENARIO",
            "outdated_scenario_label": "NOTICE_MISSING_OR_DELAYED",
        }
    if scenario_id == "S17":
        return {
            "successor_obligation": "REQUIRED",
            "stop_condition": "NOT_A_STOP_SCENARIO",
            "outdated_scenario_label": "TIMELY_NOTICE",
        }
    if scenario_id == "S18":
        return {
            "successor_obligation": "REQUIRED",
            "stop_condition": "MUST_STOP",
            "outdated_scenario_label": "TIMELY_NOTICE",
        }
    scenario = WEEK2_SCENARIOS[scenario_id]
    return {
        "successor_obligation": scenario["successor_obligation"],
        "stop_condition": scenario["stop_condition"],
        "outdated_scenario_label": scenario["outdated_scenario_label"],
    }


def _build_new(spec: tuple[str, str, int, int]) -> dict[str, Any]:
    proposal_id, scenario_id, seed, index = spec
    world = make_world(seed, index)
    auth = dict(INITIAL_AUTHORITY)
    world_after, auth_after, schedule = _mutate(scenario_id, world, auth)
    payload = legal_payload(world)
    successor_world = dict(world)
    successor_world["qty"] = world["successor_qty"]
    successor_world["generation"] = 1
    labels = _labels(scenario_id)
    return {
        "proposal_id": proposal_id,
        "scenario_id": scenario_id,
        "task_id": f"ful-w3-{proposal_id}",
        "payload_path": f"proposals/{proposal_id}.bin",
        "payload_sha256": sha256_bytes(payload),
        "payload_bytes": payload,
        "successor_payload_path": f"proposals/{proposal_id}.successor.bin",
        "successor_payload_sha256": sha256_bytes(legal_payload(successor_world)),
        "successor_payload_bytes": legal_payload(successor_world),
        "task_semantic_version": PREDICATE_VERSION,
        "world_before": world,
        "world_before_hash": sha256_obj(world),
        "world_after": world_after,
        "world_after_hash": sha256_obj(world_after),
        "authority_token": auth["token"],
        "authority_epoch": auth["epoch"],
        "authority_initial": auth,
        "authority_initial_hash": sha256_obj(auth),
        "authority_after": auth_after,
        "authority_after_hash": sha256_obj(auth_after),
        "successor_obligation": labels["successor_obligation"],
        "stop_condition": labels["stop_condition"],
        "outdated_scenario_label": labels["outdated_scenario_label"],
        "fault_schedule": schedule,
        "fault_schedule_hash": sha256_obj(schedule),
        "seed": seed,
        "timeout_ms": 2000,
        "budget_units": 10,
        "idempotency_key": f"effect:{proposal_id}",
        "outbox_event_id": f"obx:{proposal_id}",
        "applicable_paths": ["Direct", "Admission", "Claim", "Outbox"],
        "common_core": True,
        "mechanism_role": SCENARIO_ROLE[scenario_id],
        "scenario_family": SCENARIO_FAMILY[scenario_id],
        "source": "week3",
        "oracle_versions": dict(ORACLE_VERSIONS),
    }


def _write_once(path: Path, payload: bytes) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _expected_matrix(scenario_id: str) -> dict[str, dict[str, str]]:
    if scenario_id == "S13":
        publish = {
            "physical_effect_state": "CONFIRMED_EFFECT",
            "recovery_state": "NOT_APPLICABLE",
            "semantic_validity": "INVALID",
            "recovery_evidence_state": "NOT_APPLICABLE",
            "published_old_bytes": "true",
        }
        withheld = {
            "physical_effect_state": "ABSENT",
            "recovery_state": "NOT_RECOVERED",
            "semantic_validity": "UNKNOWN",
            "recovery_evidence_state": "UNKNOWN",
            "published_old_bytes": "false",
        }
        return {"Direct": publish, "Admission": publish, "Claim": withheld, "Outbox": withheld}
    if scenario_id in {"S14", "S16"}:
        publish = {
            "physical_effect_state": "CONFIRMED_EFFECT",
            "recovery_state": "NOT_APPLICABLE",
            "semantic_validity": "INVALID",
            "recovery_evidence_state": "NOT_APPLICABLE",
            "published_old_bytes": "true",
        }
        restored = {
            "physical_effect_state": "PRESENT_UNVERIFIED",
            "recovery_state": "RESTORED_BYTES",
            "semantic_validity": "INVALID",
            "recovery_evidence_state": "PUBLISH_SEALED",
            "published_old_bytes": "true",
        }
        return {"Direct": publish, "Admission": publish, "Claim": restored, "Outbox": restored}
    if scenario_id == "S15":
        confirmed = {
            "physical_effect_state": "CONFIRMED_EFFECT",
            "recovery_state": "NOT_APPLICABLE",
            "semantic_validity": "INVALID",
            "recovery_evidence_state": "NOT_APPLICABLE",
            "published_old_bytes": "true",
        }
        killed = dict(confirmed)
        killed["recovery_evidence_state"] = "CONFIRM_TARGET"
        return {"Direct": confirmed, "Admission": confirmed, "Claim": killed, "Outbox": killed}
    if scenario_id == "S17":
        old = {
            "physical_effect_state": "CONFIRMED_EFFECT",
            "recovery_state": "NOT_APPLICABLE",
            "semantic_validity": "INVALID",
            "successor_status": "FAILED",
            "published_old_bytes": "true",
            "authority_decision": "STALE_ACCEPTED",
        }
        successor = {
            "physical_effect_state": "CONFIRMED_EFFECT",
            "recovery_state": "NOT_APPLICABLE",
            "semantic_validity": "VALID",
            "successor_status": "COMPLETED_VALID",
            "published_old_bytes": "false",
            "authority_decision": "NO_STALE_ACCEPTANCE_RECORDED",
        }
        return {"Direct": old, "Admission": old, "Claim": successor, "Outbox": successor}
    stopped = {
        "physical_effect_state": "ABSENT",
        "recovery_state": "NOT_APPLICABLE",
        "semantic_validity": "UNKNOWN",
        "successor_status": "CORRECT_STOP",
        "published_old_bytes": "false",
        "authority_decision": "NO_STALE_ACCEPTANCE_RECORDED",
    }
    old = {
        "physical_effect_state": "CONFIRMED_EFFECT",
        "recovery_state": "NOT_APPLICABLE",
        "semantic_validity": "INVALID",
        "successor_status": "FAILED",
        "published_old_bytes": "true",
        "authority_decision": "STALE_ACCEPTED",
    }
    return {"Direct": old, "Admission": old, "Claim": stopped, "Outbox": stopped}


def _evidence_class(scenario_id: str) -> str:
    return {
        "S13": "UNKNOWN",
        "S14": "PUBLISH_SEALED",
        "S15": "CONFIRM_TARGET",
        "S16": "PUBLISH_SEALED",
        "S17": "LEGAL_SUCCESSOR",
        "S18": "CORRECT_STOP",
    }[scenario_id]


def _hypothesis(scenario_id: str) -> str:
    return {
        "S13": "密封前世界已变。目标若带着旧声明字节，物理上对得上声明，语义 oracle 对观察世界返回 INVALID。Claim 与 Outbox 在密封前被杀死时不发布。",
        "S14": "密封后、替换前世界已变。Claim 与 Outbox 由新进程按密封字节发布，记为 RESTORED_BYTES，不是 CONFIRMED_EFFECT。语义仍为 INVALID。",
        "S15": "替换已落盘、回执未落盘。新进程只确认目标摘要，物理状态是 CONFIRMED_EFFECT。语义对变化后的世界为 INVALID。",
        "S16": "回执缺失，只剩密封字节。核对不能重放回执，只能重新发布密封字节，记为 RESTORED_BYTES。语义为 INVALID。",
        "S17": "声明之后、物理发布之前权威通知到达。Claim 与 Outbox 冻结旧声明且不发布旧字节，并接受合法后继。Direct 与 Admission 仍发布旧字节。这是场景行为，不是新协议。",
        "S18": "撤销通知在发布前到达。Claim 与 Outbox 冻结旧声明并不发布，正确停止。正确停止不是后继失败。Direct 与 Admission 仍发布旧字节。",
    }[scenario_id]


def write_manifests(records: list[dict[str, Any]]) -> None:
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record["scenario_id"] in SEPARATION_SCENARIOS or record["scenario_id"] in FREEZE_SCENARIOS:
            by_scenario.setdefault(record["scenario_id"], []).append(record)
    manifest_dir = WEEK3 / "scenario_manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    for scenario_id, rows in sorted(by_scenario.items()):
        path = manifest_dir / f"{scenario_id}.yaml"
        if path.exists():
            continue
        fixture_id = FIXTURE_PROPOSAL[scenario_id]
        fixture = next(row for row in rows if row["proposal_id"] == fixture_id)
        notice = "ABSENT" if scenario_id in SEPARATION_SCENARIOS else fixture["fault_schedule"]["points"][0]["actions"][0]["op"]
        body = {
            "scenario_id": scenario_id,
            "family": SCENARIO_FAMILY[scenario_id],
            "mechanism_role": SCENARIO_ROLE[scenario_id],
            "research_hypothesis": _hypothesis(scenario_id),
            "fixture_proposal_id": fixture_id,
            "bound_proposals": [row["proposal_id"] for row in rows],
            "initial_world": fixture["world_before"],
            "observed_world": fixture["world_after"],
            "mutation": fixture["fault_schedule"]["points"][0]["actions"][0]["op"],
            "fault": {
                "checkpoint": SEPARATION_CHECKPOINT.get(scenario_id, "after_claim_before_publish"),
                "schedule_hash": fixture["fault_schedule_hash"],
            },
            "notice_schedule": {
                "notice": notice,
                "arrives": "after_claim_before_publish" if scenario_id in FREEZE_SCENARIOS else "ABSENT",
            },
            "effect_key": "effect:{proposal_id}",
            "semantic_oracle": {
                "name": "semantics_oracle",
                "version": ORACLE_VERSIONS["semantics_oracle"],
                "scoring_world": "observed_world",
                "predicate_version": PREDICATE_VERSION,
            },
            "successor_requirement": {
                "required": scenario_id in FREEZE_SCENARIOS,
                "status_space": "COMPLETED_VALID,CORRECT_STOP,FAILED",
            },
            "expected_evidence_class": _evidence_class(scenario_id),
            "oracle_versions": dict(ORACLE_VERSIONS),
            "predicate_version": PREDICATE_VERSION,
            "world_state_id": {
                "initial": fixture["world_before_hash"],
                "observed": fixture["world_after_hash"],
            },
            "expected_matrix": _expected_matrix(scenario_id),
            "applicable_paths": ["Direct", "Admission", "Claim", "Outbox"],
        }
        path.write_text(dump(body) + "\n", encoding="utf-8")


def ensure_new_proposals() -> list[dict[str, Any]]:
    built = [_build_new(spec) for spec in NEW_SPECS]
    proposal_dir = WEEK3 / "proposals"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    stored = []
    for record in built:
        payload_path = WEEK3 / record["payload_path"]
        successor_path = WEEK3 / record["successor_payload_path"]
        _write_once(payload_path, record.pop("payload_bytes"))
        _write_once(successor_path, record.pop("successor_payload_bytes"))
        on_disk = payload_path.read_bytes()
        record["payload_sha256"] = sha256_bytes(on_disk)
        record["successor_payload_sha256"] = sha256_bytes(successor_path.read_bytes())
        stored.append(record)
    registry = WEEK3 / "proposal_registry.jsonl"
    if not registry.exists():
        registry.write_text(
            "".join(json.dumps({k: v for k, v in row.items()}, ensure_ascii=False, sort_keys=True) + "\n" for row in stored),
            encoding="utf-8",
        )
    write_manifests(stored)
    return stored


def _annotate_week2(row: dict[str, Any]) -> dict[str, Any]:
    scenario_id = row["scenario_id"]
    annotated = dict(row)
    annotated["source"] = "week2"
    annotated["mechanism_role"] = SCENARIO_ROLE[scenario_id]
    annotated["scenario_family"] = SCENARIO_FAMILY[scenario_id]
    annotated["task_semantic_version"] = row["task_semantic_version"]
    return annotated


def load_records() -> list[dict[str, Any]]:
    week2_rows = [_annotate_week2(row) for row in load_week2_registry()]
    new_rows = ensure_new_proposals()
    by_id = {row["proposal_id"]: row for row in week2_rows}
    for row in new_rows:
        by_id[row["proposal_id"]] = row
    order = [f"p{index:02d}" for index in range(1, 28)]
    return [by_id[proposal_id] for proposal_id in order]
