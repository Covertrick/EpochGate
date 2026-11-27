"""从账本事件得出一次运行能指出来的五件事。页面只渲染这里的结果。"""

from __future__ import annotations

from typing import Any

from epochgate_runtime.ledger import RunLedger


def _absent() -> dict[str, Any]:
    return {"status": "absent"}


def observe_events(events: list[dict[str, Any]], *, slot_released: bool) -> dict[str, Any]:
    return {
        "fencing": _fencing(events),
        "idempotency": _idempotency(events),
        "reconcile": _reconcile(events),
        "slot": _slot(events, slot_released),
        "container_publish": _container_publish(events),
    }


def observe_run(ledger: RunLedger, run_id: str) -> dict[str, Any]:
    state = ledger.get_run(run_id)
    return observe_events(ledger.events(run_id), slot_released=state.cancel.slot_released)


def _fencing(events: list[dict[str, Any]]) -> dict[str, Any]:
    superseded = [item for item in events if item["event_type"] == "eligibility_superseded"]
    rejected = [item for item in events if item["event_type"] in {"result_withheld", "commit_rejected"}]
    if not superseded or not rejected:
        return _absent()
    new_token = int(superseded[-1]["payload"].get("fencing_token") or 0)
    return {
        "status": "seen",
        "old_token": new_token - 1 if new_token else None,
        "new_token": new_token,
        "rejected_event": rejected[-1]["event_type"],
    }


def _idempotency(events: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = [item for item in events if item["event_type"] == "publish_outcome"]
    if not outcomes:
        return _absent()
    payload = outcomes[-1]["payload"]
    status = str(payload.get("status") or "")
    if status not in {"conflict", "replayed"}:
        return _absent()
    return {"status": "seen", "result": status, "unchanged": bool(payload.get("unchanged"))}


def _reconcile(events: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = [item["payload"].get("decision") for item in events if item["event_type"] == "reconcile_decision"]
    if "confirmed_from_sink" in decisions and "replay" in decisions:
        return {"status": "seen", "first": "confirmed_from_sink", "second": "replay"}
    confirmed = [item for item in events if item["event_type"] == "reconciled_from_sink"]
    if confirmed and "replay" in decisions:
        return {"status": "seen", "first": "confirmed_from_sink", "second": "replay"}
    return _absent()


def _slot(events: list[dict[str, Any]], slot_released: bool) -> dict[str, Any]:
    cancel = next((item for item in events if item["event_type"] == "cancel_requested"), None)
    exited = next((item for item in events if item["event_type"] == "process_exited"), None)
    if cancel is None:
        return _absent()
    released_after_exit = bool(exited is not None and slot_released and int(exited["seq"]) > int(cancel["seq"]))
    if not released_after_exit:
        return {
            "status": "seen",
            "cancel_requested": True,
            "slot_released": bool(slot_released),
            "released_after_exit": False,
        }
    return {
        "status": "seen",
        "cancel_requested": True,
        "slot_released": True,
        "released_after_exit": True,
    }


def _container_publish(events: list[dict[str, Any]]) -> dict[str, Any]:
    published = [
        item for item in events
        if item["event_type"] == "file_published"
        and item["payload"].get("receipt_verified") is True
        and str(item["payload"].get("receipt") or "").startswith("container:")
    ]
    if not published:
        return _absent()
    payload = published[-1]["payload"]
    receipt = str(payload.get("receipt") or "")
    parts = receipt.split(":")
    container = parts[1] if len(parts) >= 3 else ""
    return {
        "status": "seen",
        "container": container,
        "path": payload.get("path"),
        "receipt": receipt,
        "receipt_verified": True,
    }
