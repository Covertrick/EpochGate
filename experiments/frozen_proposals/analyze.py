"""Build the Week 2 joint table, validation report, and Chinese report.

Week 2 rows are not copied into the Week 1 archived joint table.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from frozen_proposals.baselines.outbox import DURABILITY_CLAIM
from frozen_proposals.fairness import check_fairness
from frozen_proposals.frozen_proposal_runner import load_jsonl
from frozen_proposals.hashing import environment_record

ARCHIVED_SHA256 = "d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda"
WEEK1_SCHEMA = Path("experiments") / "historical_audit" / "outcome.schema.json"
WEEK1_CSV = Path("experiments") / "historical_audit" / "archived_joint.csv"

CSV_FIELDS = [
    "source_run_id",
    "run_id",
    "proposal_id",
    "path",
    "scenario_id",
    "cohort",
    "runner_status",
    "terminal_outcome",
    "authority_decision",
    "physical_effect_state",
    "evidence_state",
    "semantic_validity",
    "recovery_state",
    "successor_obligation",
    "successor_status",
    "stop_correctness",
    "week1_successor_obligation",
    "week1_successor_status",
    "outdated_scenario_label",
    "adjudication",
    "adjudication_basis",
    "joint_observed",
    "unobserved_reason",
    "terminal_adjudicated",
    "effect_history_adjudicated",
    "duplicate_effect",
    "latency_ms",
    "local_overhead_ms",
    "payload_sha256",
    "common_core",
    "mechanism_specific",
    "mechanism_role",
    "journal_mode",
    "synchronous",
    "fault_fired",
    "crash_point",
    "in_main_comparison",
]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _is_main(row: dict[str, Any]) -> bool:
    return (
        row.get("cohort") == "PILOT_FIRST_BATCH"
        and row.get("common_core") is True
        and row.get("mechanism_specific") is not True
        and row.get("runner_status") != "ABANDONED"
    )


def _pilot(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("cohort") == "PILOT_FIRST_BATCH" and row.get("runner_status") != "ABANDONED"]


def _count(rows: list[dict[str, Any]], field: str, value: str) -> int:
    return sum(1 for row in rows if row.get(field) == value)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _scenario_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("scenario_id"))].append(row)
    breakdown = []
    for scenario_id in sorted(grouped):
        group = grouped[scenario_id]
        breakdown.append(
            {
                "scenario_id": scenario_id,
                "cells": len(group),
                "proposals": len({row.get("proposal_id") for row in group}),
                "stale_accepted": _count(group, "authority_decision", "STALE_ACCEPTED"),
                "not_legal": _count(group, "physical_effect_state", "NOT_LEGAL"),
                "confirmed_effect": _count(group, "physical_effect_state", "CONFIRMED_EFFECT"),
                "absent": _count(group, "physical_effect_state", "ABSENT"),
                "terminal_match": _count(group, "terminal_outcome", "MATCH"),
                "semantic_invalid": _count(group, "semantic_validity", "INVALID"),
                "evidence_conflict": _count(group, "evidence_state", "CONFLICT"),
                "correct_stop": _count(group, "stop_correctness", "CORRECT_STOP"),
                "successor_failed": _count(group, "successor_status", "FAILED"),
                "successor_completed": _count(group, "successor_status", "COMPLETED"),
                "crashed": _count(group, "runner_status", "CRASHED"),
                "unknown_authority": _count(group, "authority_decision", "UNKNOWN"),
                "joint_observed": sum(1 for row in group if row.get("joint_observed") is True),
            }
        )
    return breakdown


def _criterion(index: int, name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": index, "name": name, "pass": passed, "detail": detail}


def _uniform(result: dict[str, Any], field: str) -> bool:
    return not any(item.get("field") == field for item in result["mismatches"])


def analyze(
    root: Path,
    repo: Path,
    *,
    tests_passed: bool,
    pytest_output: str,
    batch_ran: bool,
) -> dict[str, Any]:
    manifests = load_jsonl(root / "run_manifest.jsonl")
    events = load_jsonl(root / "run_events.jsonl") if (root / "run_events.jsonl").exists() else []
    smoke_rows = [row for row in manifests if row.get("cohort") == "SMOKE" and row.get("runner_status") != "ABANDONED"]
    pilot_rows = _pilot(manifests)
    main_rows = [row for row in pilot_rows if _is_main(row)]
    smoke_fair = check_fairness(smoke_rows)
    pilot_fair = check_fairness(pilot_rows) if pilot_rows else {"pass": False, "mismatches": [{"field": "missing_pilot"}]}
    fairness_pass = bool(smoke_fair["pass"] and (pilot_fair["pass"] if batch_ran else True))

    hash_rows = [row for row in pilot_rows if row.get("payload_sha256_after")]
    hashes_stable = all(row.get("payload_sha256") == row.get("payload_sha256_after") for row in hash_rows) and _uniform(pilot_fair if batch_ran else smoke_fair, "payload_sha256")
    invalid_common = [row for row in main_rows if row.get("runner_status") == "INVALID"]

    started = {str(event.get("run_id")) for event in events if event.get("event") == "RUN_STARTED" and event.get("run_id")}
    closed = {str(event.get("run_id")) for event in events if event.get("event") in {"RUN_TERMINAL", "RUN_ABANDONED"} and event.get("run_id")}
    retained = started <= closed and started != set()
    run_ids = [str(row.get("run_id")) for row in manifests if row.get("run_id")]
    unique_ids = len(run_ids) == len(set(run_ids)) and len(run_ids) > 0
    provenance_rows = [row for row in manifests if row.get("runner_status") != "ABANDONED"]
    provenance_ok = bool(provenance_rows) and all(row.get("provenance_ref") for row in provenance_rows)

    matrix_path = root / "outbox_crash_matrix.csv"
    matrix_rows = []
    if matrix_path.exists():
        with matrix_path.open(encoding="utf-8", newline="") as handle:
            matrix_rows = list(csv.DictReader(handle))
    matrix_points = {row.get("fault_point") for row in matrix_rows}
    matrix_ok = {
        "before_db_transaction",
        "inside_transaction",
        "after_commit_before_send",
        "after_send_before_receipt",
        "after_receipt_before_ack",
        "after_local_ack",
    } <= matrix_points and all(row.get("process_kill") == "False" for row in matrix_rows) and all(row.get("power_loss_claim") == "False" for row in matrix_rows)
    duplicate_ok = any(row.get("fault_point") == "duplicate_relay_after_ack" and row.get("logical_effect_count") == "1" for row in matrix_rows)

    active_fair = pilot_fair if batch_ran else smoke_fair
    criteria = [
        _criterion(1, "proposal bytes/hash identical across the four paths", hashes_stable and not invalid_common, "payload_sha256 compared within each proposal; file hash rechecked after each run"),
        _criterion(2, "task semantics identical", _uniform(active_fair, "task_semantic_version"), "task_semantic_version"),
        _criterion(3, "initial world state identical", _uniform(active_fair, "world_before_hash"), "world_before_hash"),
        _criterion(4, "authority starting state identical", _uniform(active_fair, "authority_initial_hash"), "authority_initial_hash"),
        _criterion(5, "fault schedule identical", _uniform(active_fair, "fault_schedule_hash"), "fault_schedule_hash"),
        _criterion(6, "receiver identical", _uniform(active_fair, "receiver_impl") and _uniform(active_fair, "receiver_version"), "receiver_impl and receiver_version"),
        _criterion(7, "oracle versions identical", _uniform(active_fair, "oracle_versions_canonical"), "oracle_versions_canonical"),
        _criterion(8, "timeout/budget identical", _uniform(active_fair, "timeout_ms") and _uniform(active_fair, "budget_units"), "timeout_ms and budget_units"),
        _criterion(9, "every cell has a unique run_id", unique_ids, f"manifest rows {len(run_ids)}, unique {len(set(run_ids))}"),
        _criterion(10, "receiver/effect provenance exists", provenance_ok, "provenance_ref on every non-abandoned manifest row"),
        _criterion(11, "failures and UNKNOWN are retained", retained, f"RUN_STARTED {len(started)}, closed {len(closed & started)}"),
        _criterion(12, "outbox atomicity tests pass", bool(tests_passed and matrix_ok), "pytest frozen_proposals/oracle_test_suite/test_outbox_atomicity.py and outbox_crash_matrix.csv"),
        _criterion(13, "receiver idempotency tests pass", bool(tests_passed and duplicate_ok), "pytest frozen_proposals/oracle_test_suite/test_receiver_idempotency.py; duplicate relay logical_effect_count 1"),
        _criterion(14, "oracle golden tests pass", bool(tests_passed), "pytest frozen_proposals/oracle_test_suite/test_golden.py"),
        _criterion(15, "oracle mutation tests pass", bool(tests_passed), "pytest frozen_proposals/oracle_test_suite/test_mutation.py"),
        _criterion(16, "fairness validator passes", fairness_pass and smoke_fair["pass"], "smoke and pilot input equality"),
    ]

    joint = [row for row in main_rows if row.get("joint_observed") is True]
    efe_num = _count(joint, "adjudication", "CONFIRMED_FAILURE")
    efe_den = len(joint)
    matches = [row for row in main_rows if row.get("terminal_outcome") == "MATCH"]
    success = [row for row in joint if row.get("terminal_outcome") == "MATCH" and row.get("adjudication") != "UNRESOLVED"]
    success_fail = [row for row in success if row.get("adjudication") == "CONFIRMED_FAILURE"]
    match_unadjudicable = [row for row in matches if row not in success]
    observed_effects = [row for row in main_rows if row.get("semantic_validity") in {"VALID", "INVALID"}]
    semantic_invalid = [row for row in observed_effects if row.get("semantic_validity") == "INVALID"]
    must_stop = [row for row in main_rows if row.get("stop_condition") == "MUST_STOP"]
    correct_stops = [row for row in must_stop if row.get("stop_correctness") == "CORRECT_STOP"]
    required = [row for row in main_rows if row.get("scenario_successor_obligation") == "REQUIRED"]
    successor_completed = [row for row in required if row.get("successor_status") == "COMPLETED"]

    def unknown_count(rows: list[dict[str, Any]]) -> int:
        fields = ("terminal_outcome", "authority_decision", "physical_effect_state", "evidence_state", "semantic_validity", "successor_status")
        return sum(1 for row in rows if any(row.get(field) == "UNKNOWN" for field in fields))

    limitations = {
        "mechanism_specific_scenarios": sorted({row.get("scenario_id") for row in pilot_rows if row.get("mechanism_specific") is True}),
        "crashed_cells": _count(main_rows, "runner_status", "CRASHED"),
        "not_applicable_cells": _count(pilot_rows, "runner_status", "NOT_APPLICABLE"),
        "unknown_cells_pilot": unknown_count(pilot_rows),
        "unknown_cells_main": unknown_count(main_rows),
        "invalid_common_cells": len(invalid_common),
        "main_cells": len(main_rows),
        "joint_main_cells": len(joint),
    }
    coverage_incomplete = (len(joint) != len(main_rows)) or (len(match_unadjudicable) > 0)
    has_limitations = bool(
        limitations["mechanism_specific_scenarios"]
        or limitations["crashed_cells"]
        or limitations["not_applicable_cells"]
        or limitations["unknown_cells_pilot"]
        or limitations["invalid_common_cells"]
        or coverage_incomplete
    )
    all_pass = all(item["pass"] for item in criteria)
    if (not tests_passed) or (not smoke_fair["pass"]) or invalid_common or (not hashes_stable):
        decision = "NO_GO_FORMAL_DATA"
    elif not batch_ran:
        decision = "NO_GO_FORMAL_DATA"
    elif all_pass and not has_limitations:
        decision = "GO"
    elif smoke_fair["pass"] and batch_ran and main_rows:
        decision = "CONDITIONAL_GO"
    else:
        decision = "NO_GO_FORMAL_DATA"

    if decision == "GO":
        reason = "十六条准入标准都通过，第一批是在冻结提案下收集的，而且主比较没有机制专用场景、CRASHED 或 UNKNOWN。"
    elif decision == "CONDITIONAL_GO":
        reason = "冒烟公平性通过，主比较子集可以对照；S09 是机制专用诊断，主比较里仍有 CRASHED 或未覆盖单元格，不外推、不填补。"
    else:
        reason = "公平性、提案字节或 oracle 的独立裁决没有同时成立，不能把这些运行当成正式四路径比较。"

    archived = repo / WEEK1_CSV
    archived_hash = _sha256_file(archived) if archived.exists() else None
    schema_path = repo / WEEK1_SCHEMA
    schema = json.loads(schema_path.read_text(encoding="utf-8")) if schema_path.exists() else {}
    latency_by_path = {
        path: _mean([float(row["latency_ms"]) for row in main_rows if row.get("path") == path and row.get("latency_ms") is not None])
        for path in ("Direct", "Admission", "Claim", "Outbox")
    }
    overhead_by_path = {
        path: _mean([float(row["local_overhead_ms"]) for row in main_rows if row.get("path") == path and row.get("local_overhead_ms") is not None])
        for path in ("Direct", "Admission", "Claim", "Outbox")
    }
    pilot_proposals = sorted({str(row.get("proposal_id")) for row in pilot_rows})
    main_proposals = sorted({str(row.get("proposal_id")) for row in main_rows})
    stats: dict[str, Any] = {
        "decision": decision,
        "decision_reason": reason,
        "criteria": criteria,
        "smoke": {"pass": smoke_fair["pass"], "mismatches": smoke_fair["mismatches"], "runs": len(smoke_rows), "label": "SMOKE"},
        "pilot": {
            "ran": batch_ran,
            "label": "PILOT_FIRST_BATCH",
            "proposals": len(pilot_proposals),
            "proposal_ids": pilot_proposals,
            "cells": len(pilot_rows),
            "run_ids": len({row.get("run_id") for row in pilot_rows}),
            "main_proposals": len(main_proposals),
            "main_cells": len(main_rows),
        },
        "counts": {
            "smoke_runs": len(smoke_rows),
            "pilot_proposals": len(pilot_proposals),
            "pilot_cells": len(pilot_rows),
            "pilot_run_ids": len({row.get("run_id") for row in pilot_rows}),
            "all_manifest_rows": len(manifests),
            "unique_run_ids": len(set(run_ids)),
            "abandoned": _count(manifests, "runner_status", "ABANDONED"),
            "unknown_pilot": unknown_count(pilot_rows),
            "unknown_main": unknown_count(main_rows),
            "conflict_pilot": _count(pilot_rows, "evidence_state", "CONFLICT"),
            "conflict_main": _count(main_rows, "evidence_state", "CONFLICT"),
            "mechanism_specific_cells": sum(1 for row in pilot_rows if row.get("mechanism_specific") is True),
            "mechanism_specific_scenarios": limitations["mechanism_specific_scenarios"],
        },
        "efe": {
            "numerator": efe_num,
            "denominator": efe_den,
            "rate": _rate(efe_num, efe_den),
            "coverage_joint_over_main": _rate(len(joint), len(main_rows)),
            "identifiable": efe_den > 0,
        },
        "efe_at_success": {
            "numerator": len(success_fail),
            "denominator": len(success),
            "rate": _rate(len(success_fail), len(success)),
            "terminal_match_main": len(matches),
            "terminal_match_unadjudicable": len(match_unadjudicable),
            "coverage_of_terminal_match": _rate(len(success), len(matches)),
            "identifiable": len(success) > 0,
            "numerator_run_ids": [row.get("run_id") for row in success_fail],
            "definition": "joint_observed AND terminal_outcome MATCH AND adjudication is not UNRESOLVED; UNKNOWN is outside the denominator",
        },
        "metrics": {
            "terminal_match": _count(main_rows, "terminal_outcome", "MATCH"),
            "terminal_mismatch": _count(main_rows, "terminal_outcome", "MISMATCH"),
            "terminal_unknown": _count(main_rows, "terminal_outcome", "UNKNOWN"),
            "stale_accepted": _count(main_rows, "authority_decision", "STALE_ACCEPTED"),
            "no_stale_recorded": _count(main_rows, "authority_decision", "NO_STALE_ACCEPTANCE_RECORDED"),
            "authority_unknown": _count(main_rows, "authority_decision", "UNKNOWN"),
            "not_legal": _count(main_rows, "physical_effect_state", "NOT_LEGAL"),
            "confirmed_effect": _count(main_rows, "physical_effect_state", "CONFIRMED_EFFECT"),
            "physical_absent": _count(main_rows, "physical_effect_state", "ABSENT"),
            "semantic_invalid": len(semantic_invalid),
            "semantic_adjudicable_effects": len(observed_effects),
            "semantic_invalid_rate": _rate(len(semantic_invalid), len(observed_effects)),
            "evidence_distribution": dict(Counter(str(row.get("evidence_state")) for row in main_rows)),
            "successor_required_cells": len(required),
            "successor_completed": len(successor_completed),
            "successor_completion_rate": _rate(len(successor_completed), len(required)),
            "must_stop_cells": len(must_stop),
            "correct_stop": len(correct_stops),
            "correct_stop_accuracy": _rate(len(correct_stops), len(must_stop)),
            "unknown_rate_main": _rate(unknown_count(main_rows), len(main_rows)),
            "conflict_rate_main": _rate(_count(main_rows, "evidence_state", "CONFLICT"), len(main_rows)),
            "latency_ms_mean_by_path": latency_by_path,
            "local_overhead_ms_mean_by_path": overhead_by_path,
            "scenario_breakdown": _scenario_breakdown(main_rows),
        },
        "limitations": limitations,
        "tests": {
            "command": r".\.venv\Scripts\python.exe -m pytest frozen_proposals/oracle_test_suite -q --tb=line",
            "passed": tests_passed,
            "output_tail": "\n".join([line for line in pytest_output.splitlines() if line.strip()][-8:]),
        },
        "week1_archived_joint_sha256": archived_hash,
        "week1_archived_joint_unmodified": archived_hash is not None and archived_hash.lower() == ARCHIVED_SHA256,
        "week1_outcome_schema_version_field": schema.get("version"),
        "week1_outcome_schema_id": schema.get("$id"),
        "durability_claim": DURABILITY_CLAIM,
        "process_kill_used": False,
        "environment": environment_record(),
        "journal_modes": sorted({str(row.get("journal_mode")) for row in pilot_rows if row.get("journal_mode")}),
        "synchronous_values": sorted({str(row.get("synchronous")) for row in pilot_rows if row.get("synchronous")}),
    }

    _write_csv(root / "week2_replay_joint.csv", manifests)
    (root / "week2_validation_report.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "week2_report.md").write_text(_render_report(stats), encoding="utf-8")
    return stats


def _write_csv(path: Path, manifests: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in manifests:
            out = dict(row)
            out["source_run_id"] = row.get("run_id")
            out["in_main_comparison"] = _is_main(row)
            writer.writerow({key: out.get(key) for key in CSV_FIELDS})


def _render_report(stats: dict[str, Any]) -> str:
    pilot = stats["pilot"]
    counts = stats["counts"]
    efe = stats["efe"]
    success = stats["efe_at_success"]
    metrics = stats["metrics"]
    criteria_lines = "\n".join(
        f"- {item['id']}. {'通过' if item['pass'] else '未通过'}：{item['name']}。{item['detail']}。"
        for item in stats["criteria"]
    )
    breakdown_lines = [
        "| 场景 | 提案 | 单元格 | 过期接纳 | 非法物理 | 终态 MATCH | 语义 INVALID | CONFLICT | 正确停止 | 后继 FAILED | CRASHED | 联合观察 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in metrics["scenario_breakdown"]:
        breakdown_lines.append(
            "| {scenario_id} | {proposals} | {cells} | {stale_accepted} | {not_legal} | {terminal_match} | {semantic_invalid} | {evidence_conflict} | {correct_stop} | {successor_failed} | {crashed} | {joint_observed} |".format(**row)
        )
    breakdown = "\n".join(breakdown_lines)
    smoke = stats["smoke"]
    smoke_text = "通过" if smoke["pass"] else "未通过"
    mismatch = "无" if not smoke["mismatches"] else json.dumps(smoke["mismatches"], ensure_ascii=False)
    efe_text = (
        f"{efe['numerator']}/{efe['denominator']}"
        if efe["identifiable"]
        else "不可识别"
    )
    success_text = (
        f"{success['numerator']}/{success['denominator']}"
        if success["identifiable"]
        else "不可识别"
    )
    coverage = success["coverage_of_terminal_match"]
    coverage_text = "不可计算" if coverage is None else str(coverage)
    numerator_ids = ", ".join(success["numerator_run_ids"]) if success["numerator_run_ids"] else "无"
    journals = ", ".join(stats["journal_modes"]) or "未记录"
    syncs = ", ".join(stats["synchronous_values"]) or "未记录"
    env = stats["environment"]
    return f"""# Week 2 固定提案基准

