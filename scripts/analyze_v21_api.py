"""核对真实 API 事件链。不计算旁观者 P95。"""

from __future__ import annotations

import json
from pathlib import Path

SOURCE = Path("results/experiments/v2_1/api/events.jsonl")
OUT = Path("results/experiments/v2_1/reports/v21_api_mechanism.json")


def main() -> None:
    """汇总第 8.3 节的事件顺序。

    输入：无。
    输出：机制 JSON。
    副作用：写出报告。
    失败：源文件缺失时抛出 ``FileNotFoundError``。
    """
    rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    focal = [row for row in rows if row.get("task_id") == "t0"]
    other = {(row.get("trigger_class"), row.get("policy"), row.get("seed")): row for row in rows if row.get("task_id") == "t1"}
    summary = []
    for row in focal:
        key = (row.get("trigger_class"), row.get("policy"), row.get("seed"))
        follower = other.get(key, {})
        revoked = row.get("eligibility_revoked_at")
        stopped = row.get("local_read_stopped_at")
        released = row.get("old_attempt_slot_release_at")
        acquired = follower.get("request_started_at") or follower.get("slot_acquired")
        order_ok = None
        if row.get("policy") == "G2" and row.get("trigger_class") == "harmful" and revoked and stopped and released:
            order_ok = revoked <= stopped <= released
        summary.append(
            {
                "trigger": row.get("trigger_class"),
                "policy": row.get("policy"),
                "seed": row.get("seed"),
                "error": bool(row.get("transport_error") or row.get("error")),
                "old_attempt_committed": bool(row.get("old_attempt_committed")),
                "false_cancel": bool(row.get("false_cancel")),
                "provider_abort_not_issued": row.get("provider_abort_not_issued"),
                "order_revoked_then_stopped_then_released": order_ok,
                "follower_slot_acquired": bool(follower.get("slot_acquired")),
                "acquired_after_release": bool(acquired and released and acquired >= released) if acquired and released else None,
            }
        )
    report = {
        "n_focal": len(focal),
        "errors": sum(1 for item in summary if item["error"]),
        "old_commits": sum(1 for item in summary if item["old_attempt_committed"]),
        "harmless_g1_false_cancel": sum(1 for item in summary if item["trigger"] == "harmless" and item["policy"] == "G1" and item["false_cancel"] and not item["error"]),
        "harmless_g2_false_cancel": sum(1 for item in summary if item["trigger"] == "harmless" and item["policy"] == "G2" and item["false_cancel"]),
        "harmful_g2_order_ok": sum(1 for item in summary if item["trigger"] == "harmful" and item["policy"] == "G2" and item["order_revoked_then_stopped_then_released"]),
        "provider_abort_not_issued_false": sum(1 for item in summary if item["provider_abort_not_issued"] is False),
        "rows": summary,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()