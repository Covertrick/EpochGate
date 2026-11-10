"""一次 Run 是否完成，只看它承诺的效果有没有被核对过。"""

from __future__ import annotations

from typing import Any


def evaluate_coding_run(
    events: list[dict[str, Any]],
    *,
    run_id: str,
    expected_path: str,
    baseline_failed: bool,
    scorer_passed: bool,
) -> dict[str, bool]:
    published = []
    for event in events:
        if event.get("run_id", run_id) != run_id:
            continue
        kind = event.get("event_type") or event.get("type")
        if kind != "file_published":
            continue
        payload = event.get("payload") or {}
        path = payload.get("path", event.get("path"))
        verified = payload.get("receipt_verified", event.get("receipt_verified")) is True
        if path == expected_path and verified:
            published.append(event)
    chain_completed = bool(published)
    return {
        "chain_completed": chain_completed,
        "repair_demonstrated": chain_completed and baseline_failed and scorer_passed,
    }