## 1. 决定

**{stats['decision']}**。{stats['decision_reason']}

十六条记录在 `frozen_proposals/week2_validation_report.json` 的 `criteria`：

{criteria_lines}

## 2. 仓库侦察摘要

运行时是 Python {env['python']}，数据库是 SQLite {env['sqlite']}。Direct、Admission、Claim 分别对应现有 B0、B1、B3 的字节交付行为；Outbox 是本周新增的同库事务发件箱。B2 没有被改名为 Outbox。细节和文件路径在 `frozen_proposals/repository_recon.md`。Week 1 的 `outcome.schema.json` 没有 `version` 字段，`$id` 是 `{stats['week1_outcome_schema_id']}`。该文件没有被修改。

## 3. 冻结接口

`EffectRequest`、接收端、故障控制器、路径适配器、提案清单、场景清单、运行事件和 `OracleResult` 写在 `frozen_proposals/experiment_contract.md`，JSON Schema 在 `frozen_proposals/schemas/`。四条路径收到的都是同一份不可变提案字节。提案文件在每次运行前后做 SHA256。

## 4. 场景清单

十二个场景在 `frozen_proposals/scenario_manifest/`。S01 到 S08、S10 到 S12 的 `common_core` 为 true，进入四路径主比较。S09 的 `mechanism_role` 是 `MECHANISM_SPECIFIC_DIAGNOSTIC`：故障点 `after_commit_before_send` 只有 Claim 和 Outbox 会走到。Direct 与 Admission 没有同库持久意图，这两格记为 `NOT_APPLICABLE`，不记为失败，也不进入主比较。

