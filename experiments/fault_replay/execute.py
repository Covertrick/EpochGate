"""One primary run of one proposal on one path.

Week 2 scenarios use the Week 2 path adapters and oracles. Separation and
notified freeze are scenario behavior. Notified freeze is not a new protocol.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from frozen_proposals.db import authority_log, connect, init_schema, pragma_snapshot
from frozen_proposals.faults import FaultController
from frozen_proposals.frozen_proposal_runner import Clock, EffectRequest, RunCtx, _classify, _execute, _observation, _score
from frozen_proposals.hashing import sha256_bytes, sha256_file, sha256_obj
from frozen_proposals.oracles import ORACLE_VERSIONS, Observation, adjudicate, authority_oracle, effect_history_adjudicated, evidence_oracle, semantics_oracle, terminal_oracle
from frozen_proposals.paths import path_factories
from frozen_proposals.predicate import PREDICATE_VERSION, legal_payload
from frozen_proposals.receiver_adapter import RECEIVER_IMPL, RECEIVER_VERSION, RecordingReceiver
from fault_replay.contract import (
    COHORT,
    FREEZE_SCENARIOS,
    RECOVERY_PATHS,
    SEPARATION_CHECKPOINT,
    SEPARATION_SCENARIOS,
    WEEK3,
    in_main_comparison,
)
from fault_replay.kill_runner import kill_once, resolve_payload_path
from fault_replay.scoring import classify_claim_digest, published_old_bytes, week2_bundle


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"sha256": sha256_bytes(value), "length": len(value)}
    raise TypeError(type(value))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _cluster_key(record: dict[str, Any]) -> str:
    return f"{record['proposal_id']}:{record['scenario_id']}"


def _empty_scores() -> dict[str, Any]:
    return {
        "terminal_outcome": "UNKNOWN",
        "authority_decision": "UNKNOWN",
        "physical_effect_state": "UNKNOWN",
        "week2_physical_effect_state": "UNKNOWN",
        "evidence_state": "UNKNOWN",
        "semantic_validity": "UNKNOWN",
        "recovery_state": "NOT_APPLICABLE",
        "successor_obligation": "UNKNOWN",
        "successor_status": "UNKNOWN",
        "week1_successor_obligation": "UNKNOWN",
        "week1_successor_status": "UNKNOWN",
        "stop_correctness": "UNKNOWN",
        "adjudication": "UNRESOLVED",
        "adjudication_basis": "runner_NOT_APPLICABLE",
        "joint_observed": False,
        "terminal_adjudicated": False,
        "effect_history_adjudicated": False,
        "duplicate_effect": False,
    }


def _shell(record: dict[str, Any], path_name: str, run_id: str) -> dict[str, Any]:
    role = record["mechanism_role"]
    return {
        "run_id": run_id,
        "cluster_key": _cluster_key(record),
        "cohort": COHORT,
        "proposal_id": record["proposal_id"],
        "scenario_id": record["scenario_id"],
        "scenario_family": record["scenario_family"],
        "mechanism_role": role,
        "in_main_comparison": in_main_comparison(record["scenario_id"], role),
        "path": path_name,
        "task_id": record["task_id"],
        "runner_status": "COMPLETED",
        "scenario_behavior": "week2_path",
        "fault_evidence_class": "NONE",
        "recovery_evidence_state": "NOT_APPLICABLE",
        "successor_required": record["successor_obligation"] == "REQUIRED",
        "outdated_scenario_label": record["outdated_scenario_label"],
        "published_old_bytes": False,
        "payload_sha256": record["payload_sha256"],
        "world_before_hash": record["world_before_hash"],
        "world_after_hash": record["world_after_hash"],
        "authority_initial_hash": record["authority_initial_hash"],
        "world_state_id_initial": record["world_before_hash"],
        "world_state_id_observed": record["world_after_hash"],
        "predicate_version": PREDICATE_VERSION,
        "oracle_versions": json.dumps(ORACLE_VERSIONS, sort_keys=True, separators=(",", ":")),
        "receiver_impl": RECEIVER_IMPL,
        "receiver_version": RECEIVER_VERSION,
        "killed_pid": "",
        "checkpoint": "",
        "physical_basis": "LEGAL_WORLD",
        "unobserved_reason": "",
        "provenance_ref": "",
        "latency_ms": 0.0,
        "journal_mode": "",
        "synchronous": "",
    }


def _install_authority(conn: Any, clock: Clock, auth: dict[str, Any]) -> int:
    seq = clock.tick("authority_init")
    conn.execute(
        "INSERT INTO authority (id, token, epoch, eligible) VALUES (1, ?, ?, ?)",
        (auth["token"], auth["epoch"], 1 if auth["eligible"] else 0),
    )
    conn.execute(
        "INSERT INTO authority_log (seq, token, epoch, eligible, event) VALUES (?, ?, ?, ?, 'init')",
        (seq, auth["token"], auth["epoch"], 1 if auth["eligible"] else 0),
    )
    return seq


def _log_authority(conn: Any, clock: Clock, auth: dict[str, Any], event: str) -> int:
    seq = clock.tick(event)
    conn.execute(
        "UPDATE authority SET token = ?, epoch = ?, eligible = ? WHERE id = 1",
        (auth["token"], auth["epoch"], 1 if auth["eligible"] else 0),
    )
    conn.execute(
        "INSERT INTO authority_log (seq, token, epoch, eligible, event) VALUES (?, ?, ?, ?, ?)",
        (seq, auth["token"], auth["epoch"], 1 if auth["eligible"] else 0, event),
    )
    return seq


def _provenance(run_dir: Path) -> str:
    observed = (run_dir / "observation.json").resolve()
    try:
        return str(observed.relative_to(WEEK3.resolve())).replace("\\", "/")
    except ValueError:
        return str(observed)


def _save_trace(run_dir: Path, obs: Observation, row: dict[str, Any]) -> None:
    _write_json(run_dir / "receiver_log.json", obs.receiver_log or [])
    _write_json(run_dir / "authority_log.json", obs.authority_log or [])
    if obs.physical_bytes is not None:
        (run_dir / "physical.bin").write_bytes(obs.physical_bytes)
    _write_json(
        run_dir / "observation.json",
        {
            "run_id": row["run_id"],
            "scenario_id": row["scenario_id"],
            "scenario_behavior": row["scenario_behavior"],
            "proposal_sha256": obs.proposal_sha256,
            "predicate_version": obs.predicate_version,
            "scoring_world": obs.scoring_world,
            "physical_sha256": None if obs.physical_bytes is None else sha256_bytes(obs.physical_bytes),
            "receiver_log": obs.receiver_log,
            "authority_log": obs.authority_log,
            "recovery_attempted": obs.recovery_attempted,
            "recovery_bytes_sha256": obs.recovery_bytes_sha256,
            "successor_obligation": obs.successor_obligation,
            "stop_condition": obs.stop_condition,
            "withheld": obs.withheld,
            "duplicate_effect": obs.duplicate_effect,
            "outdated_scenario_label": obs.outdated_scenario_label,
            "runner_status": row["runner_status"],
            "recovery_evidence_state": row.get("recovery_evidence_state"),
            "published_old_bytes": row["published_old_bytes"],
            "physical_basis": row.get("physical_basis"),
        },
    )
    row["provenance_ref"] = _provenance(run_dir)


def run_week2_style(record: dict[str, Any], path_name: str, run_dir: Path, run_id: str) -> dict[str, Any]:
    row = _shell(record, path_name, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    if path_name not in record["applicable_paths"]:
        row["runner_status"] = "NOT_APPLICABLE"
        row["unobserved_reason"] = "MECHANISM_NOT_APPLICABLE"
        row.update(_empty_scores())
        row["adjudication_basis"] = "runner_NOT_APPLICABLE"
        _write_json(run_dir / "observation.json", {"mechanism_applicable": False, "run_id": run_id})
        row["provenance_ref"] = _provenance(run_dir)
        return row
    payload_path = resolve_payload_path(record)
    payload = payload_path.read_bytes()
    if sha256_bytes(payload) != record["payload_sha256"]:
        row["runner_status"] = "INVALID"
        row["unobserved_reason"] = "proposal_hash_before_run"
        row.update(_empty_scores())
        row["adjudication_basis"] = "proposal_hash_before_run"
        return row
    request = EffectRequest(
        proposal_id=record["proposal_id"],
        task_id=record["task_id"],
        scenario_id=record["scenario_id"],
        payload=payload,
        payload_sha256=record["payload_sha256"],
        idempotency_key=record["idempotency_key"],
        outbox_event_id=record["outbox_event_id"],
        authority_token=int(record["authority_token"]),
        authority_epoch=int(record["authority_epoch"]),
        task_semantic_version=record["task_semantic_version"],
        timeout_ms=int(record["timeout_ms"]),
        budget_units=int(record["budget_units"]),
    )
    clock = Clock()
    conn = connect(run_dir / "state.sqlite")
    try:
        init_schema(conn)
        world = json.loads(json.dumps(record["world_before"]))
        auth = json.loads(json.dumps(record["authority_initial"]))
        _install_authority(conn, clock, auth)
        receiver = RecordingReceiver(run_dir, clock, request.payload_sha256)

        def write_auth(updated: dict[str, Any], event: str) -> None:
            _log_authority(conn, clock, updated, event)

        faults = FaultController(record["fault_schedule"], world, auth, write_auth, receiver)
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
        status = _execute(path, ctx)
        status, invalid_reason = _classify(status, ctx, record, payload_path)
        obs = _observation(ctx, record)
        scores = _score(obs, status, invalid_reason, sha256_bytes(request.payload) == record["payload_sha256"])
        pragmas = pragma_snapshot(conn)
        row.update(pragmas)
        row.update(scores)
        row["week2_physical_effect_state"] = scores["physical_effect_state"]
        row["runner_status"] = status
        row["physical_basis"] = "LEGAL_WORLD"
        row["published_old_bytes"] = published_old_bytes(obs.physical_bytes, payload)
        row["fault_evidence_class"] = "IN_PROCESS_FAULT" if ctx.crash_point or status == "CRASHED" else "NONE"
        row["latency_ms"] = round(ctx.latency_ms, 3)
        if invalid_reason:
            row["unobserved_reason"] = invalid_reason
            row["joint_observed"] = False
        _save_trace(run_dir, obs, row)
        return row
    finally:
        conn.close()


def _separated_physical(target: bytes | None, claim: bytes, observed: dict[str, Any], *, restored: bool, attempted: bool) -> tuple[str, str]:
    if restored:
        return classify_claim_digest(target, sha256_bytes(claim), recovery_attempted=True, restored_bytes=True)
    if target is None:
        return classify_claim_digest(None, sha256_bytes(claim), recovery_attempted=attempted, restored_bytes=False)
    legal = legal_payload(observed)
    if target == claim or (legal != b"" and target == legal):
        return "CONFIRMED_EFFECT", "NOT_APPLICABLE"
    return "NOT_LEGAL", "NOT_APPLICABLE"


def _score_separated(
    *,
    obs: Observation,
    runner_status: str,
    physical: str,
    recovery: str,
    successor_status: str | None = None,
    stop_correctness: str | None = None,
    week1_successor_status: str | None = None,
) -> dict[str, Any]:
    bundle = week2_bundle(obs, runner_status)
    terminal = bundle["terminal_outcome"]
    authority = bundle["authority_decision"]
    evidence = bundle["evidence_state"]
    adjudication, basis = adjudicate(
        terminal=terminal,
        authority=authority,
        physical=physical,
        evidence=evidence,
        duplicate_effect=obs.duplicate_effect,
        runner_status=runner_status,
    )
    bundle["physical_effect_state"] = physical
    bundle["recovery_state"] = recovery
    bundle["adjudication"] = adjudication
    bundle["adjudication_basis"] = basis
    bundle["effect_history_adjudicated"] = effect_history_adjudicated(authority, physical, evidence)
    bundle["joint_observed"] = bool(
        runner_status in {"COMPLETED", "CRASHED"}
        and terminal in {"MATCH", "MISMATCH"}
        and bundle["effect_history_adjudicated"]
    )
    if successor_status is not None:
        bundle["successor_status"] = successor_status
    if stop_correctness is not None:
        bundle["stop_correctness"] = stop_correctness
    if week1_successor_status is not None:
        bundle["week1_successor_status"] = week1_successor_status
    return bundle


def _publish(run_dir: Path, record: dict[str, Any], payload: bytes, token: int, epoch: int, run_id: str) -> RecordingReceiver:
    clock = Clock()
    receiver = RecordingReceiver(run_dir, clock, sha256_bytes(payload) if False else record["payload_sha256"])
    # The shared receiver must accept a successor whose digest is not the claim.
    # RecordingReceiver flags foreign payloads. Call SharedReceiver through a
    # local clock log instead when the bytes are a successor.
    receiver.accept(
        effect_key=record["idempotency_key"],
        attempt_id=f"{run_id}:send",
        payload=payload,
        authority_token=token,
        authority_epoch=epoch,
        run_id=run_id,
    )
    return receiver


def run_separation(record: dict[str, Any], path_name: str, run_dir: Path, run_id: str) -> dict[str, Any]:
    row = _shell(record, path_name, run_id)
    row["scenario_behavior"] = "separation"
    row["physical_basis"] = "CLAIM_DIGEST"
    row["checkpoint"] = SEPARATION_CHECKPOINT[record["scenario_id"]]
    run_dir.mkdir(parents=True, exist_ok=True)
    payload_path = resolve_payload_path(record)
    payload = payload_path.read_bytes()
    observed = record["world_after"]
    if path_name in RECOVERY_PATHS:
        killed = kill_once(
            run_dir / "kill",
            proposal=record,
            path=path_name,
            checkpoint=row["checkpoint"],
            run_id=run_id,
            scoring_world=observed,
        )
        target_path = run_dir / "kill" / "effect.bin"
        target = target_path.read_bytes() if target_path.is_file() else None
        physical, recovery = _separated_physical(
            target,
            payload,
            observed,
            restored=bool(killed["restored_bytes"]),
            attempted=True,
        )
        row["fault_evidence_class"] = "PROCESS_CRASH_INJECTION"
        row["recovery_evidence_state"] = killed["evidence_state"]
        row["killed_pid"] = killed["killed_pid"]
        receiver_log_path = run_dir / "kill" / "receiver_log.json"
        receiver_log = json.loads(receiver_log_path.read_text(encoding="utf-8")) if receiver_log_path.is_file() else []
        authority = [
            {
                "seq": 1,
                "token": int(record["authority_token"]),
                "epoch": int(record["authority_epoch"]),
                "eligible": True,
                "event": "init",
            }
        ]
    else:
        conn = connect(run_dir / "state.sqlite")
        clock = Clock()
        try:
            init_schema(conn)
            auth = json.loads(json.dumps(record["authority_initial"]))
            _install_authority(conn, clock, auth)
            receiver = RecordingReceiver(run_dir, clock, record["payload_sha256"])
            receiver.accept(
                effect_key=record["idempotency_key"],
                attempt_id=f"{run_id}:send",
                payload=payload,
                authority_token=int(record["authority_token"]),
                authority_epoch=int(record["authority_epoch"]),
                run_id=run_id,
            )
            target = receiver.physical()
            receiver_log = list(receiver.log)
            authority = authority_log(conn)
            pragmas = pragma_snapshot(conn)
            row.update(pragmas)
        finally:
            conn.close()
        physical, recovery = _separated_physical(target, payload, observed, restored=False, attempted=False)
        row["fault_evidence_class"] = "NONE"
        row["recovery_evidence_state"] = "NOT_APPLICABLE"
    obs = Observation(
        run_id=run_id,
        proposal_id=record["proposal_id"],
        proposal_sha256=record["payload_sha256"],
        predicate_version=PREDICATE_VERSION,
        scoring_world=observed,
        physical_bytes=target,
        receiver_log=receiver_log,
        authority_log=authority,
        recovery_attempted=path_name in RECOVERY_PATHS,
        recovery_bytes_sha256=sha256_bytes(target) if target is not None and path_name in RECOVERY_PATHS and row["recovery_evidence_state"] == "PUBLISH_SEALED" else None,
        successor_obligation="NOT_REQUIRED",
        stop_condition="NOT_A_STOP_SCENARIO",
        outdated_scenario_label=record["outdated_scenario_label"],
    )
    # Keep Week 2 recovery_state from labeling a confirmed target as RESTORED_BYTES.
    # The Week 3 class is applied explicitly below.
    scores = _score_separated(obs=obs, runner_status="COMPLETED", physical=physical, recovery=recovery)
    row.update(scores)
    row["published_old_bytes"] = published_old_bytes(target, payload)
    row["physical_basis"] = "CLAIM_DIGEST"
    _save_trace(run_dir, obs, row)
    return row


def _freeze_status(target: bytes | None, claim: bytes, observed: dict[str, Any], path_name: str) -> dict[str, str]:
    legal = legal_payload(observed)
    if path_name in RECOVERY_PATHS:
        if legal == b"" and target is None:
            return {
                "successor_status": "CORRECT_STOP",
                "stop_correctness": "CORRECT_STOP",
                "week1_successor_status": "NOT_APPLICABLE",
                "week1_successor_obligation": "OBLIGATED",
            }
        if legal != b"" and target == legal:
            return {
                "successor_status": "COMPLETED_VALID",
                "stop_correctness": "UNKNOWN",
                "week1_successor_status": "COMPLETED",
                "week1_successor_obligation": "OBLIGATED",
            }
    return {
        "successor_status": "FAILED",
        "stop_correctness": "NOT_CORRECT_STOP",
        "week1_successor_status": "NOT_COMPLETED",
        "week1_successor_obligation": "OBLIGATED",
    }


def run_freeze(record: dict[str, Any], path_name: str, run_dir: Path, run_id: str) -> dict[str, Any]:
    row = _shell(record, path_name, run_id)
    row["scenario_behavior"] = "notified_freeze"
    row["physical_basis"] = "CLAIM_DIGEST"
    row["successor_required"] = True
    row["checkpoint"] = "after_claim_before_publish"
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = resolve_payload_path(record).read_bytes()
    observed = json.loads(json.dumps(record["world_after"]))
    auth = json.loads(json.dumps(record["authority_after"]))
    conn = connect(run_dir / "state.sqlite")
    clock = Clock()
    try:
        init_schema(conn)
        initial = json.loads(json.dumps(record["authority_initial"]))
        _install_authority(conn, clock, initial)
        if path_name in RECOVERY_PATHS:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS frozen_claim (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    path TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    publish_allowed INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO frozen_claim (id, path, payload, payload_sha256, status, publish_allowed)
                VALUES (1, ?, ?, ?, 'FROZEN', 0)
                """,
                (path_name, payload, record["payload_sha256"]),
            )
        _log_authority(conn, clock, auth, record["fault_schedule"]["points"][0]["actions"][0]["op"])
        legal = legal_payload(observed)
        receiver = RecordingReceiver(run_dir, clock, record["payload_sha256"])
        if path_name in RECOVERY_PATHS:
            if legal != b"":
                receiver.accept(
                    effect_key=record["idempotency_key"],
                    attempt_id=f"{run_id}:successor",
                    payload=legal,
                    authority_token=int(auth["token"]),
                    authority_epoch=int(auth["epoch"]),
                    run_id=run_id,
                )
            else:
                receiver.log.append(
                    {
                        "run_id": run_id,
                        "seq": clock.tick("withhold"),
                        "effect_key": record["idempotency_key"],
                        "attempt_id": f"{run_id}:stop",
                        "accepted": False,
                        "detail": "frozen_stop",
                        "digest": None,
                        "token": int(auth["token"]),
                        "epoch": int(auth["epoch"]),
                        "receipt": None,
                    }
                )
        else:
            receiver.accept(
                effect_key=record["idempotency_key"],
                attempt_id=f"{run_id}:send",
                payload=payload,
                authority_token=int(record["authority_token"]),
                authority_epoch=int(record["authority_epoch"]),
                run_id=run_id,
            )
        target = receiver.physical()
        physical, recovery = _separated_physical(target, payload, observed, restored=False, attempted=False)
        status = _freeze_status(target, payload, observed, path_name)
        obs = Observation(
            run_id=run_id,
            proposal_id=record["proposal_id"],
            proposal_sha256=record["payload_sha256"],
            predicate_version=PREDICATE_VERSION,
            scoring_world=observed,
            physical_bytes=target,
            receiver_log=list(receiver.log),
            authority_log=authority_log(conn),
            successor_obligation="REQUIRED",
            stop_condition=record["stop_condition"],
            withheld=target is None,
            successor_effect_bytes=target if path_name in RECOVERY_PATHS else None,
            outdated_scenario_label=record["outdated_scenario_label"],
        )
        scores = _score_separated(
            obs=obs,
            runner_status="COMPLETED",
            physical=physical,
            recovery=recovery,
            successor_status=status["successor_status"],
            stop_correctness=status["stop_correctness"],
            week1_successor_status=status["week1_successor_status"],
        )
        scores["successor_obligation"] = "REQUIRED"
        scores["week1_successor_obligation"] = "OBLIGATED"
        pragmas = pragma_snapshot(conn)
        row.update(pragmas)
        row.update(scores)
        row["published_old_bytes"] = published_old_bytes(target, payload)
        _save_trace(run_dir, obs, row)
        return row
    finally:
        conn.close()


def run_cell(record: dict[str, Any], path_name: str, run_root: Path | None = None) -> dict[str, Any]:
    run_id = f"w3-{record['proposal_id']}-{path_name}-1"
    root = run_root or (WEEK3 / "formal_runs")
    run_dir = root / run_id
    payload_path = resolve_payload_path(record)
    before = sha256_file(payload_path)
    scenario_id = record["scenario_id"]
    if scenario_id in SEPARATION_SCENARIOS:
        row = run_separation(record, path_name, run_dir, run_id)
    elif scenario_id in FREEZE_SCENARIOS:
        row = run_freeze(record, path_name, run_dir, run_id)
    else:
        row = run_week2_style(record, path_name, run_dir, run_id)
    after = sha256_file(payload_path)
    row["payload_sha256_before"] = before
    row["payload_sha256_after"] = after
    row["payload_hash_match"] = before == after == record["payload_sha256"]
    if not row["payload_hash_match"]:
        row["runner_status"] = "INVALID"
        row["unobserved_reason"] = "proposal_hash_mismatch"
        row["joint_observed"] = False
    return row
