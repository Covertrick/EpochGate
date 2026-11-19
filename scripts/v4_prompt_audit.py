"""审计 v4 已经落盘的模型输入。不改 trace，不把 4/6 改写成严格隐藏评测。"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V4 = ROOT / "results" / "runtime" / "correctness" / "v4-coding"
OUT_JSON = V4 / "prompt-audit.json"
OUT_MD = ROOT / "docs" / "开发文档" / "v4提示泄漏审计.md"
QUOTE = re.compile(r"[\"']([^\"']{3,120})[\"']")


def traces() -> list[Path]:
    return sorted(V4.glob("**/trace.json"))


def quoted_in_patch(symptom: str, patch: str) -> list[str]:
    found = []
    for match in QUOTE.findall(symptom or ""):
        if match in patch and match not in found:
            found.append(match)
    return found


def classify(card: dict, test_patch: str) -> dict:
    problem = card.get("problem") or ""
    expected = card.get("expected") or ""
    symptom = card.get("symptom") or ""
    blob = "\n".join((problem, expected, symptom))
    source_hit = "diff --git" in blob or bool(test_patch[:80] and test_patch[:80] in blob)
    assertion = any(token in symptom for token in ("AssertionError", "assert ", " != ", " == ", "does not match"))
    exception = any(token in symptom for token in ("Error", "Exception", "Traceback"))
    quotes = quoted_in_patch(symptom, test_patch)
    commit_overlap = quoted_in_patch(problem + "\n" + expected, test_patch)
    return {
        "symptom_status": card.get("symptom_status"),
        "saw_hidden_test_source": source_hit,
        "saw_assertion_expectation": assertion,
        "saw_exception_or_failure_text": bool(symptom) and (exception or " failed" in symptom),
        "quoted_strings_also_in_hidden_patch": quotes,
        "commit_message_quotes_hidden_patch": commit_overlap,
        "protocol_allows_failure_lines": True,
        "task_text_source": "git log -1 of the fix commit, not a rewrite of the hidden test",
        "symptom_source": "visible_symptom applies test_patch on base and keeps pytest E lines",
    }


def main() -> None:
    frozen = json.loads((V4 / "frozen-tasks.json").read_text(encoding="utf-8"))
    patches = {}
    from coding_v3 import POOL, git
    for row in frozen["dev"] + frozen["confirm"]:
        repo = POOL / row["repo"]
        patches[row["task_id"]] = git(repo, "diff", row["base"], row["fix"], "--", *row["tests"]).stdout
    rows = []
    for path in traces():
        trace = json.loads(path.read_text(encoding="utf-8"))
        card = trace.get("card") or {}
        task_id = trace.get("task_id") or card.get("task_id")
        phase = "confirm" if "confirm" in path.parts else "dev"
        kind = path.parent.name
        rows.append({
            "phase": phase,
            "kind": kind,
            "task_id": task_id,
            "label": trace.get("label"),
            **classify(card, patches.get(task_id, "")),
        })
    by_task = {}
    for row in rows:
        by_task.setdefault((row["phase"], row["task_id"]), row)
    tasks = list(by_task.values())
    summary = {
        "traces": len(rows),
        "tasks": len(tasks),
        "symptom_extracted": sum(1 for row in tasks if row["symptom_status"] == "extracted"),
        "saw_hidden_test_source": sum(1 for row in tasks if row["saw_hidden_test_source"]),
        "saw_assertion_expectation": sum(1 for row in tasks if row["saw_assertion_expectation"]),
        "saw_exception_or_failure_text": sum(1 for row in tasks if row["saw_exception_or_failure_text"]),
        "quoted_overlap": sum(1 for row in tasks if row["quoted_strings_also_in_hidden_patch"]),
        "label": "FIXED_FAILURE_FEEDBACK",
        "not_strict_hidden_evaluation": True,
        "historical_results_kept": True,
    }
    OUT_JSON.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# v4 提示泄漏审计",
        "",
        "范围是 v4 已落盘的 `trace.json`。本文件不改那些记录，也不把确认集初始通过改写成严格隐藏评测。",
        "",
        "协议 `docs/开发文档/Coding-v4协议.md` 写明用户消息包含评分器摘出的失败行。代码路径是 `coding_v3.visible_symptom`：先把隐藏测试补丁打到 base 上，再跑 pytest，把以 `E ` 开头的行放进 `card.symptom`。`user_prompt` 原样放进模型输入。问题描述来自修复提交的 `git log -1`，不是隐藏测试 diff 的改写。卡片生成时只拦截 `diff --git` 和测试补丁开头的 60 个字符，不拦截断言期望值。",
        "",
        f"轨迹 {summary['traces']} 条，任务 {summary['tasks']} 个。失败行状态为 extracted 的任务：{summary['symptom_extracted']}。看到隐藏测试源码 diff 的任务：{summary['saw_hidden_test_source']}。失败行里带断言期望的任务：{summary['saw_assertion_expectation']}。失败行里带异常或失败计数的任务：{summary['saw_exception_or_failure_text']}。失败行中的引号字符串也出现在隐藏测试补丁里的任务：{summary['quoted_overlap']}。",
        "",
        "这些信息被 v4 协议明确允许。因此 v4 是“提供预先固定的隐藏测试失败反馈之后的修复实验”，不是严格隐藏评测。确认集原有通过数保留在 `confirm/summary.json`。严格隔离的确认放在另一批，不回写这一批。",
        "",
    ]
    for row in rows:
        lines.append(
            f"- `{row['phase']}/{row['task_id']}/{row['kind']}`：源码 diff {str(row['saw_hidden_test_source']).lower()}，"
            f"断言期望 {str(row['saw_assertion_expectation']).lower()}，异常或失败文本 {str(row['saw_exception_or_failure_text']).lower()}，"
            f"引号与隐藏补丁重合 {len(row['quoted_strings_also_in_hidden_patch'])}，"
            f"提交说明与隐藏补丁重合 {len(row['commit_message_quotes_hidden_patch'])}。协议允许失败行：true。"
        )
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