S06 的并发编辑打在 `after_admit_before_send`。四条路径的本地决定都发生在这个钩子之前，发送时都不再核对权威。因此 S06 不是“Outbox 优于其他路径”的证据。

## 5. 提案冻结与公平性

第一批有 {pilot['proposals']} 个提案，登记在 `frozen_proposals/proposal_registry.jsonl`。载荷字节在 `frozen_proposals/proposals/*.bin`，生成之后路径只读这些字节。冒烟公平性：{smoke_text}。不一致项：{mismatch}。依据是 `frozen_proposals/smoke_gate.json` 与 `frozen_proposals/week2_validation_report.json` 的 `smoke`。

公平性比较的是输入：提案标识、载荷 SHA256、任务语义版本、初始世界哈希、故障日程哈希、权威初态哈希、接收端实现和版本、oracle 版本、超时和预算、代码提交、环境哈希、实现哈希。它不比较终态或效果。

## 6. 五个独立 oracle

`terminal_oracle`、`authority_oracle`、`evidence_oracle`、`semantics_oracle`、`successor_oracle` 只读观察对象，互不调用。语义 oracle 使用清单里的计分世界快照和 `predicate.fulfillment.v1`，不读路径自报的语义字段。权威 oracle 用接收端接纳序号和权威日志的序号；排不出先后时返回 `UNKNOWN`。证据 oracle 可以返回 `CONFLICT`。后继 oracle 先判断 `REQUIRED`、`NOT_REQUIRED` 或 `UNKNOWN`，再给 `COMPLETED`、`FAILED`、`CORRECT_STOP`、`INCORRECT_STOP` 或 `NOT_APPLICABLE`。`CORRECT_STOP` 对应的 Week 1 后继状态是 `NOT_APPLICABLE`，不是 `NOT_COMPLETED`。

