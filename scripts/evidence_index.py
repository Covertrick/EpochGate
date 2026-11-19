"""从原始 JSON 重算证据索引。手改过的数字对不上就失败，不覆盖。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "开发文档" / "证据索引.md"
RESULTS = ROOT / "results" / "runtime" / "correctness"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def count_json(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.glob("*.json"))


def facts() -> dict[str, str]:
    rescore = load(RESULTS / "v3-coding" / "rescore" / "summary.json")
    repeat = load(RESULTS / "e4-repeat" / "summary.json")
    outbox = load(RESULTS / "outbox-baseline" / "summary.json")
    regression = load(RESULTS / "final-regression" / "summary.json")
    m5 = load(RESULTS / "m5" / "summary.json")
    m5_coding = load(RESULTS / "v2-m5-coding" / "summary.json")
    m6 = load(RESULTS / "v2-m6" / "summary.json")
    feasibility = load(RESULTS / "v4-coding" / "feasibility" / "summary.json")
    audit = load(RESULTS / "v4-coding" / "prompt-audit.json")["summary"]
    owners = sorted({str(row.get("capability_owner")) for row in m5["rows"]})
    faults = sorted({str(row.get("fault")) for row in m5["rows"]})
    gate_path = RESULTS / "v4-coding" / "dev" / "gate.json"
    confirm_path = RESULTS / "v4-coding" / "confirm" / "summary.json"
    gate = load(gate_path) if gate_path.is_file() else None
    confirm_v4 = load(confirm_path) if confirm_path.is_file() else None
    v4_dev_assigned = "absent" if gate is None else str(len(gate["rows"]))
    v4_dev_scorable = "absent" if gate is None else str(gate["scorable_patches"])
    v4_confirm_n = "absent" if confirm_v4 is None else str(confirm_v4["denominator"])
    v4_confirm_initial_passed = "absent" if confirm_v4 is None else str(sum(1 for row in confirm_v4["rows"] if row.get("initial_hidden_passed") is True))
    v4_confirm_successor_passed = "absent" if confirm_v4 is None else str(sum(1 for row in confirm_v4["rows"] if row.get("agent_successor_passed") is True))
    raise_invalid = sum(1 for row in feasibility["rows"] if row["semantic_raise"]["invalid"])
    v1_legal = sum(1 for row in feasibility["rows"] if row["version_comment"]["legal_successor"])
    return {
        "v3_assigned_confirm": str(rescore["assigned_confirm"]),
        "v3_budget_exceeded": str(len(rescore["budget_exceeded"])),
        "v3_hidden_passed": str(rescore["passed_count"]),
        "v3_patches_scored": str(rescore["model_patches_scored"]),
        "e4_repeat_cells": str(repeat["cells"]),
        "e4_after_cells": str(count_json(RESULTS / "after" / "e4" / "cells")),
        "e4_repeat_dir_cells": str(count_json(RESULTS / "e4-repeat" / "cells")),
        "outbox_stale": outbox["stale_token"],
        "outbox_fresh": outbox["fresh_token"],
        "outbox_claim_to_bytes": str(outbox["claim_to_bytes"]).lower(),
        "outbox_external": str(outbox["external_exactly_once"]).lower(),
        "regression_result": regression["result"],
        "power_loss": regression["power_loss"],
        "m5_n": str(m5["n"]),
        "m5_blocked": str(m5["blocked"]),
        "m5_owners": ",".join(owners),
        "m5_faults": ",".join(faults),
        "m5_coding_status": m5_coding["status"],
        "m6_precision": m6["precision_status"],
        "m6_h5_p": str(m6["historical"]["h5_mcnemar"]["pvalue"]),
        "v4_dev_tasks": str(len(feasibility["rows"])),
        "v4_v1_legal": str(v1_legal),
        "v4_raise_invalid": str(raise_invalid),
        "v4_image": feasibility["image_id"],
        "v4_dev_assigned": v4_dev_assigned,
        "v4_dev_scorable": v4_dev_scorable,
        "v4_confirm_n": v4_confirm_n,
        "v4_confirm_initial_passed": v4_confirm_initial_passed,
        "v4_confirm_successor_passed": v4_confirm_successor_passed,
        "v4_leak_tasks": str(audit["tasks"]),
        "v4_leak_extracted": str(audit["symptom_extracted"]),
        "v4_leak_source": str(audit["saw_hidden_test_source"]),
        "v4_leak_assertion": str(audit["saw_assertion_expectation"]),
    }


def render(values: dict[str, str]) -> str:
    return "\n".join([
        "# 证据索引",
        "",
        "数字由 `scripts/evidence_index.py` 从原始 JSON 读出。改这份文档里的数字之后再跑脚本会失败。各批不相加。",
        "",
        "## 记录",
        "",
        f"- 同一任务终态掩盖不同安全结果。旧 E4 格子目录有 {values['e4_after_cells']} 个 JSON。新 120 格在 `results/runtime/correctness/e4-repeat/summary.json`，`cells` 为 {values['e4_repeat_cells']}，目录计数 {values['e4_repeat_dir_cells']}。协议与报告在 `docs/开发文档/E4三问与重复批次.md`。两批不相加。",
        f"- 资格与版本保护不同。E9 报告在 `docs/开发文档/补实验结果.md`。M4 在 `docs/开发文档/M4-M6-v2结果与验收.md`。Coding v3 确认分母 {values['v3_assigned_confirm']}，其中触顶 {values['v3_budget_exceeded']}。容器补测评分了 {values['v3_patches_scored']} 份模型补丁，隐藏测试通过 {values['v3_hidden_passed']}。正式数字在 `results/runtime/correctness/v3-coding/rescore/summary.json`。",
        f"- 物理效果与回执不同。最终回归是 {values['regression_result']}。掉电是 {values['power_loss']}。记录在 `results/runtime/correctness/final-regression/summary.json`。E5/E6/E10 的原始叙述在 `docs/开发文档/补实验结果.md`，不回写。",
        "- 已授权效果可恢复。决定点测试列在最终回归的 `faults` 里。Coding v3 的 S3 恢复以 rescore 的 payload 摘要与冻结补丁摘要对照为准。",
        f"- 通知缺失仍有风险。M5 履约迁移 {values['m5_n']} 行，blocked {values['m5_blocked']}。故障集合是 {values['m5_faults']}。能力归属是 {values['m5_owners']}。同键一次接纳写在 `external_receiver` 名下。",
        f"- 版本保护有进展代价。v4 开发集可行性 {values['v4_dev_tasks']} 格，V1 合法后继 {values['v4_v1_legal']}，冻结 raise 无效 {values['v4_raise_invalid']}。镜像 `{values['v4_image']}`。开发集分配 {values['v4_dev_assigned']}，可评分初始补丁 {values['v4_dev_scorable']}。确认集分母 {values['v4_confirm_n']}，初始隐藏测试通过 {values['v4_confirm_initial_passed']}，后继隐藏测试通过 {values['v4_confirm_successor_passed']}。这是固定失败反馈下的修复：{values['v4_leak_tasks']} 个任务里 {values['v4_leak_extracted']} 个带有隐藏测试失败行，看到测试源码 diff 的是 {values['v4_leak_source']}，带断言期望的是 {values['v4_leak_assertion']}。开发集与确认集不相加，也不与 v3 相加，也不改写成严格隐藏评测。",
        "- 持久化成本只引用 E7/E8 当时的配置，见 `docs/开发文档/正确性修复与补充实验.md`。不与本轮回归的耗时比较。",
        "",
        "## 这轮对照",
        "",
        f"- Outbox：旧凭证 {values['outbox_stale']}，新凭证 {values['outbox_fresh']}，Claim 到字节 {values['outbox_claim_to_bytes']}，外部 exactly-once {values['outbox_external']}。文件是 `results/runtime/correctness/outbox-baseline/summary.json`。",
        f"- LangGraph 编码冒烟是 {values['m5_coding_status']}。没有新的模型工具轨迹，不记成跨 Runtime 的 Agent 工作流。文件是 `results/runtime/correctness/v2-m5-coding/summary.json`。",
        f"- M6 历史精度是 {values['m6_precision']}。H5 精确 McNemar 双侧 p={values['m6_h5_p']}。不把这个 p 值当成加任务的理由。",
        "",
        "## 不支持的说法",
        "",
        "- 没有验证 GPU 或 KV 资源释放。取消回执不是回收。",
        "- 不支持任意 HTTP exactly-once。Outbox 对照把这一项记成 false。",
        "- 没有测掉电。进程退出恢复、SQLite 事务恢复、文件替换可见性分开记录。",
        "- 没有证明跨任务普遍提升。六个确认任务即使全部完成，也不是跨项目结论。",
        "- 没有首次提出 stale 不等于 unsafe。近邻文献在 `docs/研究/Novelty-and-Related-Work.md`。",
        "- 没有首次提出任务结果不同于可验证证据。",
        "",
        "## 查找",
        "",
        "- 语义和保证：`docs/研究/执行语义与保证边界.md`。",
        "- 统计规则：`docs/研究/统计协议.md`。写于读取 v4 确认结果之前。",
        "- `7ac72dd` 的缺陷清单：`docs/开发文档/Runtime-正确性缺陷清单.md`。它是当时的快照，不描述当前树。",
        "- 冻结稿：`docs/现行/论文/稿件.md`。本轮不改。",
        "- 历史 GO：`docs/开发文档/M4-M6-v2结果与验收.md`。不重算。",
        "",
    ]) + "\n"


def main() -> None:
    values = facts()
    text = render(values)
    if OUT.exists():
        current = OUT.read_text(encoding="utf-8")
        for value in values.values():
            if value and value not in current:
                raise SystemExit(f"index is missing recomputed value {value}")
        # A hand edit that changes a recomputed token fails above. Extra prose is left in place only when every recomputed token is still present.
        if current != text:
            raise SystemExit("index text does not match the generator; do not hand-edit numbers")
    OUT.write_text(text, encoding="utf-8")
    print(json.dumps({"index": "ok", "tokens": len(values)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
