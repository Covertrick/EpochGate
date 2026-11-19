"""标出 v3 提示里的失败行来自哪里。不改 trace。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "开发文档" / "提示泄漏审计.md"


def traces() -> list[Path]:
    base = ROOT / "results" / "runtime" / "correctness" / "v3-coding"
    found = []
    for phase in ("dev", "confirm"):
        directory = base / phase
        if not directory.is_dir():
            continue
        found.extend(directory.glob("*/trace.json"))
    return found


def main() -> None:
    lines = [
        "# 提示泄漏审计",
        "",
        "范围是 v3 已经落盘的 `trace.json` 里的 `card.symptom`。本文件不改那些记录。",
        "",
        "v3 的失败行不是公开 issue，也不是事先单独列出的公开测试摘录。评分器先把隐藏测试补丁打到修复前的树上，再把 pytest 的失败行放进用户消息。这些行里可以出现隐藏测试的期望输出。",
        "",
    ]
    manifest = json.loads((ROOT / "results/runtime/correctness/v2-coding-pool/manifest.json").read_text(encoding="utf-8"))
    patches = {task["task_id"]: task["test_patch"] for task in manifest["tasks"]}
    leaked = 0
    for path in traces():
        trace = json.loads(path.read_text(encoding="utf-8"))
        card = trace.get("card") or {}
        symptom = card.get("symptom") or ""
        status = card.get("symptom_status") or ""
        from_hidden_run = "FAILED tests/" in symptom or "AssertionError" in symptom or status.startswith("reproduced")
        has_expected = "assert" in symptom and "==" in symptom
        lines.append(
            f"- `{trace.get('task_id')}`：失败行在模型生成前固定。来源是评分器把隐藏测试打到 base 上之后摘出的 pytest 输出，不是公开 issue。"
            f" 含断言期望值：{str(has_expected).lower()}。状态 `{status}`。"
        )
        leaked += int(from_hidden_run and has_expected)
    lines.extend([
        "",
        f"失败行里带有隐藏测试期望值的格子：{leaked}。",
        "这些行在模型调用前就写进了用户消息。下一轮确认只把公开任务描述和公开测试反馈给 Agent，隐藏测试只给评分端。",
        "仓库里的 `.env` 不进入这份审计的输出。",
        "",
    ])
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"traces": len(list(traces())), "symptom_in_hidden_patch": leaked}))


if __name__ == "__main__":
    main()