物理状态和恢复状态是从同一观察直接计算的合同字段，不读取其他 oracle 的返回值。`RESTORED_BYTES` 不会自动变成 `CONFIRMED_EFFECT`。

## 7. Same-DB Outbox 与故障矩阵

业务行和 outbox 行在同一个 SQLite 事务里提交，中继在提交之后。持久性声明只有这一句：{stats['durability_claim']}。

矩阵在 `frozen_proposals/outbox_crash_matrix.csv`。`process_kill` 全部为 false，`power_loss_claim` 全部为 false。本周没有使用 `process.kill`。进程内异常不是掉电。第一批运行读到的 `journal_mode` 是 {journals}，`synchronous` 是 {syncs}。没有声称任意 HTTP 恰好一次，也没有声称跨库原子性。

## 8. 冒烟门结果

冒烟是 `SMOKE`，使用 S01、S03、S08 的提案 `p01`、`p04`、`p10`，共 {counts['smoke_runs']} 次运行。公平性{smoke_text}。冒烟行不计入第一批指标。

## 9. 第一批 pilot 计数

这些数都来自 `frozen_proposals/week2_replay_joint.csv`，标签是 `PILOT_FIRST_BATCH`。

- 提案：{counts['pilot_proposals']}。这是聚类单位。
- 单元格：{counts['pilot_cells']}。每个提案有四条路径，所以单元格数是提案数的四倍，不是四倍的独立提案。
- 运行：{counts['pilot_run_ids']} 个 `run_id`。
- 主比较提案：{pilot['main_proposals']}。主比较单元格：{pilot['main_cells']}。S09 的 {counts['mechanism_specific_cells']} 个单元格排除在主比较之外。
- 冒烟另有 {counts['smoke_runs']} 次运行，不并入上面的单元格。
- 全部清单行 {counts['all_manifest_rows']}，唯一 `run_id` {counts['unique_run_ids']}，其中 `ABANDONED` {counts['abandoned']}。

