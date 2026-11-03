"""只读审计 v1.8 队列 JSONL。不改原始文件。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

SOURCE = Path("results/experiments/v1_3/queue/batches.jsonl")
OUT = Path("results/experiments/v2_1/audit/v18_bystander_audit.json")


def main() -> None:
    """统计 v1.8 行里有没有旁观者角色和槽位接收者。

    输入：无。读取 ``batches.jsonl``。
    输出：审计 JSON。
    副作用：只创建审计文件。
    失败：源文件缺失时抛出 ``FileNotFoundError``。
    """
    rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    keys = set().union(*(row.keys() for row in rows)) if rows else set()
    arms = Counter(row.get("arm") for row in rows)
    orders = Counter(row.get("scheduler_order") for row in rows)
    loads = Counter(row.get("queue_pressure_label") for row in rows)
    report = {
        "source": str(SOURCE).replace("\\", "/"),
        "n_rows": len(rows),
        "arms": dict(arms),
        "scheduler_order": dict(orders),
        "queue_pressure_label": dict(loads),
        "has_workflow_role": "workflow_role" in keys,
        "has_slot_reacquired_by": "slot_reacquired_by" in keys,
        "has_first_queue_wait_s": "first_queue_wait_s" in keys,
        "rename_to_bystander_p95": False,
        "reason": "行里有后续任务的 first_queue_wait_s，没有 workflow_role，也没有 slot_reacquired_by。不能把 v1.8 的后续等待改名为旁观者 P95。",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
