"""M0-R 的研究适用性和工程启动门。不改写旧的六项准入。"""

from __future__ import annotations

from typing import Any


def research_suitability(report: dict[str, Any]) -> dict[str, Any]:
    shared = list(report.get("shared_files") or [])
    post = report.get("linux_post_state") or {}
    pytest_passed = post.get("pytest_started") is True and post.get("exit_code") == 0
    cells = {
        "base": "not_run",
        "base_plus_gold": "not_run",
        "base_plus_change": "passed" if pytest_passed else "not_run",
        "base_plus_change_plus_valid_fix": "not_run",
    }
    discriminates = False
    reason = (
        "旧六项准入保持不变。附近提交没有改 gold 文件，已有的 Linux pytest 跑在变更后的树上。"
        "这次审计没有同时看到失败的基线和通过的参考修复，所以还不能把测试当作能区分对错的评分。"
    )
    if shared:
        reason = "附近提交碰到了 gold 文件。变更后可区分性仍要单独的四格结果，不能用文件交集代替。"
    return {
        "case_id": report.get("case_id"),
        "historical_admission_unchanged": True,
        "old_admitted": bool((report.get("admission") or {}).get("admitted")),
        "nearby_change_touches_gold_files": bool(shared),
        "task_relevance": "unknown",
        "evidence_invalidation": "unknown",
        "refresh_requirement": "unknown",
        "world_effect": "unknown",
        "information": "insufficient",
        "cells": cells,
        "discriminates_wrong_from_right": discriminates,
        "research_suitability": "limited",
        "reason": reason,
    }


def engineering_gate(records: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [item for item in records if item.get("m0r_status") == "complete"]
    repos = sorted({item.get("repo", "") for item in complete if item.get("repo")})
    passed = len(complete) >= 5 and len(repos) >= 2
    counts: dict[str, int] = {}
    for item in records:
        status = str(item.get("m0r_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "data_gate": "pass" if passed else "stop",
        "complete_pairs": len(complete),
        "repositories": repos,
        "pool_size": len(records),
        "status_counts": counts,
        "paper_sample_frozen": False,
        "reason": (
            "工程启动门要求至少 5 条完整事件对，并且来自至少 2 个仓库。"
            if not passed
            else "工程启动门通过。论文样本量另冻。"
        ),
    }