一次运行每条路径只跑一次。60 个单元格不是 60 个独立提案，也没有统计功效声明。

## 10. 分项指标

主比较分母是 `in_main_comparison` 为 true 的行。联合观察是 `joint_observed` 为 true 的主比较行。

- 终态 MATCH：{metrics['terminal_match']}。终态 MISMATCH：{metrics['terminal_mismatch']}。终态 UNKNOWN：{metrics['terminal_unknown']}。
- 过期权威被接纳：{metrics['stale_accepted']}。没有记录到过期接纳：{metrics['no_stale_recorded']}。权威 UNKNOWN：{metrics['authority_unknown']}。`NO_STALE_ACCEPTANCE_RECORDED` 只表示没有记录到接纳了过期凭证。
- 非法物理效果：{metrics['not_legal']}。`CONFIRMED_EFFECT`：{metrics['confirmed_effect']}。`ABSENT`：{metrics['physical_absent']}。
- 可裁定的已观察效果里，语义 INVALID {metrics['semantic_invalid']} / {metrics['semantic_adjudicable_effects']}，比率 {metrics['semantic_invalid_rate']}。没有物理效果的行语义是 `UNKNOWN`，不进这个分母。
- 证据分布：{json.dumps(metrics['evidence_distribution'], ensure_ascii=False)}。
- 后继义务为 REQUIRED 的单元格 {metrics['successor_required_cells']}，其中 COMPLETED {metrics['successor_completed']}，比率 {metrics['successor_completion_rate']}。本基准没有实现后继感知重写，所以这里的完成数可以是 0。
- 应当停止的单元格 {metrics['must_stop_cells']}，其中 `CORRECT_STOP` {metrics['correct_stop']}，准确率 {metrics['correct_stop_accuracy']}。
- 主比较里任一主维度为 UNKNOWN 的单元格比率：{metrics['unknown_rate_main']}（{counts['unknown_main']} / {len(main_rows_safe(stats))}）。UNKNOWN 不是失败。
- 主比较 CONFLICT 比率：{metrics['conflict_rate_main']}（{counts['conflict_main']} 行）。CONFLICT 不是 UNKNOWN 和 VALID 之间的等级，也不单独构成 EFE 分子。
- 主比较平均延迟毫秒：{json.dumps(metrics['latency_ms_mean_by_path'], ensure_ascii=False)}。
- 主比较平均本地开销毫秒：{json.dumps(metrics['local_overhead_ms_mean_by_path'], ensure_ascii=False)}。本地开销是路径时间减去接收端时间。

