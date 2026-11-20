"""Execute one frozen proposal on one path at a time.

A run that already reached RUN_TERMINAL is never overwritten. A RUN_STARTED
without a terminal event is marked RUN_ABANDONED and a later retry gets a new
run_id.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import copy
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from frozen_proposals.db import authority_log, connect, init_schema, pragma_snapshot
from frozen_proposals.fairness import check_fairness
from frozen_proposals.faults import FaultController, FaultCut
from frozen_proposals.freeze_artifacts import CLUSTERS, PATH_NAMES, SMOKE_IDS, freeze
from frozen_proposals.hashing import canonical_bytes, environment_hash, git_head, implementation_hash, sha256_bytes, sha256_file, sha256_obj
from frozen_proposals.oracles import (
    ORACLE_VERSIONS,
    Observation,
    adjudicate,
    authority_oracle,
    effect_history_adjudicated,
    evidence_oracle,
    physical_effect_state,
    recovery_state,
    semantics_oracle,
    successor_oracle,
    terminal_oracle,
)
from frozen_proposals.paths import path_factories
from frozen_proposals.predicate import PREDICATE_VERSION
from frozen_proposals.receiver_adapter import RECEIVER_IMPL, RECEIVER_VERSION, RecordingReceiver

RECOVERABLE = {"after_commit_before_send", "after_send_before_receipt", "after_receipt_before_ack"}


@dataclass(frozen=True)
class EffectRequest:
    proposal_id: str
    task_id: str
    scenario_id: str
    payload: bytes
    payload_sha256: str
    idempotency_key: str
    outbox_event_id: str
    authority_token: int
    authority_epoch: int
    task_semantic_version: str
    timeout_ms: int
    budget_units: int


class Clock:
    def __init__(self) -> None:
        self.seq = 0

    def tick(self, _name: str) -> int:
        self.seq += 1
        return self.seq


@dataclass
class RunCtx:
    request: EffectRequest
    world: dict[str, Any]
    auth: dict[str, Any]
    conn: Any
    receiver: RecordingReceiver
    faults: FaultController
    clock: Clock
    run_id: str
    attempt_id: str
    withheld: bool = False
    local_ack: bool = False
    observed_receipt: dict[str, Any] | None = None
    recovery_attempted: bool = False
    recovery_bytes_sha256: str | None = None
    crash_point: str | None = None
    foreign_payload: bool = False
    latency_ms: float = 0.0
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


def _ordered_paths(index: int) -> list[str]:
    shift = index % len(PATH_NAMES)
    return PATH_NAMES[shift:] + PATH_NAMES[:shift]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _load_registry(root: Path) -> list[dict[str, Any]]:
    records = load_jsonl(root / "proposal_registry.jsonl")
    stored = (root / "freeze_manifest.sha256").read_text(encoding="utf-8").strip()
    actual = sha256_bytes(canonical_bytes(records))
    if actual != stored:
        raise RuntimeError("proposal registry hash does not match freeze_manifest.sha256")
    return records


def _finished(root: Path) -> set[tuple[str, str, str]]:
    keys = set()
    for row in load_jsonl(root / "run_manifest.jsonl"):
        if row.get("runner_status") in {"COMPLETED", "CRASHED", "INVALID", "TIMEOUT", "NOT_APPLICABLE", "BLOCKED"}:
            keys.add((str(row.get("cohort")), str(row.get("proposal_id")), str(row.get("path"))))
    return keys


def _known_run_ids(root: Path) -> set[str]:
    ids = set()
    for row in load_jsonl(root / "run_events.jsonl"):
        if row.get("run_id"):
            ids.add(str(row["run_id"]))
    for row in load_jsonl(root / "run_manifest.jsonl"):
        if row.get("run_id"):
            ids.add(str(row["run_id"]))
    return ids


def abandon_incomplete(root: Path) -> list[str]:
    events = load_jsonl(root / "run_events.jsonl")
    started: dict[str, dict[str, Any]] = {}
    closed: set[str] = set()
    for event in events:
        run_id = str(event.get("run_id") or "")
        if event.get("event") == "RUN_STARTED" and run_id:
            started[run_id] = event
        if event.get("event") in {"RUN_TERMINAL", "RUN_ABANDONED"} and run_id:
            closed.add(run_id)
    abandoned = []
    for run_id, event in started.items():
        if run_id in closed:
            continue
        append_jsonl(
            root / "run_events.jsonl",
            {
                "event": "RUN_ABANDONED",
                "run_id": run_id,
                "cohort": event.get("cohort"),
                "proposal_id": event.get("proposal_id"),
                "path": event.get("path"),
                "reason": "process ended after RUN_STARTED without RUN_TERMINAL",
            },
        )
        append_jsonl(
            root / "run_manifest.jsonl",
            {
                "run_id": run_id,
                "cohort": event.get("cohort"),
                "proposal_id": event.get("proposal_id"),
                "path": event.get("path"),
                "scenario_id": event.get("scenario_id"),
                "runner_status": "ABANDONED",
                "joint_observed": False,
                "adjudication": "UNRESOLVED",
                "adjudication_basis": "runner_ABANDONED",
                "unobserved_reason": "abandoned_after_RUN_STARTED",
                "common_core": event.get("common_core"),
                "mechanism_specific": event.get("mechanism_role") == "MECHANISM_SPECIFIC_DIAGNOSTIC",
            },
        )
        abandoned.append(run_id)
    return abandoned


def _try_recover(path: Any, ctx: RunCtx, point: str) -> bool:
    if point not in RECOVERABLE or not path.supports_recovery or not path.has_durable(ctx):
        return False
    blob = path.blob(ctx)
    if sha256_bytes(blob) != ctx.request.payload_sha256:
        ctx.foreign_payload = True
        return False
    ctx.recovery_attempted = True
    ctx.recovery_bytes_sha256 = sha256_bytes(blob)
    if ctx.observed_receipt is None:
        path.mark_sending(ctx)
        ctx.observed_receipt = ctx.receiver.accept(
            effect_key=ctx.request.idempotency_key,
            attempt_id=ctx.attempt_id + ":recover",
            payload=blob,
            authority_token=ctx.request.authority_token,
            authority_epoch=ctx.request.authority_epoch,
            run_id=ctx.run_id,
        )
    path.acknowledge(ctx, ctx.observed_receipt)
    return True


def _deliver(path: Any, ctx: RunCtx) -> str:
    ctx.faults.hit("before_send")
    if ctx.receiver.fail_next:
        ctx.receiver.note_failure(
            effect_key=ctx.request.idempotency_key,
            attempt_id=ctx.attempt_id,
            payload=ctx.request.payload,
            authority_token=ctx.request.authority_token,
            authority_epoch=ctx.request.authority_epoch,
            run_id=ctx.run_id,
        )
        return "COMPLETED"
    blob = path.blob(ctx)
    if sha256_bytes(blob) != ctx.request.payload_sha256:
        ctx.foreign_payload = True
        return "INVALID"
    path.mark_sending(ctx)
    result = ctx.receiver.accept(
        effect_key=ctx.request.idempotency_key,
        attempt_id=ctx.attempt_id,
        payload=blob,
        authority_token=ctx.request.authority_token,
        authority_epoch=ctx.request.authority_epoch,
        run_id=ctx.run_id,
    )
    try:
        ctx.faults.hit("after_send_before_receipt")
        ctx.observed_receipt = result
        ctx.faults.hit("after_receipt_before_ack")
        path.acknowledge(ctx, result)
        ctx.faults.hit("after_local_ack")
    except FaultCut as cut:
        ctx.crash_point = cut.point
        if _try_recover(path, ctx, cut.point):
            return "COMPLETED"
        raise
    return "COMPLETED"


def _execute_inner(path: Any, ctx: RunCtx) -> str:
    ctx.faults.hit("before_effect")
    ctx.faults.hit("authority_gate")
    try:
        proceed = path.authorize(ctx)
    except FaultCut as cut:
        ctx.crash_point = cut.point
        if _try_recover(path, ctx, cut.point):
            return "COMPLETED"
        raise
    ctx.faults.hit("after_admit_before_send")
    if not proceed:
        ctx.withheld = True
        return "COMPLETED"
    return _deliver(path, ctx)


def _execute(path: Any, ctx: RunCtx) -> str:
    started = time.perf_counter()
    try:
        status = _execute_inner(path, ctx)
    except FaultCut as cut:
        ctx.crash_point = cut.point
        status = "CRASHED"
    except Exception as exc:
        ctx.error = f"{type(exc).__name__}: {exc}"
        status = "CRASHED"
    ctx.latency_ms = (time.perf_counter() - started) * 1000.0
    if ctx.latency_ms > ctx.request.timeout_ms and status not in {"INVALID", "CRASHED"}:
        return "TIMEOUT"
    return status


def _table_record(conn: Any, sql: str, key: str) -> dict[str, Any] | None:
    row = conn.execute(sql, (key,)).fetchone()
    if row is None:
        return None
    return {
        "payload_sha256": row["payload_sha256"],
        "status": row["status"],
        "receipt_id": row["receipt_id"],
    }


def _classify(status: str, ctx: RunCtx, proposal: dict[str, Any], payload_file: Path) -> tuple[str, str | None]:
    if ctx.foreign_payload or ctx.receiver.foreign_payload:
        return "INVALID", "foreign_payload"
    if sha256_bytes(ctx.request.payload) != proposal["payload_sha256"]:
        return "INVALID", "proposal_mutated"
    if sha256_file(payload_file) != proposal["payload_sha256"]:
        return "INVALID", "proposal_file_mutated"
    if sha256_obj(ctx.world) != proposal["world_after_hash"]:
        return "INVALID", "world_after_mismatch"
    if sha256_obj(ctx.auth) != proposal["authority_after_hash"]:
        return "INVALID", "authority_after_mismatch"
    return status, None


def _observation(ctx: RunCtx, proposal: dict[str, Any]) -> Observation:
    physical = ctx.receiver.physical()
    return Observation(
        run_id=ctx.run_id,
        proposal_id=proposal["proposal_id"],
        proposal_sha256=proposal["payload_sha256"],
        predicate_version=proposal["task_semantic_version"],
        scoring_world=copy.deepcopy(proposal["world_after"]),
        physical_bytes=physical,
        receiver_log=list(ctx.receiver.log),
        receiver_provenance_missing=False,
        authority_log=authority_log(ctx.conn),
        claim_record=_table_record(
            ctx.conn,
            "SELECT payload_sha256, status, receipt_id FROM byte_claims WHERE idempotency_key = ?",
            ctx.request.idempotency_key,
        ),
        outbox_record=_table_record(
            ctx.conn,
            "SELECT payload_sha256, status, receipt_id FROM outbox WHERE event_id = ?",
            ctx.request.outbox_event_id,
        ),
        recovery_attempted=ctx.recovery_attempted,
        recovery_bytes_sha256=ctx.recovery_bytes_sha256,
        successor_obligation=proposal["successor_obligation"],
        stop_condition=proposal["stop_condition"],
        withheld=ctx.withheld,
        duplicate_effect=ctx.receiver.accepted_count(ctx.request.idempotency_key) > 1,
        outdated_scenario_label=proposal["outdated_scenario_label"],
    )


def _score(obs: Observation, runner_status: str, invalid_reason: str | None, hash_ok: bool) -> dict[str, Any]:
    terminal = terminal_oracle(obs)
    authority = authority_oracle(obs)
    physical = physical_effect_state(obs)
    evidence = evidence_oracle(obs)
    semantic = semantics_oracle(obs)
    successor = successor_oracle(obs)
    recovery = recovery_state(obs)
    adjudication, basis = adjudicate(
        terminal=terminal,
        authority=authority,
        physical=physical,
        evidence=evidence,
        duplicate_effect=obs.duplicate_effect,
        runner_status=runner_status,
    )
    history = effect_history_adjudicated(authority, physical, evidence)
    joint = (
        invalid_reason is None
        and hash_ok
        and runner_status in {"COMPLETED", "CRASHED"}
        and terminal in {"MATCH", "MISMATCH"}
        and history
    )
    return {
        "terminal_outcome": terminal,
        "authority_decision": authority,
        "physical_effect_state": physical,
        "evidence_state": evidence,
        "semantic_validity": semantic,
        "recovery_state": recovery,
        "successor_obligation": successor["obligation"],
        "successor_status": successor["status"],
        "week1_successor_obligation": successor["week1_successor_obligation"],
        "week1_successor_status": successor["week1_successor_status"],
        "stop_correctness": successor["week1_stop_correctness"],
        "successor_oracle_status": successor["status"],
        "adjudication": adjudication,
        "adjudication_basis": basis,
        "joint_observed": joint,
        "terminal_adjudicated": terminal in {"MATCH", "MISMATCH"},
        "effect_history_adjudicated": history,
        "duplicate_effect": obs.duplicate_effect,
    }


def _not_applicable_score() -> dict[str, Any]:
    return {
        "terminal_outcome": "UNKNOWN",
        "authority_decision": "UNKNOWN",
        "physical_effect_state": "UNKNOWN",
        "evidence_state": "UNKNOWN",
        "semantic_validity": "UNKNOWN",
        "recovery_state": "NOT_APPLICABLE",
        "successor_obligation": "UNKNOWN",
        "successor_status": "UNKNOWN",
        "week1_successor_obligation": "UNKNOWN",
        "week1_successor_status": "UNKNOWN",
        "stop_correctness": "UNKNOWN",
        "successor_oracle_status": "UNKNOWN",
        "adjudication": "UNRESOLVED",
        "adjudication_basis": "runner_NOT_APPLICABLE",
        "joint_observed": False,
        "terminal_adjudicated": False,
        "effect_history_adjudicated": False,
        "duplicate_effect": False,
    }


def _base_row(proposal: dict[str, Any], cohort: str, path_name: str, run_id: str, order_index: int) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "cohort": cohort,
        "proposal_id": proposal["proposal_id"],
        "scenario_id": proposal["scenario_id"],
        "task_id": proposal["task_id"],
        "path": path_name,
        "path_order_index": order_index,
        "seed": proposal["seed"],
        "payload_sha256": proposal["payload_sha256"],
        "task_semantic_version": proposal["task_semantic_version"],
        "world_before_hash": proposal["world_before_hash"],
        "world_after_hash_manifest": proposal["world_after_hash"],
        "fault_schedule_hash": proposal["fault_schedule_hash"],
        "authority_initial_hash": proposal["authority_initial_hash"],
        "receiver_impl": RECEIVER_IMPL,
        "receiver_version": RECEIVER_VERSION,
        "oracle_versions_canonical": json.dumps(ORACLE_VERSIONS, sort_keys=True, separators=(",", ":")),
        "timeout_ms": proposal["timeout_ms"],
        "budget_units": proposal["budget_units"],
        "code_commit": proposal["code_commit"],
        "environment_hash": proposal["environment_hash"],
        "implementation_hash": proposal["implementation_hash"],
        "common_core": proposal["common_core"],
        "mechanism_role": proposal["mechanism_role"],
        "mechanism_specific": proposal["mechanism_role"] == "MECHANISM_SPECIFIC_DIAGNOSTIC",
        "outdated_scenario_label": proposal["outdated_scenario_label"],
        "scenario_successor_obligation": proposal["successor_obligation"],
        "stop_condition": proposal["stop_condition"],
    }


def _write_terminal(root: Path, row: dict[str, Any], scores: dict[str, Any]) -> None:
    append_jsonl(root / "run_manifest.jsonl", row)
    for name in ("terminal_outcome", "authority_decision", "evidence_state", "semantic_validity", "successor_status"):
        append_jsonl(
            root / "run_events.jsonl",
            {
                "event": "ORACLE_RESULT",
                "run_id": row["run_id"],
                "cohort": row["cohort"],
                "proposal_id": row["proposal_id"],
                "path": row["path"],
                "oracle": name,
                "value": scores[name],
            },
        )
    append_jsonl(
        root / "run_events.jsonl",
        {
            "event": "RUN_TERMINAL",
            "run_id": row["run_id"],
            "cohort": row["cohort"],
            "proposal_id": row["proposal_id"],
            "path": row["path"],
            "scenario_id": row["scenario_id"],
            "runner_status": row["runner_status"],
            "common_core": row["common_core"],
            "mechanism_role": row["mechanism_role"],
        },
    )


def _alloc_run_id(root: Path, cohort: str, proposal_id: str, path_name: str) -> str:
    known = _known_run_ids(root)
    number = 1
    while True:
        run_id = f"w2-{cohort}-{proposal_id}-{path_name}-{number}"
        if run_id not in known:
            return run_id
        number += 1


def _run_path(root: Path, proposal: dict[str, Any], cohort: str, path_name: str, order_index: int, runtime_impl_hash: str) -> None:
    run_id = _alloc_run_id(root, cohort, proposal["proposal_id"], path_name)
    started = {
        "event": "RUN_STARTED",
        "run_id": run_id,
        "cohort": cohort,
        "proposal_id": proposal["proposal_id"],
        "path": path_name,
        "scenario_id": proposal["scenario_id"],
        "common_core": proposal["common_core"],
        "mechanism_role": proposal["mechanism_role"],
    }
    append_jsonl(root / "run_events.jsonl", started)
    row = _base_row(proposal, cohort, path_name, run_id, order_index)
    if runtime_impl_hash != proposal["implementation_hash"]:
        row["runner_status"] = "INVALID"
        row["invalid_reason"] = "implementation_hash_drift"
        row["provenance_ref"] = None
        row.update(_not_applicable_score())
        row["adjudication_basis"] = "implementation_hash_drift"
        row["unobserved_reason"] = "implementation_hash_drift"
        _write_terminal(root, row, row)
        return
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if path_name not in proposal["applicable_paths"]:
        probe = connect(run_dir / "pragma.sqlite")
        try:
            pragmas = pragma_snapshot(probe)
        finally:
            probe.close()
        provenance = run_dir / "provenance.json"
        provenance.write_text(
            json.dumps({"mechanism_applicable": False, "path": path_name, "scenario_id": proposal["scenario_id"]}, sort_keys=True),
            encoding="utf-8",
        )
        row.update(pragmas)
        row["runner_status"] = "NOT_APPLICABLE"
        row["invalid_reason"] = None
        row["fault_fired"] = False
        row["payload_sha256_after"] = sha256_file(root / proposal["payload_path"])
        row["provenance_ref"] = str(provenance.relative_to(root)).replace("\\", "/")
        row["latency_ms"] = 0.0
        row["local_overhead_ms"] = 0.0
        row["unobserved_reason"] = "MECHANISM_NOT_APPLICABLE"
        row.update(_not_applicable_score())
        _write_terminal(root, row, row)
        return

    payload_file = root / proposal["payload_path"]
    payload = payload_file.read_bytes()
    if sha256_bytes(payload) != proposal["payload_sha256"]:
        row["runner_status"] = "INVALID"
        row["invalid_reason"] = "proposal_hash_before_run"
        row["unobserved_reason"] = "proposal_hash_before_run"
        row.update(_not_applicable_score())
        row["adjudication_basis"] = "proposal_hash_before_run"
        _write_terminal(root, row, row)
        return
    request = EffectRequest(
        proposal_id=proposal["proposal_id"],
        task_id=proposal["task_id"],
        scenario_id=proposal["scenario_id"],
        payload=payload,
        payload_sha256=proposal["payload_sha256"],
        idempotency_key=proposal["idempotency_key"],
        outbox_event_id=proposal["outbox_event_id"],
        authority_token=int(proposal["authority_token"]),
        authority_epoch=int(proposal["authority_epoch"]),
        task_semantic_version=proposal["task_semantic_version"],
        timeout_ms=int(proposal["timeout_ms"]),
        budget_units=int(proposal["budget_units"]),
    )
    if request.task_semantic_version != PREDICATE_VERSION:
        row["runner_status"] = "INVALID"
        row["invalid_reason"] = "predicate_version"
        row.update(_not_applicable_score())
        _write_terminal(root, row, row)
        return
    clock = Clock()
    conn = connect(run_dir / "state.sqlite")
    init_schema(conn)
    world = copy.deepcopy(proposal["world_before"])
    auth = copy.deepcopy(proposal["authority_initial"])
    installed_hash = sha256_obj(auth)
    seq = clock.tick("authority_init")
    conn.execute(
        "INSERT INTO authority (id, token, epoch, eligible) VALUES (1, ?, ?, ?)",
        (auth["token"], auth["epoch"], 1 if auth["eligible"] else 0),
    )
    conn.execute(
        "INSERT INTO authority_log (seq, token, epoch, eligible, event) VALUES (?, ?, ?, ?, 'init')",
        (seq, auth["token"], auth["epoch"], 1 if auth["eligible"] else 0),
    )
    receiver = RecordingReceiver(run_dir, clock, request.payload_sha256)

    def write_auth(updated: dict[str, Any], event: str) -> None:
        logged = clock.tick(event)
        conn.execute(
            "UPDATE authority SET token = ?, epoch = ?, eligible = ? WHERE id = 1",
            (updated["token"], updated["epoch"], 1 if updated["eligible"] else 0),
        )
        conn.execute(
            "INSERT INTO authority_log (seq, token, epoch, eligible, event) VALUES (?, ?, ?, ?, ?)",
            (logged, updated["token"], updated["epoch"], 1 if updated["eligible"] else 0, event),
        )

    faults = FaultController(proposal["fault_schedule"], world, auth, write_auth, receiver)
    path = path_factories()[path_name]()
    ctx = RunCtx(
        request=request,
        world=world,
        auth=auth,
        conn=conn,
        receiver=receiver,
        faults=faults,
        clock=clock,
        run_id=run_id,
        attempt_id=f"{run_id}:send",
    )
    try:
        status = _execute(path, ctx)
        status, invalid_reason = _classify(status, ctx, proposal, payload_file)
        obs = _observation(ctx, proposal)
        scores = _score(obs, status, invalid_reason, sha256_bytes(request.payload) == proposal["payload_sha256"])
        pragmas = pragma_snapshot(conn)
        (run_dir / "receiver_log.json").write_text(json.dumps(ctx.receiver.log, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "authority_log.json").write_text(json.dumps(authority_log(conn), ensure_ascii=False, indent=2), encoding="utf-8")
        for point in ctx.faults.fired:
            append_jsonl(
                root / "run_events.jsonl",
                {"event": "FAULT_APPLIED", "run_id": run_id, "point": point, "cohort": cohort, "path": path_name},
            )
        local_overhead = ctx.latency_ms - ctx.receiver.elapsed_ms
        row.update(pragmas)
        row.update(scores)
        row["runner_status"] = status
        row["invalid_reason"] = invalid_reason
        row["fault_fired"] = bool(ctx.faults.fired)
        row["crash_point"] = ctx.crash_point
        row["payload_sha256_after"] = sha256_file(payload_file)
        row["world_after_hash_observed"] = sha256_obj(ctx.world)
        row["authority_initial_observed_hash"] = installed_hash
        row["provenance_ref"] = str((run_dir / "receiver_log.json").relative_to(root)).replace("\\", "/")
        row["latency_ms"] = round(ctx.latency_ms, 3)
        row["local_overhead_ms"] = round(local_overhead, 3)
        row["receiver_ms"] = round(ctx.receiver.elapsed_ms, 3)
        row["error"] = ctx.error
        row["recovery_attempted"] = ctx.recovery_attempted
        if invalid_reason:
            row["unobserved_reason"] = invalid_reason
            row["joint_observed"] = False
        elif status == "CRASHED":
            row["unobserved_reason"] = None if row["joint_observed"] else "CRASHED"
        else:
            row["unobserved_reason"] = None if row["joint_observed"] else "not_joint"
        _write_terminal(root, row, scores)
    finally:
        conn.close()


def run_selected(root: Path, proposal_ids: list[str], cohort: str) -> dict[str, Any]:
    abandon_incomplete(root)
    records = _load_registry(root)
    by_id = {row["proposal_id"]: row for row in records}
    runtime_impl = implementation_hash(root)
    finished = _finished(root)
    order_map = {proposal_id: index for index, (proposal_id, _scenario, _seed) in enumerate(CLUSTERS)}
    for proposal_id in proposal_ids:
        proposal = by_id[proposal_id]
        for order_index, path_name in enumerate(_ordered_paths(order_map[proposal_id])):
            if (cohort, proposal_id, path_name) in finished:
                continue
            _run_path(root, proposal, cohort, path_name, order_index, runtime_impl)
            finished.add((cohort, proposal_id, path_name))
    rows = [row for row in load_jsonl(root / "run_manifest.jsonl") if row.get("cohort") == cohort and row.get("proposal_id") in set(proposal_ids)]
    return check_fairness([row for row in rows if row.get("runner_status") != "ABANDONED"])


def smoke_gate(root: Path) -> dict[str, Any]:
    rows = [
        row
        for row in load_jsonl(root / "run_manifest.jsonl")
        if row.get("cohort") == "SMOKE" and row.get("runner_status") != "ABANDONED"
    ]
    result = check_fairness(rows)
    payload = {"pass": result["pass"], "mismatches": result["mismatches"], "runs": len(rows), "label": "SMOKE"}
    (root / "smoke_gate.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _identity_stamp(root: Path, repo: Path) -> dict[str, str]:
    return {
        "code_commit": git_head(repo),
        "environment_hash": environment_hash(),
        "implementation_hash": implementation_hash(root),
    }


if __name__ == "__main__":
    import subprocess
    import sys

    from frozen_proposals.analyze import analyze
    from frozen_proposals.baselines.outbox import write_crash_matrix

    week2 = Path(__file__).resolve().parent
    repo = week2.parent.parent
    pytest_cmd = [sys.executable, "-m", "pytest", str(week2 / "oracle_test_suite"), "-q", "--tb=line"]
    completed = subprocess.run(pytest_cmd, cwd=repo, capture_output=True, text=True)
    (week2 / "pytest_output.txt").write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
    freeze(week2, repo)
    write_crash_matrix(week2 / "outbox_crash_matrix.csv")
    if completed.returncode != 0:
        analyze(week2, repo, tests_passed=False, pytest_output=completed.stdout + completed.stderr, batch_ran=False)
        raise SystemExit(completed.returncode)
    run_selected(week2, SMOKE_IDS, "SMOKE")
    gate = smoke_gate(week2)
    if not gate["pass"]:
        analyze(week2, repo, tests_passed=True, pytest_output=completed.stdout + completed.stderr, batch_ran=False)
        raise SystemExit(2)
    run_selected(week2, [item[0] for item in CLUSTERS], "PILOT_FIRST_BATCH")
    analyze(week2, repo, tests_passed=True, pytest_output=completed.stdout + completed.stderr, batch_ran=True)
    _ = _identity_stamp
