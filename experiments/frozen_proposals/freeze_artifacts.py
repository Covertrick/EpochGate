"""Freeze scenario manifests and proposal bytes once.

Proposal payload bytes are produced here and only here. Path adapters load the
files; they do not call the predicate to rebuild a payload.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from frozen_proposals.hashing import canonical_bytes, environment_hash, git_head, implementation_hash, sha256_bytes, sha256_obj
from frozen_proposals.oracles import ORACLE_VERSIONS
from frozen_proposals.predicate import PREDICATE_VERSION, legal_payload
from frozen_proposals.receiver_adapter import RECEIVER_IMPL, RECEIVER_VERSION
from frozen_proposals.faults import simulate

PATH_NAMES = ["Direct", "Admission", "Claim", "Outbox"]
TIMEOUT_MS = 2000
BUDGET_UNITS = 10
INITIAL_AUTHORITY = {"token": 1, "epoch": 1, "eligible": True}

CLUSTERS = [
    ("p01", "S01", 1101),
    ("p02", "S01", 1102),
    ("p03", "S02", 1103),
    ("p04", "S03", 1104),
    ("p05", "S03", 1105),
    ("p06", "S04", 1106),
    ("p07", "S05", 1107),
    ("p08", "S06", 1108),
    ("p09", "S07", 1109),
    ("p10", "S08", 1110),
    ("p11", "S08", 1111),
    ("p12", "S09", 1112),
    ("p13", "S10", 1113),
    ("p14", "S11", 1114),
    ("p15", "S12", 1115),
]
SMOKE_IDS = ["p01", "p04", "p10"]

SCENARIOS: dict[str, dict[str, Any]] = {
    "S01": {
        "research_hypothesis": "效果发生前权威已撤销。规范结果是不产生物理效果。",
        "fault_injection_point": "before_effect",
        "fault_transition": "revoke",
        "expected_authority_state": "revoked",
        "expected_physical_effect_state": "ABSENT",
        "expected_evidence_state": "SUFFICIENT",
        "expected_semantic_state": "NO_OBSERVED_EFFECT",
        "successor_obligation": "NOT_REQUIRED",
        "expected_stop_condition": "CORRECT_STOP",
        "stop_condition": "MUST_STOP",
        "oracle_fixture": "revoke_before_gate",
        "applicable_paths": PATH_NAMES,
        "common_core": True,
        "mechanism_role": "MAIN_COMPARISON",
        "outdated_scenario_label": "TIMELY_NOTICE",
        "seed": 1101,
    },
    "S02": {
        "research_hypothesis": "效果发生前权威被取代。旧提案字节不应落地。本场景不把后继完成当作义务。",
        "fault_injection_point": "before_effect",
        "fault_transition": "supersede",
        "expected_authority_state": "superseded",
        "expected_physical_effect_state": "ABSENT",
        "expected_evidence_state": "SUFFICIENT",
        "expected_semantic_state": "NO_OBSERVED_EFFECT",
        "successor_obligation": "NOT_REQUIRED",
        "expected_stop_condition": "CORRECT_STOP",
        "stop_condition": "MUST_STOP",
        "oracle_fixture": "supersede_old_effect",
        "applicable_paths": PATH_NAMES,
        "common_core": True,
        "mechanism_role": "MAIN_COMPARISON",
        "outdated_scenario_label": "TIMELY_NOTICE",
        "seed": 1103,
    },
    "S03": {
        "research_hypothesis": "世界已变但权威通知缺失。旧字节相对新世界失效，token 并未过期。过时不是过期权威。",
        "fault_injection_point": "before_effect",
        "fault_transition": "drift",
        "expected_authority_state": "unchanged_token",
        "expected_physical_effect_state": "NOT_LEGAL",
        "expected_evidence_state": "SUFFICIENT",
        "expected_semantic_state": "INVALID",
        "successor_obligation": "NOT_REQUIRED",
        "expected_stop_condition": "NOT_A_STOP_SCENARIO",
        "stop_condition": "NOT_A_STOP_SCENARIO",
        "oracle_fixture": "missing_notice_drift",
        "applicable_paths": PATH_NAMES,
        "common_core": True,
        "mechanism_role": "MAIN_COMPARISON",
        "outdated_scenario_label": "NOTICE_MISSING_OR_DELAYED",
        "seed": 1104,
    },
    "S04": {
        "research_hypothesis": "纪元前进但合法载荷未变。过期凭证即使字节仍合法也不应被接纳。",
        "fault_injection_point": "before_effect",
        "fault_transition": "stale_epoch",
        "expected_authority_state": "stale_epoch",
        "expected_physical_effect_state": "ABSENT",
        "expected_evidence_state": "SUFFICIENT",
        "expected_semantic_state": "NO_OBSERVED_EFFECT",
        "successor_obligation": "NOT_REQUIRED",
        "expected_stop_condition": "NOT_A_STOP_SCENARIO",
        "stop_condition": "NOT_A_STOP_SCENARIO",
        "oracle_fixture": "stale_but_valid",
        "applicable_paths": PATH_NAMES,
        "common_core": True,
        "mechanism_role": "MAIN_COMPARISON",
        "outdated_scenario_label": "UNKNOWN",
        "seed": 1106,
    },
    "S05": {
        "research_hypothesis": "提案对当前世界适用，但接收端失败。终态失败不等于权威过期，也不等于语义无效。",
        "fault_injection_point": "before_send",
        "fault_transition": "receiver_fail",
        "expected_authority_state": "unchanged_token",
        "expected_physical_effect_state": "ABSENT",
        "expected_evidence_state": "SUFFICIENT",
        "expected_semantic_state": "NO_OBSERVED_EFFECT",
        "successor_obligation": "NOT_REQUIRED",
        "expected_stop_condition": "NOT_A_STOP_SCENARIO",
        "stop_condition": "NOT_A_STOP_SCENARIO",
        "oracle_fixture": "applicable_but_fail",
        "applicable_paths": PATH_NAMES,
        "common_core": True,
        "mechanism_role": "MAIN_COMPARISON",
        "outdated_scenario_label": "UNKNOWN",
        "seed": 1107,
    },
    "S06": {
        "research_hypothesis": "准入决定之后、发送之前发生并发编辑。四条路径都不会在发送点重新核对权威。",
        "fault_injection_point": "after_admit_before_send",
        "fault_transition": "concurrent_edit",
        "expected_authority_state": "edited_epoch",
        "expected_physical_effect_state": "ABSENT",
        "expected_evidence_state": "SUFFICIENT",
        "expected_semantic_state": "NO_OBSERVED_EFFECT",
        "successor_obligation": "NOT_REQUIRED",
        "expected_stop_condition": "CORRECT_STOP",
        "stop_condition": "MUST_STOP",
        "oracle_fixture": "concurrent_edit_window",
        "applicable_paths": PATH_NAMES,
        "common_core": True,
        "mechanism_role": "MAIN_COMPARISON",
        "outdated_scenario_label": "UNKNOWN",
        "seed": 1108,
    },
    "S07": {
        "research_hypothesis": "同一幂等键重复投递。接收端幂等不应产生第二次逻辑效果。",
        "fault_injection_point": "before_send",
        "fault_transition": "duplicate_delivery",
        "expected_authority_state": "unchanged_token",
        "expected_physical_effect_state": "CONFIRMED_EFFECT",
        "expected_evidence_state": "SUFFICIENT",
        "expected_semantic_state": "VALID",
        "successor_obligation": "NOT_REQUIRED",
        "expected_stop_condition": "NOT_A_STOP_SCENARIO",
        "stop_condition": "NOT_A_STOP_SCENARIO",
        "oracle_fixture": "duplicate_delivery",
        "applicable_paths": PATH_NAMES,
        "common_core": True,
        "mechanism_role": "MAIN_COMPARISON",
        "outdated_scenario_label": "UNKNOWN",
        "seed": 1109,
    },
    "S08": {
        "research_hypothesis": "发送已在接收端生效、本地尚未记下回执时中断。这是进程内故障注入，不是掉电，也不是 process.kill。",
        "fault_injection_point": "after_send_before_receipt",
        "fault_transition": "crash",
        "expected_authority_state": "unchanged_token",
        "expected_physical_effect_state": "CONFIRMED_EFFECT",
        "expected_evidence_state": "SUFFICIENT",
        "expected_semantic_state": "VALID",
        "successor_obligation": "NOT_REQUIRED",
        "expected_stop_condition": "NOT_A_STOP_SCENARIO",
        "stop_condition": "NOT_A_STOP_SCENARIO",
        "oracle_fixture": "effect_before_receipt",
        "applicable_paths": PATH_NAMES,
        "common_core": True,
        "mechanism_role": "MAIN_COMPARISON",
        "outdated_scenario_label": "UNKNOWN",
        "seed": 1110,
    },
    "S09": {
        "research_hypothesis": "持久意图提交之后、发送之前中断。只有会把意图写入同一数据库的机制才能表达该诊断。Direct 与 Admission 排除出四路径主比较。",
        "fault_injection_point": "after_commit_before_send",
        "fault_transition": "crash",
        "expected_authority_state": "unchanged_token",
        "expected_physical_effect_state": "CONFIRMED_EFFECT",
        "expected_evidence_state": "SUFFICIENT",
        "expected_semantic_state": "VALID",
        "successor_obligation": "NOT_REQUIRED",
        "expected_stop_condition": "NOT_A_STOP_SCENARIO",
        "stop_condition": "NOT_A_STOP_SCENARIO",
        "oracle_fixture": "durable_intent_before_send",
        "applicable_paths": ["Claim", "Outbox"],
        "common_core": False,
        "mechanism_role": "MECHANISM_SPECIFIC_DIAGNOSTIC",
        "outdated_scenario_label": "UNKNOWN",
        "seed": 1112,
    },
    "S10": {
        "research_hypothesis": "同一幂等键出现另一份载荷。证据状态保持 CONFLICT，不能把它排成 UNKNOWN 与 VALID 之间的等级。",
        "fault_injection_point": "before_send",
        "fault_transition": "conflict_delivery",
        "expected_authority_state": "unchanged_token",
        "expected_physical_effect_state": "CONFIRMED_EFFECT",
        "expected_evidence_state": "CONFLICT",
        "expected_semantic_state": "VALID",
        "successor_obligation": "NOT_REQUIRED",
        "expected_stop_condition": "NOT_A_STOP_SCENARIO",
        "stop_condition": "NOT_A_STOP_SCENARIO",
        "oracle_fixture": "payload_key_conflict",
        "applicable_paths": PATH_NAMES,
        "common_core": True,
        "mechanism_role": "MAIN_COMPARISON",
        "outdated_scenario_label": "UNKNOWN",
        "seed": 1113,
    },
    "S11": {
        "research_hypothesis": "撤销之后不应继续产生效果，也不存在后继义务。正确停止不是后继失败。",
        "fault_injection_point": "before_effect",
        "fault_transition": "revoke",
        "expected_authority_state": "revoked",
        "expected_physical_effect_state": "ABSENT",
        "expected_evidence_state": "SUFFICIENT",
        "expected_semantic_state": "NO_OBSERVED_EFFECT",
        "successor_obligation": "NOT_REQUIRED",
        "expected_stop_condition": "CORRECT_STOP",
        "stop_condition": "MUST_STOP",
        "oracle_fixture": "correct_stop_after_revocation",
        "applicable_paths": PATH_NAMES,
        "common_core": True,
        "mechanism_role": "MAIN_COMPARISON",
        "outdated_scenario_label": "TIMELY_NOTICE",
        "seed": 1114,
    },
    "S12": {
        "research_hypothesis": "取代之后后继义务为 REQUIRED。本基准不实现 Successor-Aware Reconciliation，路径不会另写后继字节。",
        "fault_injection_point": "before_effect",
        "fault_transition": "supersede",
        "expected_authority_state": "superseded",
        "expected_physical_effect_state": "SUCCESSOR_BYTES",
        "expected_evidence_state": "SUFFICIENT",
        "expected_semantic_state": "VALID_SUCCESSOR",
        "successor_obligation": "REQUIRED",
        "expected_stop_condition": "NOT_CORRECT_STOP",
        "stop_condition": "NOT_A_STOP_SCENARIO",
        "oracle_fixture": "required_successor",
        "applicable_paths": PATH_NAMES,
        "common_core": True,
        "mechanism_role": "MAIN_COMPARISON",
        "outdated_scenario_label": "TIMELY_NOTICE",
        "seed": 1115,
    },
}


def make_world(seed: int, index: int) -> dict[str, Any]:
    qty = 1 + (index % 4)
    return {
        "order_id": f"ORD-{seed}",
        "sku": ["A", "B", "C"][index % 3],
        "qty": qty,
        "dest": ["north", "south", "east"][index % 3],
        "generation": 0,
        "effect_forbidden": False,
        "successor_qty": qty + 7,
        "drift_qty": qty + 3,
    }


def schedule_for(scenario: dict[str, Any], proposal_id: str) -> dict[str, Any]:
    action: dict[str, Any] = {"op": scenario["fault_transition"]}
    if scenario["fault_transition"] == "conflict_delivery":
        action["payload_hex"] = f"CONFLICT|{proposal_id}".encode("utf-8").hex()
    return {"points": [{"point": scenario["fault_injection_point"], "actions": [action]}]}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError("git repository not found")


def freeze(root: Path, repo: Path | None = None) -> list[dict[str, Any]]:
    repo = repo or repo_root()
    code_commit = git_head(repo)
    env_hash = environment_hash()
    impl_hash = implementation_hash(root)
    manifest_dir = root / "scenario_manifest"
    proposal_dir = root / "proposals"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for scenario_id, scenario in SCENARIOS.items():
        body = {
            "scenario_id": scenario_id,
            "research_hypothesis": scenario["research_hypothesis"],
            "initial_state": {
                "authority": INITIAL_AUTHORITY,
                "world_shape": "order_id, sku, qty, dest, generation=0, effect_forbidden=false",
                "concrete_world": "proposal_registry.jsonl world_before",
            },
            "fault_injection_point": scenario["fault_injection_point"],
            "fault_transition": scenario["fault_transition"],
            "expected_authority_state": scenario["expected_authority_state"],
            "expected_physical_effect_state": scenario["expected_physical_effect_state"],
            "expected_evidence_state": scenario["expected_evidence_state"],
            "expected_semantic_state": scenario["expected_semantic_state"],
            "successor_obligation": scenario["successor_obligation"],
            "expected_stop_condition": scenario["expected_stop_condition"],
            "oracle_fixture": scenario["oracle_fixture"],
            "applicable_paths": scenario["applicable_paths"],
            "common_core": scenario["common_core"],
            "mechanism_role": scenario["mechanism_role"],
            "seed": scenario["seed"],
        }
        _write_json(manifest_dir / f"{scenario_id}.json", body)
        index.append(
            {
                "scenario_id": scenario_id,
                "common_core": scenario["common_core"],
                "mechanism_role": scenario["mechanism_role"],
                "file": f"scenario_manifest/{scenario_id}.json",
            }
        )
    _write_json(manifest_dir / "index.json", {"scenarios": index})

    records: list[dict[str, Any]] = []
    for index_number, (proposal_id, scenario_id, seed) in enumerate(CLUSTERS):
        scenario = SCENARIOS[scenario_id]
        world = make_world(seed, index_number)
        payload = legal_payload(world)
        successor_world = dict(world)
        successor_world["qty"] = world["successor_qty"]
        successor_world["generation"] = 1
        successor_payload = legal_payload(successor_world)
        schedule = schedule_for(scenario, proposal_id)
        world_after, auth_after = simulate(world, dict(INITIAL_AUTHORITY), schedule)
        payload_path = proposal_dir / f"{proposal_id}.bin"
        successor_path = proposal_dir / f"{proposal_id}.successor.bin"
        payload_path.write_bytes(payload)
        successor_path.write_bytes(successor_payload)
        record = {
            "proposal_id": proposal_id,
            "scenario_id": scenario_id,
            "task_id": f"ful-w2-{proposal_id}",
            "payload_path": str(payload_path.relative_to(root)).replace("\\", "/"),
            "payload_sha256": sha256_bytes(payload),
            "successor_payload_path": str(successor_path.relative_to(root)).replace("\\", "/"),
            "successor_payload_sha256": sha256_bytes(successor_payload),
            "task_semantic_version": PREDICATE_VERSION,
            "world_before": world,
            "world_before_hash": sha256_obj(world),
            "world_after": world_after,
            "world_after_hash": sha256_obj(world_after),
            "authority_token": INITIAL_AUTHORITY["token"],
            "authority_epoch": INITIAL_AUTHORITY["epoch"],
            "authority_initial": INITIAL_AUTHORITY,
            "authority_initial_hash": sha256_obj(INITIAL_AUTHORITY),
            "authority_after": auth_after,
            "authority_after_hash": sha256_obj(auth_after),
            "successor_obligation": scenario["successor_obligation"],
            "stop_condition": scenario["stop_condition"],
            "outdated_scenario_label": scenario["outdated_scenario_label"],
            "fault_schedule": schedule,
            "fault_schedule_hash": sha256_obj(schedule),
            "seed": seed,
            "timeout_ms": TIMEOUT_MS,
            "budget_units": BUDGET_UNITS,
            "oracle_versions": ORACLE_VERSIONS,
            "code_commit": code_commit,
            "environment_hash": env_hash,
            "implementation_hash": impl_hash,
            "idempotency_key": f"effect:{proposal_id}",
            "outbox_event_id": f"obx:{proposal_id}",
            "receiver_impl": RECEIVER_IMPL,
            "receiver_version": RECEIVER_VERSION,
            "common_core": scenario["common_core"],
            "mechanism_role": scenario["mechanism_role"],
            "applicable_paths": scenario["applicable_paths"],
            "oracle_fixture": scenario["oracle_fixture"],
        }
        records.append(record)
    registry = root / "proposal_registry.jsonl"
    registry.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records), encoding="utf-8")
    manifest_hash = sha256_bytes(canonical_bytes(records))
    (root / "freeze_manifest.sha256").write_text(manifest_hash + "\n", encoding="utf-8")
    return records