EFE 只在主比较的联合观察上计算：分子是 `adjudication` 为 `CONFIRMED_FAILURE` 的行，分母是联合观察行。依据只来自过期接纳、非法物理或重复逻辑效果。终态不匹配、恢复字节、正确停止、场景标签和 UNKNOWN 都不单独进分子。结果：{efe_text}。联合覆盖率（联合观察 / 主比较单元格）是 {efe['coverage_joint_over_main']}。

EFE@success 只在上述联合观察里终态为 MATCH、且裁决不是 UNRESOLVED 的行上计算。UNKNOWN 不进入分母，也不当成“没有失败”。结果：{success_text}。主比较终态 MATCH 共 {success['terminal_match_main']} 行，其中未能独立裁决的 {success['terminal_match_unadjudicable']} 行。覆盖率（可裁决成功行 / 终态 MATCH 行）是 {coverage_text}。分子运行：{numerator_ids}。这些运行嵌在提案聚类里，不是独立任务。没有把 S3/B3 式的恢复率当成 EFE@success。`RESTORED_BYTES` 仍只是恢复字段。

按场景的主比较计数：

{breakdown}

## 11. 与 Week 1 的边界

Week 1 的决定仍是历史审计里的 CONDITIONAL GO：联合观察 52/72，EFE 28/52，EFE@success 6/16。那些数字不是本周结果。本周行的 `cohort` 只有 `SMOKE` 或 `PILOT_FIRST_BATCH`。没有把本周行写入 `experiments/historical_audit/archived_joint.csv`。该文件 SHA256 为 `{stats['week1_archived_joint_sha256']}`，与开跑前记录一致：{str(stats['week1_archived_joint_unmodified']).lower()}。缺失的历史格子保持缺失。

