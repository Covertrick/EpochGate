"""从 trace 里解释一笔任务为什么被拦住。不访问模型。"""

from __future__ import annotations

import json
from pathlib import Path


def explain_task(path: str | Path, task_id: str) -> list[str]:
    """按任务 id 抽出三类资格事件。

    输入：JSONL 路径和任务 id。
    输出：每个人类可读的标签行。没有匹配事件时返回一行说明。
    副作用：只读文件。
    失败：文件无法读取时向上抛。
    """
    lines: list[str] = []
    for event in _load(path):
        identity = event.get("identity") or {}
        if identity.get("task_id") != task_id and identity.get("source_id") != task_id:
            if identity.get("task_id") not in {None, "", task_id}:
                continue
        kind = str(event.get("event_type") or "")
        reason = str(event.get("reason") or "")
        if kind == "source_dirty":
            lines.append(f"stale_observation source={identity.get('source_id') or task_id} reason={reason}")
        elif kind == "action_rejected":
            lines.append(f"late_output_rejected reason={reason}")
        elif kind == "cancel_requested":
            lines.append(f"eligibility_revoked reason={reason}")
    if not lines:
        return [f"task={task_id} has no stale_observation, late_output_rejected, or eligibility_revoked"]
    return [f"task={task_id}", *lines]


def _load(path: str | Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
