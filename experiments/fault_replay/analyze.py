"""Score gates from the formal rows. The statistical plan is not rewritten here."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from frozen_proposals.hashing import sha256_bytes
from frozen_proposals.oracles import Observation, authority_oracle, evidence_oracle, semantics_oracle
from frozen_proposals.predicate import legal_payload
from fault_replay.contract import (
    ADJUDICATION_VALUES,
    ARCHIVED_JOINT,
    ARCHIVED_SHA256,
    AUTHORITY_VALUES,
    BLIND_AUDIT_SEED,
    EVIDENCE_ORACLE_VALUES,
    PHYSICAL_VALUES,
    RECOVERY_VALUES,
    SEMANTIC_VALUES,
    SEPARATION_SCENARIOS,
    SUCCESSOR_STATUS_VALUES,
    TERMINAL_VALUES,
    WEEK3,
)
from fault_replay.execute import _freeze_status, _separated_physical
from fault_replay.scoring import week2_bundle
from fault_replay.stats import pre_registered

PILOT_CELLS = 60
PILOT_CLUSTERS = 15


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def validate_row(row: dict[str, Any]) -> list[str]:
    errors = []
    if row.get("cohort") != "WEEK3_FORMAL":
        errors.append("cohort")
    if not row.get("run_id") or not row.get("proposal_id") or not row.get("scenario_id") or not row.get("path"):
        errors.append("identity")
    checks = {
        "terminal_outcome": TERMINAL_VALUES,
        "authority_decision": AUTHORITY_VALUES,
        "physical_effect_state": PHYSICAL_VALUES,
        "evidence_state": EVIDENCE_ORACLE_VALUES,
        "semantic_validity": SEMANTIC_VALUES,
        "recovery_state": RECOVERY_VALUES,
        "successor_status": SUCCESSOR_STATUS_VALUES,
        "adjudication": ADJUDICATION_VALUES,
    }
    for field, allowed in checks.items():
        if row.get(field) not in allowed:
            errors.append(field)
    if row.get("runner_status") not in {"COMPLETED", "CRASHED", "NOT_APPLICABLE", "INVALID", "TIMEOUT", "BLOCKED", "ABANDONED"}:
        errors.append("runner_status")
    return errors


def _main(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if _as_bool(row.get("in_main_comparison"))]


def _gate_a(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hits = []
    for row in _main(rows):
        semantic_invalid = row.get("semantic_validity") == "INVALID"
        confirmed = row.get("physical_effect_state") == "CONFIRMED_EFFECT"
        restored = row.get("recovery_state") == "RESTORED_BYTES"
        if semantic_invalid and (confirmed or restored):
            hits.append(row)
    by_scenario: dict[str, int] = defaultdict(int)
    for row in hits:
        by_scenario[str(row["scenario_id"])] += 1
    return {"pass": len(hits) >= 6, "count": len(hits), "by_scenario": dict(sorted(by_scenario.items()))}


def _gate_b(crash_rows: list[dict[str, str]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in crash_rows:
        grouped[(row["checkpoint"], row["path"])].add(row["evidence_state"])
    stable = sorted({next(iter(states)) for states in grouped.values() if len(states) == 1})
    unstable = [f"{checkpoint}:{path}" for (checkpoint, path), states in sorted(grouped.items()) if len(states) != 1]
    mismatches = sum(1 for row in crash_rows if row.get("proposal_contract_match") != "true")
    return {
        "pass": len(crash_rows) >= 80 and len(stable) >= 3 and not unstable and mismatches == 0,
        "rows": len(crash_rows),
        "stable_states": stable,
        "unstable": unstable,
        "contract_mismatches": mismatches,
    }


def _gate_c(rows: list[dict[str, Any]]) -> dict[str, Any]:
    found = []
    for scenario_id in ("S17", "S18"):
        proposals = sorted({row["proposal_id"] for row in rows if row["scenario_id"] == scenario_id})
        for proposal_id in proposals:
            cells = {row["path"]: row for row in rows if row["proposal_id"] == proposal_id}
            if set(cells) != {"Direct", "Admission", "Claim", "Outbox"}:
                continue
            frozen = cells["Claim"]["successor_status"] == "COMPLETED_VALID" and not _as_bool(cells["Claim"]["published_old_bytes"])
            frozen = frozen and cells["Outbox"]["successor_status"] == "COMPLETED_VALID" and not _as_bool(cells["Outbox"]["published_old_bytes"])
            published = _as_bool(cells["Direct"]["published_old_bytes"]) and _as_bool(cells["Admission"]["published_old_bytes"])
            if frozen and published:
                found.append(f"{scenario_id}:{proposal_id}")
    return {"pass": len(found) >= 1, "cases": found}


def _legal_clusters(rows: list[dict[str, Any]], schema_errors: dict[str, list[str]]) -> list[str]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["cluster_key"])].append(row)
    legal = []
    for key, cells in sorted(grouped.items()):
        if len(cells) != 4:
            continue
        if any(schema_errors.get(str(cell["run_id"])) for cell in cells):
            continue
        hashes = {cell.get("payload_sha256") for cell in cells}
        if len(hashes) != 1 or "" in hashes or None in hashes:
            continue
        if any(not _as_bool(cell.get("payload_hash_match")) for cell in cells):
            continue
        if any(cell.get("runner_status") == "INVALID" for cell in cells):
            continue
        paths = {cell.get("path") for cell in cells}
        if paths != {"Direct", "Admission", "Claim", "Outbox"}:
            continue
        legal.append(key)
    return legal


def _load_crash() -> list[dict[str, str]]:
    with (WEEK3 / "crash_matrix.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _recompute(row: dict[str, Any]) -> dict[str, Any]:
    trace = Path(str(row["provenance_ref"]))
    if not trace.is_absolute():
        trace = WEEK3 / trace
    document = json.loads(trace.read_text(encoding="utf-8"))
    if document.get("mechanism_applicable") is False:
        return {"runner_status": "NOT_APPLICABLE"}
    physical_path = trace.parent / "physical.bin"
    physical = physical_path.read_bytes() if physical_path.is_file() else None
    observation = Observation(
        run_id=document["run_id"],
        proposal_id=row["proposal_id"],
        proposal_sha256=document["proposal_sha256"],
        predicate_version=document["predicate_version"],
        scoring_world=document["scoring_world"],
        physical_bytes=physical,
        receiver_log=document.get("receiver_log"),
        authority_log=document.get("authority_log"),
        recovery_attempted=bool(document.get("recovery_attempted")),
        recovery_bytes_sha256=document.get("recovery_bytes_sha256"),
        successor_obligation=document.get("successor_obligation") or "UNKNOWN",
        stop_condition=document.get("stop_condition") or "NOT_A_STOP_SCENARIO",
        withheld=bool(document.get("withheld")),
        duplicate_effect=bool(document.get("duplicate_effect")),
        outdated_scenario_label=document.get("outdated_scenario_label") or "UNKNOWN",
        successor_effect_bytes=physical if document.get("scenario_behavior") == "notified_freeze" else None,
    )
    if document.get("scenario_behavior") == "week2_path":
        bundle = week2_bundle(observation, document.get("runner_status") or "COMPLETED")
        return {
            "terminal_outcome": bundle["terminal_outcome"],
            "authority_decision": bundle["authority_decision"],
            "physical_effect_state": bundle["week2_physical_effect_state"],
            "evidence_state": bundle["evidence_state"],
            "semantic_validity": bundle["semantic_validity"],
            "recovery_state": bundle["week2_recovery_state"],
            "successor_status": bundle["successor_status"],
        }
    claim = physical if physical is not None and sha256_bytes(physical) == document["proposal_sha256"] else b"\x00not-the-claim"
    evidence_name = document.get("recovery_evidence_state") or "NOT_APPLICABLE"
    restored = evidence_name == "PUBLISH_SEALED"
    attempted = evidence_name not in {"NOT_APPLICABLE", "", None}
    physical_state, recovery_state = _separated_physical(
        physical,
        claim if physical is not None else b"",
        document["scoring_world"],
        restored=restored,
        attempted=attempted,
    )
    result = {
        "authority_decision": authority_oracle(observation),
        "evidence_state": evidence_oracle(observation),
        "semantic_validity": semantics_oracle(observation),
        "physical_effect_state": physical_state,
        "recovery_state": recovery_state,
    }
    if document.get("scenario_behavior") == "notified_freeze":
        status = _freeze_status(physical, claim if physical is not None else b"", document["scoring_world"], "Claim" if physical != claim or physical is None else "Direct")
        # Path is not an input. Derive the status from bytes alone.
        legal = legal_payload(document["scoring_world"])
        if physical is None and legal == b"":
            result["successor_status"] = "CORRECT_STOP"
        elif physical is not None and legal != b"" and physical == legal:
            result["successor_status"] = "COMPLETED_VALID"
        else:
            result["successor_status"] = "FAILED"
        del status
    return result


def _blind_audit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    population = sorted(rows, key=lambda row: str(row["run_id"]))
    n = len(population)
    if n == 0:
        return []
    low = math.ceil(0.10 * n)
    high = math.floor(0.20 * n)
    k = min(high, max(low, round(0.15 * n)))
    rng = random.Random(BLIND_AUDIT_SEED)
    chosen = rng.sample(population, k)
    audited = []
    fields = (
        "terminal_outcome",
        "authority_decision",
        "physical_effect_state",
        "evidence_state",
        "semantic_validity",
        "recovery_state",
        "successor_status",
    )
    for index, row in enumerate(chosen, start=1):
        recomputed = _recompute(row)
        compared = [name for name in fields if name in recomputed]
        mismatches = [name for name in compared if str(recomputed[name]) != str(row.get(name))]
        audited.append(
            {
                "audit_id": f"A{index:02d}",
                "trace_token": hashlib.sha256(str(row["run_id"]).encode("utf-8")).hexdigest()[:16],
                "run_id": row["run_id"],
                "path_column_withheld": "true",
                "recomputed": json.dumps(recomputed, sort_keys=True),
                "mismatched_fields": ",".join(mismatches),
                "match": "true" if not mismatches else "false",
            }
        )
    return audited


def _write_audit(rows: list[dict[str, Any]]) -> Path:
    target = WEEK3 / "blind_audit.csv"
    fields = ["audit_id", "trace_token", "run_id", "path_column_withheld", "recomputed", "mismatched_fields", "match"]
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return target


def _fmt(value: Any) -> str:
    if value is None:
        return "无"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _rate_line(rows: list[dict[str, Any]], name: str, predicate) -> dict[str, Any]:
    denominator = len(rows)
    numerator = sum(1 for row in rows if predicate(row))
    unknown = {
        "authority": sum(1 for row in rows if row.get("authority_decision") == "UNKNOWN"),
        "semantic": sum(1 for row in rows if row.get("semantic_validity") == "UNKNOWN"),
        "successor": sum(1 for row in rows if row.get("successor_status") == "UNKNOWN"),
        "outdated": sum(1 for row in rows if row.get("authority_decision") == "UNKNOWN" or row.get("semantic_validity") == "UNKNOWN"),
    }[name]
    return {
        "indicator": name,
        "numerator": numerator,
        "denominator": denominator,
        "rate": None if denominator == 0 else numerator / denominator,
        "unknown_cells": unknown,
    }


def _render(report: dict[str, Any]) -> str:
    gates = report["gates"]
    passed = [name for name in ("A", "B", "C", "D") if gates[name]["pass"]]
    failed = [name for name in ("A", "B", "C", "D") if not gates[name]["pass"]]
    if report["decision"] == "WEEK3_COMPARISON":
        opening = (
            "决定：进行 Week 3 正式簇配对比较。四道门都通过。"
            "主要结果是 authority、semantic、successor、outdated 四个分开的指标，不设复合 EFE 标题。"
        )
    else:
        opening = (
            "决定：降级叙述。未通过的门保持失败，不改写成通过。"
            "已完成的部分按受控配对样本和恢复证据来写，不外推成掉电耐久性，也不外推成协议优势。"
        )
    lines = [
        "# Week 3 报告",
        "",
        "## 1. 决定与门",
        "",
        opening,
        "",
        f"通过：{', '.join(passed) if passed else '无'}。未通过：{', '.join(failed) if failed else '无'}。",
        "",
        f"- 门 A 分离：{'通过' if gates['A']['pass'] else '未通过'}。物理 `CONFIRMED_EFFECT` 或 `RESTORED_BYTES` 且语义 `INVALID` 的主比较格子 {gates['A']['count']} 个。按场景：{gates['A']['by_scenario']}。",
        f"- 门 B 崩溃：{'通过' if gates['B']['pass'] else '未通过'}。矩阵 {gates['B']['rows']} 行。稳定证据状态：{', '.join(gates['B']['stable_states']) or '无'}。",
        f"- 门 C 冻结：{'通过' if gates['C']['pass'] else '未通过'}。Claim/Outbox 不发布旧字节并完成合法后继、同时 Direct/Admission 发布旧字节的案例：{', '.join(gates['C']['cases']) or '无'}。",
        f"- 门 D 规模：{'通过' if gates['D']['pass'] else '未通过'}。合法簇 {gates['D']['legal_clusters']}。模式错误 {gates['D']['schema_error_count']}。盲审行 {gates['D']['blind_rows']}，占轨迹 {gates['D']['blind_fraction']}。",
        "",
        "## 2. 进程崩溃注入、进程内故障、掉电",
        "",
        "B-1 是受控进程崩溃注入。父进程看见检查点标记后调用 Windows `TerminateProcess`。子进程内存消失，新进程重新打开 SQLite 和文件。`process_kill` 为 true，`power_loss_claim` 为 false。这不是失败概率。",
        "",
        "Week 2 格子里的 CRASHED 是进程内 `FaultCut`，在本周记为 `IN_PROCESS_FAULT`。它不是操作系统级结束，也不是掉电。本周没有把这两种故障写成同一类证据。",
        "",
        "## 3. 分离结果（门 A）",
        "",
        "物理列在分离场景里按声明摘要解释：目标等于冻结声明，前向发布是 `CONFIRMED_EFFECT`；新进程从密封字节写回目标是 `RESTORED_BYTES`，物理列保持 `PRESENT_UNVERIFIED`。语义列是 `semantics_oracle` 对 `observed_world` 的结果。`NOT_LEGAL` 加语义 `INVALID` 仍是 Week 2 那种叠在一起的条件，不计入门 A。",
        "",
        "| 场景 | 路径 | 物理 | 恢复 | 语义 | 崩溃证据 | 格子 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report["separation_crosstab"]:
        lines.append(
            f"| {item['scenario_id']} | {item['path']} | {item['physical_effect_state']} | {item['recovery_state']} | {item['semantic_validity']} | {item['recovery_evidence_state']} | {item['cells']} |"
        )
    lines.extend(
        [
            "",
            "## 4. 崩溃证据（门 B）",
            "",
            f"四个检查点乘 Claim 与 Outbox，各 10 次，共 {gates['B']['rows']} 行。稳定状态：{', '.join(gates['B']['stable_states']) or '无'}。",
            f"p10 与 p11 的载荷 SHA256、初始世界哈希和权威初始哈希与 Week 2 冻结登记一致：{gates['B']['rows'] - gates['B']['contract_mismatches']} / {gates['B']['rows']}。细节在 `crash_evidence_summary.md`。",
            "",
            "## 5. 通知冻结（门 C）",
            "",
            "S17 和 S18 是场景行为：冻结旧声明，不发布旧字节，再接受合法后继或正确停止。这不是新的协议名称。",
            "",
            "| 场景 | 路径 | 发布旧字节 | 后继状态 | 语义 | 权威 | 格子 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in report["freeze_crosstab"]:
        lines.append(
            f"| {item['scenario_id']} | {item['path']} | {item['published_old_bytes']} | {item['successor_status']} | {item['semantic_validity']} | {item['authority_decision']} | {item['cells']} |"
        )
    lines.extend(
        [
            "",
            "S18 的 `CORRECT_STOP` 不是后继失败。S06 仍是 `NON_DISCRIMINATIVE`，S09 仍是 `MECHANISM_SPECIFIC_DIAGNOSTIC`，两者都不在主比较里。",
            "",
            "## 6. 分开的样本量",
            "",
            f"- 正式簇：{report['sample']['clusters']}。正式格子：{report['sample']['cells']}。",
            f"- 主比较簇：{report['sample']['main_clusters']}。主比较格子：{report['sample']['main_cells']}。",
            f"- 崩溃重复行：{report['sample']['crash_rows']}。这些行不在正式联合表里。",
            f"- Week 2 `PILOT_FIRST_BATCH`：{PILOT_CLUSTERS} 个簇、{PILOT_CELLS} 个格子。只作引用，不进入本周正式样本。",
            "- Week 1 归档的 52 行没有用来填空，归档文件没有被修改。",
            "",
            "## 7. 预注册比较",
            "",
            "单位是簇。估计是 Claim 减对照的平均配对差。区间是 4000 次簇 bootstrap 的 95% 百分位区间，种子 20261120。McNemar 精确 p 值只作补充。`UNKNOWN` 留在分母里，指标为 0，不记为失败。",
            "",
            "| 比较 | 指标 | 配对差 | 95% 下界 | 95% 上界 | 簇数 | McNemar p |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in report["pre_registered"]:
        p_value = item["mcnemar"]["p_value"]
        lines.append(
            f"| {item['comparison']} | {item['indicator']} | {_fmt(item['estimate'])} | {_fmt(item['ci_low'])} | {_fmt(item['ci_high'])} | {item['n_clusters']} | {_fmt(p_value)} |"
        )
    lines.extend(
        [
            "",
            "没有做这三对以外的确认性比较。Claim 与 Outbox 的配对差为 0 时，只表示这次样本里四个指标没有不一致对，不是等价检验，也不是 Outbox 更优。",
            "",
            "## 8. 分开的 EFE 指标",
            "",
            "下面四个比率都不相加。分母是全部主比较格子。",
            "",
            "| 指标 | 分子 | 分母 | 比率 | 该列 UNKNOWN 格子 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for item in report["split_metrics"]:
        lines.append(
            f"| {item['indicator']} | {item['numerator']} | {item['denominator']} | {_fmt(item['rate'])} | {item['unknown_cells']} |"
        )
    lines.extend(
        [
            "",
            f"主比较里至少一列主 oracle 为 UNKNOWN 的格子：{report['unknown_any_cells']}。这些格子留在分母中。",
            "",
            "## 9. 与 Week 1、Week 2 的边界",
            "",
            f"归档文件 SHA256 为 `{report['archived_joint_sha256']}`。与要求的 `{ARCHIVED_SHA256}` 一致：{'是' if report['archived_joint_unchanged'] else '否'}。",
            "Week 2 的决定仍是 CONDITIONAL_GO。本周没有改 week2 目录，没有把 60 个试点格子并进正式统计，也没有实现 Semantic Claim、Successor-Aware Reconciliation 或类似 Cordon 的任务事务。",
            "不声称 Outbox 普遍优于其他路径，不声称任意 HTTP 恰好一次，不声称跨库原子性，不把进程崩溃说成掉电。",
            "",
            "## 10. 复现命令",
            "",
            "```text",
            ".\\.venv\\Scripts\\python.exe -m fault_replay.kill_runner",
            ".\\.venv\\Scripts\\python.exe -m fault_replay",
            ".\\.venv\\Scripts\\python.exe -m pytest fault_replay/tests -q --tb=short",
            ".\\.venv\\Scripts\\python.exe -m pytest frozen_proposals/oracle_test_suite -q --tb=short",
            "```",
            "",
            f"统计计划 SHA256：`{report['statistical_plan_sha256']}`。正式运行前写入 `statistical_plan.sha256`，本报告不改计划正文。",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(rows: list[dict[str, Any]], plan_sha: str) -> dict[str, Any]:
    schema_errors = {str(row["run_id"]): validate_row(row) for row in rows}
    schema_error_count = sum(len(item) for item in schema_errors.values())
    crash_rows = _load_crash()
    gates = {
        "A": _gate_a(rows),
        "B": _gate_b(crash_rows),
        "C": _gate_c(rows),
        "D": {},
    }
    legal = _legal_clusters(rows, schema_errors)
    audit_rows = _blind_audit(rows)
    _write_audit(audit_rows)
    n = len(rows)
    fraction = 0 if n == 0 else len(audit_rows) / n
    audit_ok = n > 0 and math.ceil(0.10 * n) <= len(audit_rows) <= math.floor(0.20 * n)
    gates["D"] = {
        "pass": len(legal) >= 24 and schema_error_count == 0 and audit_ok and len({row["path"] for row in rows}) == 4,
        "legal_clusters": len(legal),
        "schema_error_count": schema_error_count,
        "blind_rows": len(audit_rows),
        "blind_fraction": round(fraction, 4),
        "blind_mismatches": sum(1 for row in audit_rows if row["match"] != "true"),
    }
    main_rows = _main(rows)
    from fault_replay.stats import indicator_authority, indicator_outdated, indicator_semantic, indicator_successor

    split_metrics = [
        _rate_line(main_rows, "authority", indicator_authority),
        _rate_line(main_rows, "semantic", indicator_semantic),
        _rate_line(main_rows, "successor", indicator_successor),
        _rate_line(main_rows, "outdated", indicator_outdated),
    ]
    separation = []
    grouped: dict[tuple, int] = defaultdict(int)
    for row in rows:
        if row["scenario_id"] not in SEPARATION_SCENARIOS:
            continue
        key = (
            row["scenario_id"],
            row["path"],
            row["physical_effect_state"],
            row["recovery_state"],
            row["semantic_validity"],
            row["recovery_evidence_state"],
        )
        grouped[key] += 1
    for key, count in sorted(grouped.items()):
        separation.append(
            {
                "scenario_id": key[0],
                "path": key[1],
                "physical_effect_state": key[2],
                "recovery_state": key[3],
                "semantic_validity": key[4],
                "recovery_evidence_state": key[5],
                "cells": count,
            }
        )
    freeze = []
    freeze_grouped: dict[tuple, int] = defaultdict(int)
    for row in rows:
        if row["scenario_id"] not in {"S17", "S18"}:
            continue
        key = (
            row["scenario_id"],
            row["path"],
            str(_as_bool(row["published_old_bytes"])).lower(),
            row["successor_status"],
            row["semantic_validity"],
            row["authority_decision"],
        )
        freeze_grouped[key] += 1
    for key, count in sorted(freeze_grouped.items()):
        freeze.append(
            {
                "scenario_id": key[0],
                "path": key[1],
                "published_old_bytes": key[2],
                "successor_status": key[3],
                "semantic_validity": key[4],
                "authority_decision": key[5],
                "cells": count,
            }
        )
    archived = _sha256_file(ARCHIVED_JOINT)
    all_pass = all(gates[name]["pass"] for name in ("A", "B", "C", "D"))
    clusters = {row["cluster_key"] for row in rows}
    main_clusters = {row["cluster_key"] for row in main_rows}
    unknown_any = sum(
        1
        for row in main_rows
        if "UNKNOWN" in {row.get("terminal_outcome"), row.get("authority_decision"), row.get("physical_effect_state"), row.get("semantic_validity")}
    )
    report = {
        "decision": "WEEK3_COMPARISON" if all_pass else "DEGRADED",
        "narrative": "full_week3_comparison" if all_pass else "degraded_paired_study",
        "gates": gates,
        "sample": {
            "clusters": len(clusters),
            "cells": len(rows),
            "main_clusters": len(main_clusters),
            "main_cells": len(main_rows),
            "crash_rows": len(crash_rows),
            "pilot_cells_not_included": PILOT_CELLS,
            "pilot_label": "PILOT_FIRST_BATCH",
        },
        "pre_registered": pre_registered(main_rows),
        "split_metrics": split_metrics,
        "unknown_any_cells": unknown_any,
        "separation_crosstab": separation,
        "freeze_crosstab": freeze,
        "schema_errors": {key: value for key, value in schema_errors.items() if value},
        "statistical_plan_sha256": plan_sha,
        "archived_joint_sha256": archived,
        "archived_joint_unchanged": archived == ARCHIVED_SHA256,
        "week2_files_modified": False,
    }
    (WEEK3 / "week3_validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (WEEK3 / "week3_report.md").write_text(_render(report), encoding="utf-8")
    return report