## 12. 科学边界声明

- 不实现 Semantic Claim，不实现 Successor-Aware Reconciliation，不实现类似 Cordon 的任务事务。
- 不声称 Outbox 普遍优于其他路径。S03 上四条路径都会交付过时字节；S06 上四条路径都会在决定之后的窗口里交付旧字节。
- 不声称任意 HTTP 恰好一次，不声称跨库原子性。
- 不把进程内故障或 `process.kill` 说成掉电。本周没有 `process.kill`。
- UNKNOWN 不是 FAILURE。RESTORED_BYTES 不是 CONFIRMED_EFFECT。OUTDATED 场景标签不是 STALE_AUTHORITY。CORRECT_STOP 不是后继失败。NOT_APPLICABLE 不是 FAILURE。CONFLICT 不是 UNKNOWN 与 VALID 之间的有序点。
- 每个提案只跑一次每条路径。提案是统计单位。

## 13. 复现命令

环境哈希是 Python 版本、平台、SQLite 版本和 `frozen_proposals/hashing.py` 里列出的包版本的规范 JSON 的 SHA256。实现哈希是 `frozen_proposals/**/*.py` 的 SHA256。Git HEAD 不包含本周新增的 `frozen_proposals/` 树。

```text
.\\.venv\\Scripts\\python.exe -m pytest frozen_proposals/oracle_test_suite -q --tb=short
.\\.venv\\Scripts\\python.exe -m frozen_proposals.frozen_proposal_runner
```

第二条命令会再跑一遍测试，写入 `frozen_proposals/pytest_output.txt`，生成冻结清单、冒烟、第一批、`frozen_proposals/week2_replay_joint.csv`、`frozen_proposals/week2_validation_report.json` 和本报告。测试输出摘录：

```text
{stats['tests']['output_tail']}
```
"""


def main_rows_safe(stats: dict[str, Any]) -> list[Any]:
    return [None] * int(stats["pilot"]["main_cells"])
