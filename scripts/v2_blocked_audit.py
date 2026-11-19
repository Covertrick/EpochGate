"""只读历史 summary，给 model_no_effect 逐格分类。不改原始文件。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "correctness" / "v2-blocked-audit"
DOC = ROOT / "docs" / "开发文档" / "M4-M6-v2-blocked审计.md"

SOURCES = {
    "m4-pilot": ROOT / "results" / "runtime" / "correctness" / "m4-pilot" / "summary.json",
    "m4-confirm": ROOT / "results" / "runtime" / "correctness" / "m4-confirm" / "summary.json",
    "m5": ROOT / "results" / "runtime" / "correctness" / "m5" / "summary.json",
}


def classify(row: dict) -> dict:
    present = sorted(key for key, value in row.items() if value not in (None, [], ""))
    transcript = bool(row.get("tool_events") or row.get("messages") or row.get("finish_reason"))
    evidence = {
        "error_type": row.get("error_type"),
        "end_reason": row.get("end_reason"),
        "model": row.get("model"),
        "stored_fields": present,
        "transcript_stored": transcript,
    }
    error = row.get("error_type")
    if error == "model_no_effect" and not transcript:
        label = "OUTCOME_UNKNOWN"
        reason = "该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。"
    elif error and str(error).startswith("http_"):
        label = "MODEL_API_FAILED"
        reason = "原始 error_type 以 http_ 开头。"
    elif error in {"TimeoutError", "URLError"}:
        label = "MODEL_API_FAILED"
        reason = "原始 error_type 是网络或超时错误。"
    elif error == "missing_key":
        label = "ENVIRONMENT_BLOCKED"
        reason = "原始 error_type 是 missing_key。"
    elif error == "feasibility_failed":
        label = "ENVIRONMENT_BLOCKED"
        reason = "原始记录写明可行性没有通过。"
    elif transcript and error == "model_no_effect":
        label = "MODEL_DID_NOT_ACT"
        reason = "记录里有轨迹，且没有效果载荷。"
    else:
        label = "OUTCOME_UNKNOWN"
        reason = "原始字段不足以落入更具体的类别。"
    return {"label": label, "reason": reason, "evidence": evidence}


def audit_batch(name: str, payload: dict) -> dict:
    rows = payload.get("rows", [])
    blocked = [row for row in rows if row.get("blocked")]
    cells = []
    for index, row in enumerate(blocked):
        item = classify(row)
        item.update({
            "batch": name,
            "index": index,
            "task_id": row.get("task_id"),
            "family": row.get("family"),
            "arm": row.get("arm"),
            "fault": row.get("fault"),
            "runtime": row.get("runtime"),
        })
        cells.append(item)
    by_arm = Counter(row.get("arm") for row in rows)
    blocked_arm = Counter(row.get("arm") for row in blocked)
    by_fault = Counter(row.get("fault") for row in rows)
    blocked_fault = Counter(row.get("fault") for row in blocked)
    clusters = []
    groups = defaultdict(list)
    for row in rows:
        groups[(row.get("task_id"), row.get("fault"))].append(row)
    for key, group in sorted(groups.items()):
        flags = [bool(row.get("blocked")) for row in group]
        if any(flags):
            clusters.append({
                "task_id": key[0],
                "fault": key[1],
                "n": len(group),
                "blocked": sum(flags),
                "arms": [row.get("arm") for row in group if row.get("blocked")],
                "all_arms_blocked": all(flags) and len(group) > 1,
            })
    return {
        "batch": name,
        "assigned": len(rows),
        "blocked": len(blocked),
        "labels": dict(Counter(cell["label"] for cell in cells)),
        "arm_assigned": dict(by_arm),
        "arm_blocked": dict(blocked_arm),
        "fault_assigned": dict(by_fault),
        "fault_blocked": dict(blocked_fault),
        "clusters": clusters,
        "cells": cells,
        "coding_family_note": payload.get("coding"),
    }


def rates(assigned: dict, blocked: dict) -> list[str]:
    lines = []
    for key in sorted(set(assigned) | set(blocked), key=lambda item: str(item)):
        total = assigned.get(key, 0)
        bad = blocked.get(key, 0)
        lines.append(f"- {key}：{bad}/{total}")
    return lines


def render(report: dict) -> str:
    lines = [
        "# 已有 blocked 逐格审计",
        "",
        "原始文件没有改。标签只根据该格已经保存的字段。`model_no_effect` 没有工具轨迹时保持 `OUTCOME_UNKNOWN`。",
        "",
        "历史 GO 仍以 `results/runtime/correctness/m6/summary.json` 为准，这里不重判。",
        "",
    ]
    for batch in report["batches"]:
        lines.append(f"## {batch['batch']}")
        lines.append("")
        lines.append(f"分配 {batch['assigned']} 格，blocked {batch['blocked']} 格。标签计数：{json.dumps(batch['labels'], ensure_ascii=False)}。")
        lines.append("")
        lines.append("按臂的 blocked 比例：")
        lines.extend(rates(batch["arm_assigned"], batch["arm_blocked"]))
        lines.append("")
        lines.append("按故障的 blocked 比例：")
        lines.extend(rates(batch["fault_assigned"], batch["fault_blocked"]))
        lines.append("")
        lines.append("任务与故障上的聚集：")
        for cluster in batch["clusters"]:
            lines.append(
                f"- {cluster['task_id']} / {cluster['fault']}：{cluster['blocked']}/{cluster['n']}，"
                f"臂 {', '.join(cluster['arms'])}，整组阻塞 {str(cluster['all_arms_blocked']).lower()}"
            )
        lines.append("")
        lines.append("逐格：")
        for cell in batch["cells"]:
            lines.append(
                f"- {cell['task_id']} {cell['arm']} {cell['fault']}：{cell['label']}。"
                f"{cell['reason']} 已存字段：{', '.join(cell['evidence']['stored_fields'])}。"
            )
        if batch.get("coding_family_note"):
            note = batch["coding_family_note"]
            lines.append("")
            lines.append(
                f"Coding 家族不是 `model_no_effect` 格子。原始记录是 `{note.get('error_type')}`：{note.get('reason')}。"
                "这是整族没有进入矩阵，不把它改写成模型弃权。"
            )
        lines.append("")
    lines.extend([
        "## 敏感性分析",
        "",
        "当前 `scripts/m4_matrix.py` 里，字符串 `model_no_effect` 只在对话接口返回成功、且没有 `submit_effect` 时写入。这个事实属于生成器源码，不是每一格保存下来的轨迹。",
        "",
        "若有人把这条分支当成全部格子的证据，标签会一律变成 `MODEL_DID_NOT_ACT`，而不是 `MODEL_API_FAILED` 或 `TOOL_EXECUTION_FAILED`。即使这样，四臂的 blocked 比例仍然相同：同一份提案被复制到各臂，机制本身没有分到不同的模型失败。聚集在任务×故障，不在 B0/B1/B2/B3 之间。",
        "",
        "本审计不采用这种升级。单格标签保持 `OUTCOME_UNKNOWN`。历史确认批因此仍有一批无法解释的分配实例。零宽 bootstrap 没有覆盖这些实例，不能当作跨任务精度证明。旧 `decision` 字段不改。",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    batches = []
    for name, path in SOURCES.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        batches.append(audit_batch(name, payload))
    report = {"sources_read_only": {name: str(path) for name, path in SOURCES.items()}, "batches": batches}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    DOC.write_text(render(report), encoding="utf-8")
    unknown = sum(cell["label"] == "OUTCOME_UNKNOWN" for batch in batches for cell in batch["cells"])
    print(json.dumps({"cells": sum(len(batch["cells"]) for batch in batches), "unknown": unknown}, ensure_ascii=False))


if __name__ == "__main__":
    main()
