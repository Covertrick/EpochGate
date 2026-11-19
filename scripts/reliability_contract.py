"""M4 与 M5 共用的任务合同、接收端、故障注入和证据记录。

B2 与 B3 把业务字节交给同一个 SharedReceiver。
B2 在同一次决定里检查版本并接纳，不写 Claim。
B3 先提交 Claim，崩溃点在接收端之前，恢复只重放 Claim 中的字节。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.sink import EffectSink


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SharedReceiver:
    """受控效果接收端。接纳日志和业务文件是同一份，不随臂更换存储。"""

    def __init__(self, root: Path, relative: str) -> None:
        self.root = root
        self.relative = relative
        self.path = root / relative
        self.log: list[dict[str, Any]] = []

    def accept(self, *, effect_key: str, attempt_id: str, payload: bytes) -> dict[str, Any]:
        digest = sha256_bytes(payload)
        prior = [row for row in self.log if row["effect_key"] == effect_key and row["accepted"]]
        if prior:
            if prior[-1]["digest"] != digest:
                self.log.append({"effect_key": effect_key, "attempt_id": attempt_id, "accepted": False, "detail": "conflict", "digest": digest})
                return {"status": "conflict", "applied": False, "digest": digest}
            self.log.append({"effect_key": effect_key, "attempt_id": attempt_id, "accepted": False, "detail": "replay", "digest": digest})
            return {"status": "replayed", "applied": False, "digest": digest, "receipt": prior[-1]["receipt"]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(payload)
        receipt = sha256_text(f"{effect_key}:{digest}")
        self.log.append({
            "effect_key": effect_key,
            "attempt_id": attempt_id,
            "accepted": True,
            "detail": "accepted",
            "digest": digest,
            "receipt": receipt,
        })
        return {"status": "accepted", "applied": True, "digest": digest, "receipt": receipt}

    def accepted_count(self, effect_key: str | None = None) -> int:
        rows = self.log if effect_key is None else [row for row in self.log if row["effect_key"] == effect_key]
        return sum(1 for row in rows if row["accepted"])

    def physical(self) -> bytes | None:
        if not self.path.is_file():
            return None
        return self.path.read_bytes()


@dataclass
class Attempt:
    run_id: str
    attempt_id: str
    token: int
    version: int
    eligible: bool
    payload: bytes | None = None
    phase: str = "open"


@dataclass
class RunContext:
    root: Path
    run_id: str
    ledger: RunLedger
    sink: EffectSink
    receiver: SharedReceiver
    clock: list[dict[str, Any]] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    reads: int = 0
    injected: bool = False
    notice_mode: str = "missing"
    authority_updated: bool = False
    frozen_token: int = 1
    authority_kind: str = "supersede"
    claim: dict[str, Any] | None = None

    def mark(self, name: str) -> None:
        self.clock.append({"order": len(self.clock) + 1, "name": name, "t": time.perf_counter()})

    def close(self) -> None:
        self.ledger.close()
        self.sink.close()


def open_run(root: Path, run_id: str, relative: str) -> RunContext:
    root.mkdir(parents=True, exist_ok=True)
    ledger = RunLedger(root / "ledger.sqlite")
    sink = EffectSink(root / "sink.sqlite")
    ledger.create_run(run_id)
    ledger.advance(run_id, "running")
    state = ledger.get_run(run_id)
    ctx = RunContext(
        root=root,
        run_id=run_id,
        ledger=ledger,
        sink=sink,
        receiver=SharedReceiver(root, relative),
        frozen_token=state.fencing_token,
    )
    ctx.mark("run_started")
    return ctx


def observe_tool_call(ctx: RunContext, name: str, attempt_id: str, arguments: dict[str, Any]) -> None:
    ctx.tool_events.append({"tool": name, "attempt_id": attempt_id, "arguments": arguments, "t": time.perf_counter()})
    if name.startswith("read_"):
        ctx.reads += 1


def inject_world_change(ctx: RunContext, world: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    ctx.mark("world_changed")
    updated = json.loads(json.dumps(world))
    for key, value in event["set"].items():
        cursor = updated
        parts = key.split(".")
        for part in parts[:-1]:
            cursor = cursor[part]
        cursor[parts[-1]] = value
    ctx.injected = True
    return updated


def move_authority(ctx: RunContext) -> None:
    if ctx.authority_kind == "revoke":
        ctx.ledger.revoke(ctx.run_id)
    else:
        ctx.ledger.supersede(ctx.run_id, ctx.sink)
    ctx.mark("authority_updated")
    ctx.authority_updated = True


def deliver_notice(ctx: RunContext, mode: str, *, kind: str = "supersede") -> None:
    ctx.notice_mode = mode
    ctx.authority_kind = kind
    if mode == "missing":
        return
    ctx.mark("notification_emitted")
    if mode == "delivered":
        ctx.mark("notification_delivered")
        move_authority(ctx)


def commit_effect(ctx: RunContext, arm: str, *, effect_key: str, attempt_id: str, payload: bytes, interrupt: bool) -> dict[str, Any]:
    """按臂把同一份载荷交给同一个接收端。"""
    ctx.mark("commit_attempted")
    state = ctx.ledger.get_run(ctx.run_id)

    def stale() -> bool:
        current = ctx.ledger.get_run(ctx.run_id)
        return (not current.eligible) or current.fencing_token != ctx.frozen_token

    if arm == "B0":
        token_was_stale = stale()
        if interrupt:
            return {"status": "crashed_before_accept", "applied": False, "recovery": "NOT_SUPPORTED", "token_was_stale": token_was_stale}
        decision = ctx.receiver.accept(effect_key=effect_key, attempt_id=attempt_id, payload=payload)
        return {**decision, "recovery": "NOT_SUPPORTED", "token_was_stale": token_was_stale}
    if arm == "B1":
        admitted = bool(state.eligible)
        if ctx.notice_mode == "delayed":
            ctx.mark("notification_delivered")
            move_authority(ctx)
        token_was_stale = stale()
        if interrupt:
            return {"status": "crashed_before_accept", "applied": False, "admitted": admitted, "recovery": "NOT_SUPPORTED", "token_was_stale": token_was_stale}
        if not admitted:
            return {"status": "rejected", "applied": False, "recovery": "NOT_SUPPORTED", "token_was_stale": token_was_stale}
        decision = ctx.receiver.accept(effect_key=effect_key, attempt_id=attempt_id, payload=payload)
        return {**decision, "recovery": "NOT_SUPPORTED", "token_was_stale": token_was_stale}
    if arm == "B2":
        with ctx.ledger._lock:
            current = ctx.ledger.get_run(ctx.run_id)
            fresh = bool(current.eligible) and current.fencing_token == ctx.frozen_token
            if not fresh:
                return {"status": "rejected", "applied": False, "recovery": "NOT_SUPPORTED", "token_was_stale": True}
            decision = ctx.receiver.accept(effect_key=effect_key, attempt_id=attempt_id, payload=payload)
        if ctx.notice_mode == "delayed":
            ctx.mark("notification_delivered")
            move_authority(ctx)
        return {**decision, "recovery": "NOT_SUPPORTED", "token_was_stale": False}
    digest = sha256_bytes(payload)
    claim = ctx.ledger.claim_publish(ctx.run_id, effect_key, ctx.frozen_token, digest)
    ctx.claim = {"status": claim.get("status"), "digest": digest, "payload": payload, "effect_key": effect_key, "attempt_id": attempt_id}
    if claim.get("status") != "claimed":
        return {"status": "rejected", "applied": False, "claim": claim.get("status"), "recovery": "claim_rejected", "token_was_stale": True}
    if interrupt:
        return {"status": "crashed_before_accept", "applied": False, "claim": "claimed", "digest": digest, "recovery": "pending", "token_was_stale": False}
    decision = ctx.receiver.accept(effect_key=effect_key, attempt_id=attempt_id, payload=payload)
    return {**decision, "claim": "claimed", "recovery": "not_needed", "token_was_stale": False}


def resume_execution(ctx: RunContext) -> dict[str, Any]:
    """只重放已经写入 Claim 的字节。没有 Claim 时不编造。"""
    claim = ctx.claim
    if claim is None or claim.get("status") != "claimed":
        return {"status": "NOT_SUPPORTED", "applied": False}
    decision = ctx.receiver.accept(
        effect_key=claim["effect_key"],
        attempt_id=claim["attempt_id"],
        payload=claim["payload"],
    )
    physical = ctx.receiver.physical()
    if physical is None or sha256_bytes(physical) != claim["digest"]:
        return {"status": "unknown", "applied": False}
    return {**decision, "status": "completed" if decision.get("applied") or decision.get("status") == "replayed" else decision.get("status")}


def score_business(
    world: dict[str, Any],
    task: dict[str, Any],
    receiver: SharedReceiver,
    *,
    old_accepted: bool,
    old_digest: str | None,
    token_was_stale: bool,
    successor: bool,
) -> dict[str, Any]:
    physical = receiver.physical()
    legal_text = task["legal_payload"](world)
    legal = legal_text.encode("utf-8") if legal_text else b""
    legal_digest = sha256_bytes(legal) if legal else None
    terminal = (physical is None and not legal) or (physical == legal and bool(legal))
    stale = bool(old_accepted and token_was_stale)
    applicable = bool(legal) and task.get("successor_applicable", True)
    correct_stop = (not applicable) and physical is None and not old_accepted
    receipt_digest = next((row["digest"] for row in reversed(receiver.log) if row["accepted"]), None)
    return {
        "task_terminal_match": terminal,
        "stale_credential_accepted": stale,
        "successor_completed": bool(successor and terminal and applicable),
        "correct_stop": correct_stop,
        "wrong_physical": bool(physical is not None and (not legal or physical != legal)),
        "duplicate_effect": receiver.accepted_count() > 1,
        "verifiable_effect": bool(receipt_digest and physical is not None and sha256_bytes(physical) == receipt_digest),
        "physical_digest": None if physical is None else sha256_bytes(physical),
        "legal_digest": legal_digest,
        "old_digest": old_digest,
        "successor_applicable": applicable,
    }


def collect_evidence(ctx: RunContext, record: dict[str, Any]) -> dict[str, Any]:
    native = {
        "run_id": ctx.run_id,
        "timeline": ctx.clock,
        "tool_events": ctx.tool_events,
        "claim_phase": None if ctx.claim is None else ctx.claim.get("status"),
    }
    physical = ctx.receiver.physical()
    sink_layer = {
        "accept_log": ctx.receiver.log,
        "physical_digest": None if physical is None else sha256_bytes(physical),
        "accepted_count": ctx.receiver.accepted_count(),
    }
    claim_view: Any
    if ctx.claim is None and record.get("arm") != "B3":
        claim_view = "NOT_SUPPORTED"
    elif ctx.claim is None:
        claim_view = None
    else:
        claim_view = {key: value for key, value in ctx.claim.items() if key != "payload"}
        claim_view["payload_sha256"] = sha256_bytes(ctx.claim["payload"])
    evidence = {
        **record,
        "native": native,
        "receiver": sink_layer,
        "claim": claim_view,
    }
    if record.get("runtime") == "langgraph" and record.get("mode") == "R0":
        evidence["claim"] = "NOT_SUPPORTED"
    return evidence


Oracle = Callable[[dict[str, Any]], dict[str, Any]]


def oracle_from_artifacts(task: dict[str, Any], world: dict[str, Any], receiver: SharedReceiver, *, old_accepted: bool, old_digest: str | None, token_was_stale: bool, successor: bool, interrupted_recovered: bool | None) -> dict[str, Any]:
    scored = score_business(world, task, receiver, old_accepted=old_accepted, old_digest=old_digest, token_was_stale=token_was_stale, successor=successor)
    scored["interrupted_recovered"] = interrupted_recovered
    scored["reason"] = "persistent_business_state_and_receiver_log"
    return scored
