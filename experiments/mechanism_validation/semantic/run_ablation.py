"""R0 / R1 / R2 on the same fixtures. Repeats are attempts, not new proposals.

R0 is ByteClaim. R1 calls the semantic oracle again and publishes only on VALID.
R2 does that and then passes the precheck token into one receiver transaction.
The world change in condition A is inserted after that oracle call.
"""

from __future__ import annotations

import csv
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from frozen_proposals.predicate import PREDICATE_VERSION, legal_payload

from mechanism_validation.runtime.auditor import effect_for
from mechanism_validation.runtime.claim_mechanism import ClaimMechanism
from mechanism_validation.runtime.hashing import runtime_code_hash, sha256_bytes, sha256_obj
from mechanism_validation.runtime.receiver_process import ReceiverProcess
from mechanism_validation.runtime.receiver_server import RECEIVER_VERSION
from mechanism_validation.runtime.rsr import ORACLE_VERSION, semantic_precheck
from mechanism_validation.runtime.scorer import score_observation
from mechanism_validation.runtime.types import EffectRequest

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
RUNS = ROOT / "runs"
REPEATS = 10
GENERATOR_ID = "week5_semantic_fixture_v1"
PATHS = {
    "R0": "ByteClaim",
    "R1": "ByteClaim+RSR",
    "R2": "ByteClaim+RSR+VersionFence",
}
ARMS = ("R0", "R1", "R2")

FIELDS = [
    "run_id",
    "proposal_id",
    "scenario_id",
    "generator_id",
    "path_id",
    "fault_id",
    "attempt_id",
    "code_hash",
    "proposal_hash",
    "world_hash",
    "authority_hash",
    "oracle_version",
    "receiver_version",
    "timestamp",
    "process_kill",
    "power_loss_claim",
    "arm",
    "precheck_payload_hash",
    "precheck_world_version",
    "precheck_oracle",
    "precheck_authority_hash",
    "effect_key",
    "gate_oracle",
    "arm_outcome",
    "published_new",
    "effect_landed",
    "effect_count",
    "invalid_publication",
    "stale_publication",
    "duplicate_effect",
    "semantically_superseded",
    "authority_superseded",
    "unknown_stop",
    "correct_stop",
    "successful_completion",
    "semantic_at_decision",
    "successor_status",
    "recovery",
    "rsr_blocked",
    "fence_blocked",
    "fence_result",
    "version_fence_status",
    "rsr_ms",
    "fence_ms",
    "recovery_latency_ms",
    "latency_ms",
]


def _world(order: str, sku: str, qty: int, dest: str, generation: int = 0) -> dict[str, Any]:
    return {
        "order_id": order,
        "sku": sku,
        "qty": qty,
        "dest": dest,
        "generation": generation,
        "effect_forbidden": False,
        "drift_qty": qty + 6,
        "successor_qty": qty + 1,
    }


def _drift(world: dict[str, Any]) -> dict[str, Any]:
    updated = dict(world)
    updated["qty"] = world["drift_qty"]
    return updated


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def _consult(arm: str, payload: bytes, world: dict[str, Any] | None) -> tuple[str, str, float]:
    if arm == "R0":
        return "PUBLISH", "NOT_CONSULTED", 0.0
    import time

    started = time.perf_counter()
    result = semantic_precheck(payload, world, PREDICATE_VERSION)
    elapsed = (time.perf_counter() - started) * 1000.0
    if result == "VALID":
        return "PUBLISH", result, elapsed
    if result == "INVALID":
        return "SEMANTICALLY_SUPERSEDED", result, elapsed
    return "UNKNOWN", result, elapsed


def _publish(client: Any, *, fence: bool, payload: bytes, effect_key: str, attempt_id: str, token: dict[str, Any], discard: bool) -> tuple[dict[str, Any], float]:
    import time

    started = time.perf_counter()
    kwargs = {
        "effect_key": effect_key,
        "payload": payload,
        "attempt_id": attempt_id,
        "fence": fence,
        "expected_world_version": token.get("world_version"),
        "expected_payload_hash": token.get("payload_hash"),
        "expected_authority": token.get("authority"),
        "authority_token": int(token["authority"]["token"]),
        "authority_epoch": int(token["authority"]["epoch"]),
    }
    if discard:
        result = client.submit_discard_response(**kwargs)
    else:
        result = client.submit(**kwargs)
    return result, (time.perf_counter() - started) * 1000.0


def _cases() -> list[dict[str, Any]]:
    valid = _world("SEM-VALID", "A", 2, "north")
    invalid_before = _world("SEM-INVALID", "B", 3, "east")
    unknown_before = _world("SEM-UNKNOWN", "C", 4, "south")
    worlds = {
        "SEM_VALID": (valid, valid, legal_payload(valid), None, "MUST_NOT_STOP"),
        "SEM_INVALID": (invalid_before, _drift(invalid_before), legal_payload(invalid_before), None, "MUST_STOP"),
        "SEM_UNKNOWN": (unknown_before, None, legal_payload(unknown_before), None, "NOT_A_STOP_SCENARIO"),
    }
    rows = []
    for scenario, (before, scoring, payload, _replacement, stop) in worlds.items():
        rows.append(
            {
                "scenario_id": scenario,
                "proposal_id": scenario.lower().replace("_", "-"),
                "seed": {"SEM_VALID": 5101, "SEM_INVALID": 5102, "SEM_UNKNOWN": 5103}[scenario],
                "world_before": before,
                "scoring_world": scoring,
                "payload": payload,
                "replacement": None,
                "world_version": {"SEM_VALID": 4, "SEM_INVALID": 5, "SEM_UNKNOWN": 6}[scenario],
                "stop_condition": "MUST_STOP" if stop == "MUST_STOP" else "NOT_A_STOP_SCENARIO",
                "condition": "BASE",
                "repeats": 1,
            }
        )
    specs = {
        "TOCTOU_A": (_world("TOC-A", "D", 8, "west"), 11, 5104),
        "TOCTOU_B": (_world("TOC-B", "E", 9, "port"), 12, 5105),
        "TOCTOU_C": (_world("TOC-C", "F", 10, "north"), 13, 5106),
        "TOCTOU_D": (_world("TOC-D", "A", 11, "south"), 14, 5107),
        "TOCTOU_E": (_world("TOC-E", "B", 12, "east"), 15, 5108),
    }
    for scenario, (world, version, seed) in specs.items():
        replacement = None
        if scenario == "TOCTOU_E":
            other = _world("TOC-E2", "C", 13, "west")
            replacement = legal_payload(other)
        rows.append(
            {
                "scenario_id": scenario,
                "proposal_id": scenario.lower().replace("_", "-"),
                "seed": seed,
                "world_before": world,
                "scoring_world": world,
                "payload": legal_payload(world),
                "replacement": replacement,
                "world_version": version,
                "stop_condition": "NOT_A_STOP_SCENARIO",
                "condition": scenario,
                "repeats": REPEATS,
            }
        )
    return rows


def _run_case(spec: dict[str, Any], arm: str, attempt_number: int, client: Any, control: Any, code_hash: str, receiver_db: Path) -> dict[str, Any]:
    import time

    attempt_id = f"r{attempt_number:02d}"
    path_id = PATHS[arm]
    run_id = f"w5sem-{spec['proposal_id']}-{arm}-{attempt_id}"
    effect_key = f"{spec['proposal_id']}:{arm}:{attempt_id}"
    authority = {"token": 1, "epoch": 1, "eligible": True}
    payload = spec["payload"]
    scoring = spec["scoring_world"]
    version = int(spec["world_version"])
    control.reset_effects()
    control.set_world_version(version)
    control.set_authority(token=1, epoch=1, eligible=True)
    precheck_oracle = semantic_precheck(payload, spec["world_before"], PREDICATE_VERSION)
    token = {
        "payload_hash": sha256_bytes(payload),
        "world_version": None if spec["condition"] == "TOCTOU_D" else version,
        "oracle": precheck_oracle,
        "authority": dict(authority),
        "effect_key": effect_key,
    }
    work = RUNS / run_id
    mechanism = ClaimMechanism(work / "claim.sqlite")
    started = time.perf_counter()
    rsr_ms = 0.0
    fence_ms = 0.0
    recovery_ms = 0.0
    fence_result = "NOT_INVOKED"
    published_new = False
    try:
        mechanism.record_authority(authority)
        request = EffectRequest(
            proposal_id=spec["proposal_id"],
            idempotency_key=effect_key,
            relay_id=f"relay:{effect_key}",
            payload=payload,
            payload_sha256=sha256_bytes(payload),
            authority_token=1,
            authority_epoch=1,
            world_version=version,
        )
        if mechanism.commit_claim(request) is not True:
            raise RuntimeError("fixture claim did not commit")
        condition = spec["condition"]
        publish_payload = payload
        decision_world = scoring
        if condition == "TOCTOU_E":
            publish_payload = spec["replacement"]
            mechanism.conn.execute(
                "UPDATE claim_rows SET payload = ?, payload_sha256 = ? WHERE idempotency_key = ?",
                (publish_payload, sha256_bytes(publish_payload), effect_key),
            )
            decision_world = spec["world_before"]
        if condition == "TOCTOU_A":
            action, gate, rsr_ms = _consult(arm, payload, spec["world_before"])
            if arm != "R0" and gate != "VALID":
                raise RuntimeError("condition A fixture was not VALID before the gap")
            decision_world = _drift(spec["world_before"])
            control.set_world_version(version + 1)
        elif condition == "BASE":
            action, gate, rsr_ms = _consult(arm, publish_payload, decision_world)
        elif condition == "TOCTOU_E":
            action, gate, rsr_ms = _consult(arm, publish_payload, decision_world)
        else:
            action, gate, rsr_ms = _consult(arm, publish_payload, spec["world_before"])
            decision_world = spec["world_before"]
        response: dict[str, Any] | None = None
        if action == "PUBLISH":
            use_fence = arm == "R2"
            discard = condition == "TOCTOU_C"
            response, call_ms = _publish(
                client,
                fence=use_fence,
                payload=publish_payload,
                effect_key=effect_key,
                attempt_id=f"{run_id}:send",
                token=token,
                discard=discard,
            )
            if use_fence:
                fence_ms = call_ms
                fence_result = str(response.get("outcome"))
            if condition == "TOCTOU_B" and response.get("outcome") == "ACCEPTED":
                control.set_world_version(version + 1)
                decision_world = spec["world_before"]
            if condition == "TOCTOU_C":
                recover_started = time.perf_counter()
                queried = client.query(effect_key)
                recovery_ms = (time.perf_counter() - recover_started) * 1000.0
                if queried.get("status") == "UNKNOWN" or queried.get("outcome") == "UNKNOWN":
                    action = "UNKNOWN"
                elif queried.get("status") == "PRESENT" and queried.get("payload_hash") == sha256_bytes(publish_payload):
                    mechanism.acknowledge_claim(effect_key, queried.get("receipt"))
                    action = "CONFIRMED_EXISTING"
                else:
                    action = str(queried.get("outcome"))
            elif response.get("outcome") == "ACCEPTED":
                mechanism.acknowledge_claim(effect_key, response.get("receipt"))
                published_new = True
                action = "ACCEPTED"
            elif response.get("outcome") == "UNKNOWN":
                action = "UNKNOWN"
            else:
                action = str(response.get("outcome"))
        landed = effect_for(receiver_db, effect_key)
        effect_count = 0 if landed is None else int(landed["logical_effect_count"])
        physical = None if landed is None else landed["payload"]
        if physical is not None:
            semantic_at_decision = semantic_precheck(physical, decision_world, PREDICATE_VERSION)
        elif gate != "NOT_CONSULTED":
            semantic_at_decision = gate
        else:
            semantic_at_decision = "NOT_CONSULTED"
        scores = score_observation(
            run_id=run_id,
            proposal_id=spec["proposal_id"],
            proposal_sha256=sha256_bytes(payload),
            predicate_version=PREDICATE_VERSION,
            scoring_world=decision_world,
            physical_bytes=physical,
            receiver_log=[],
            authority_log=None,
            withheld=physical is None,
            successor_obligation="NOT_REQUIRED",
            stop_condition=spec["stop_condition"],
            recovery_attempted=condition == "TOCTOU_C",
            recovery_bytes_sha256=sha256_bytes(publish_payload) if condition == "TOCTOU_C" else None,
        )
        observed_version = None if response is None else response.get("world_version_observed")
        hash_changed = sha256_bytes(publish_payload) != token["payload_hash"]
        version_changed = (
            observed_version is not None
            and token["world_version"] is not None
            and int(observed_version) != int(token["world_version"])
        )
        effect_landed = effect_count >= 1
        invalid_publication = int(
            effect_landed and physical is not None and semantic_precheck(physical, decision_world, PREDICATE_VERSION) == "INVALID"
        )
        stale_publication = int(effect_landed and published_new and (hash_changed or version_changed))
        duplicate_effect = int(effect_count > 1)
        semantically_superseded = int(action == "SEMANTICALLY_SUPERSEDED")
        authority_superseded = int(action == "AUTHORITY_SUPERSEDED")
        unknown_stop = int(action == "UNKNOWN")
        correct_stop = int(scores["successor_status"] == "CORRECT_STOP")
        successful = int(
            effect_landed
            and semantic_precheck(physical, decision_world, PREDICATE_VERSION) == "VALID"
            and action in {"ACCEPTED", "CONFIRMED_EXISTING", "REPLAY"}
        )
        rsr_blocked = int(arm != "R0" and action in {"SEMANTICALLY_SUPERSEDED", "UNKNOWN"} and fence_result == "NOT_INVOKED")
        if arm != "R0" and action == "SEMANTICALLY_SUPERSEDED":
            rsr_blocked = 1
        fence_blocked = int(
            arm == "R2"
            and fence_result in {"WORLD_VERSION_MISMATCH", "AUTHORITY_SUPERSEDED", "PAYLOAD_HASH_MISMATCH", "UNKNOWN"}
            and not effect_landed
        )
        if condition == "TOCTOU_D" and arm == "R2":
            # RSR returned VALID. The unknown version is the fence's stop.
            rsr_blocked = 0
        latency_ms = (time.perf_counter() - started) * 1000.0
        recovery_label = "NOT_APPLICABLE"
        if condition == "TOCTOU_C" and action == "CONFIRMED_EXISTING":
            recovery_label = "RESTORED_BYTES"
        row = {
            "run_id": run_id,
            "proposal_id": spec["proposal_id"],
            "scenario_id": spec["scenario_id"],
            "generator_id": GENERATOR_ID,
            "path_id": path_id,
            "fault_id": "NONE" if condition == "BASE" else condition,
            "attempt_id": attempt_id,
            "code_hash": code_hash,
            "proposal_hash": sha256_bytes(payload),
            "world_hash": sha256_obj(spec["world_before"]),
            "authority_hash": sha256_obj(authority),
            "oracle_version": ORACLE_VERSION,
            "receiver_version": RECEIVER_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "process_kill": "false",
            "power_loss_claim": "false",
            "arm": arm,
            "precheck_payload_hash": token["payload_hash"],
            "precheck_world_version": "" if token["world_version"] is None else token["world_version"],
            "precheck_oracle": precheck_oracle,
            "precheck_authority_hash": sha256_obj(authority),
            "effect_key": effect_key,
            "gate_oracle": gate,
            "arm_outcome": action,
            "published_new": int(published_new),
            "effect_landed": int(effect_landed),
            "effect_count": effect_count,
            "invalid_publication": invalid_publication,
            "stale_publication": stale_publication,
            "duplicate_effect": duplicate_effect,
            "semantically_superseded": semantically_superseded,
            "authority_superseded": authority_superseded,
            "unknown_stop": unknown_stop,
            "correct_stop": correct_stop,
            "successful_completion": successful,
            "semantic_at_decision": semantic_at_decision,
            "successor_status": scores["successor_status"],
            "recovery": recovery_label,
            "rsr_blocked": rsr_blocked,
            "fence_blocked": fence_blocked,
            "fence_result": fence_result,
            "version_fence_status": "ATOMIC_SQLITE_TRANSACTION" if arm == "R2" else "NOT_IN_ARM",
            "rsr_ms": round(rsr_ms, 3),
            "fence_ms": round(fence_ms, 3),
            "recovery_latency_ms": round(recovery_ms, 3),
            "latency_ms": round(latency_ms, 3),
            "seed": spec["seed"],
        }
        return row
    finally:
        mechanism.close()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in FIELDS})


def _sum(rows: list[dict[str, Any]], arm: str, key: str) -> int:
    return sum(int(row[key]) for row in rows if row["arm"] == arm)


def _performance(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# R0 / R1 / R2 性能与拦截",
        "",
        "同一批提案、场景和种子。`R0` 是 ByteClaim，发布密封字节，不调用语义预检。`R1` 在发布前再调用 `semantics_oracle`。`R2` 在此之后把预检令牌交给接收端的一次 SQLite 事务。版本围栏状态是 `ATOMIC_SQLITE_TRANSACTION`，不是 `VERSION_FENCE_UNSUPPORTED`。",
        "",
        "TOCTOU A 到 E 各重复 10 次。重复是 attempt，不是新的独立提案。`process_kill=false`，`power_loss_claim=false`。间隙由实验控制器在预检返回之后改写目标世界版本，不是第二次并发进程，也不是 `TerminateProcess`。",
        "",
        "无效发布和陈旧发布可以落在同一行。下面的计数是行上的标志之和，不是互斥事件数。",
        "",
        "| 臂 | 无效发布 | 陈旧发布 | 重复效果 | SEMANTICALLY_SUPERSEDED | AUTHORITY_SUPERSEDED | UNKNOWN 停止 | CORRECT_STOP | 成功完成 | RSR 拦住 | 围栏拦住 | 延迟 p95 ms | RSR 均值 ms | 围栏均值 ms | 恢复延迟 p95 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for arm in ARMS:
        subset = [row for row in rows if row["arm"] == arm]
        lines.append(
            "| {arm} | {invalid} | {stale} | {dup} | {sem} | {auth} | {unknown} | {stop} | {ok} | {rsr} | {fence} | {p95} | {rsr_mean} | {fence_mean} | {recovery_p95} |".format(
                arm=PATHS[arm],
                invalid=_sum(rows, arm, "invalid_publication"),
                stale=_sum(rows, arm, "stale_publication"),
                dup=_sum(rows, arm, "duplicate_effect"),
                sem=_sum(rows, arm, "semantically_superseded"),
                auth=_sum(rows, arm, "authority_superseded"),
                unknown=_sum(rows, arm, "unknown_stop"),
                stop=_sum(rows, arm, "correct_stop"),
                ok=_sum(rows, arm, "successful_completion"),
                rsr=_sum(rows, arm, "rsr_blocked"),
                fence=_sum(rows, arm, "fence_blocked"),
                p95=round(_p95([float(row["latency_ms"]) for row in subset]), 3),
                rsr_mean=round(sum(float(row["rsr_ms"]) for row in subset) / len(subset), 3),
                fence_mean=round(sum(float(row["fence_ms"]) for row in subset) / len(subset), 3),
                recovery_p95=round(_p95([float(row["recovery_latency_ms"]) for row in subset]), 3),
            )
        )
    lines.extend(["", "## 谁拦住了什么", ""])
    for condition in ("BASE", "TOCTOU_A", "TOCTOU_B", "TOCTOU_C", "TOCTOU_D", "TOCTOU_E"):
        block = [row for row in rows if (row["fault_id"] == "NONE" and condition == "BASE") or row["fault_id"] == condition]
        if condition != "BASE":
            block = [row for row in rows if row["fault_id"] == condition]
        else:
            block = [row for row in rows if row["fault_id"] == "NONE"]
        lines.append(f"### {condition}")
        lines.append("")
        for arm in ARMS:
            chosen = [row for row in block if row["arm"] == arm]
            lines.append(
                "- {arm}：n={n}，无效 {invalid}，陈旧 {stale}，SEMANTICALLY_SUPERSEDED {sem}，UNKNOWN {unknown}，围栏结果 {fence}，RSR 拦住 {rsr}，围栏拦住 {blocked}，成功 {ok}".format(
                    arm=PATHS[arm],
                    n=len(chosen),
                    invalid=sum(int(row["invalid_publication"]) for row in chosen),
                    stale=sum(int(row["stale_publication"]) for row in chosen),
                    sem=sum(int(row["semantically_superseded"]) for row in chosen),
                    unknown=sum(int(row["unknown_stop"]) for row in chosen),
                    fence=sorted({row["fence_result"] for row in chosen}),
                    rsr=sum(int(row["rsr_blocked"]) for row in chosen),
                    blocked=sum(int(row["fence_blocked"]) for row in chosen),
                    ok=sum(int(row["successful_completion"]) for row in chosen),
                )
            )
        lines.append("")
    lines.extend(
        [
            "## 代价",
            "",
            "RSR 的代价是发布前多一次 `semantics_oracle`。它拦住 `INVALID` 和 `UNKNOWN`，不发布。条件 A 在这次调用返回 `VALID` 之后才改变世界，所以 R1 仍会发布；这组陈旧发布要由版本围栏拒绝。条件 E 在调用前已经换成另一份载荷，R1 和 R2 都由 RSR 返回 `INVALID`，围栏没有被调用。",
            "",
            "围栏的代价是带预期世界版本、权威版本和载荷散列的一次提交。目标在同一个 `BEGIN IMMEDIATE` 里检查这些条件并决定是否插入效果。条件 A 由它拒绝。条件 D 的预期世界版本是空的，围栏返回 `UNKNOWN` 并且不发布。条件 B 是提交成功之后世界才变，围栏不回滚已经提交的效果。条件 C 的发布已经到达目标，客户端丢掉响应字节；恢复只查询并确认已有效果，不再提交一次新效果。",
            "",
            "RSR 单独不消除 TOCTOU。`VALID` 只描述那一次调用当时的世界。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    code_hash = runtime_code_hash()
    cases = _cases()
    for spec in cases:
        (FIXTURES / f"{spec['proposal_id']}.bin").write_bytes(spec["payload"])
        if spec["replacement"] is not None:
            (FIXTURES / f"{spec['proposal_id']}-replacement.bin").write_bytes(spec["replacement"])
    receiver = ReceiverProcess(RUNS / "receiver")
    receiver.start()
    rows: list[dict[str, Any]] = []
    try:
        client = receiver.path_client()
        control = receiver.control_client()
        for spec in cases:
            for attempt in range(1, int(spec["repeats"]) + 1):
                for arm in ARMS:
                    rows.append(
                        _run_case(spec, arm, attempt, client, control, code_hash, receiver.db_path)
                    )
    finally:
        receiver.stop()
    _write_csv(ROOT / "ablation_results.csv", rows)
    _write_csv(ROOT / "rsr_truth_table.csv", [row for row in rows if row["fault_id"] == "NONE"])
    _write_csv(ROOT / "toctou_matrix.csv", [row for row in rows if row["fault_id"] != "NONE"])
    (ROOT / "performance_report.md").write_text(_performance(rows), encoding="utf-8")
    print(f"rows={len(rows)}")
    for arm in ARMS:
        print(
            arm,
            "invalid",
            _sum(rows, arm, "invalid_publication"),
            "stale",
            _sum(rows, arm, "stale_publication"),
            "dup",
            _sum(rows, arm, "duplicate_effect"),
            "sem",
            _sum(rows, arm, "semantically_superseded"),
            "auth",
            _sum(rows, arm, "authority_superseded"),
            "unknown",
            _sum(rows, arm, "unknown_stop"),
            "stop",
            _sum(rows, arm, "correct_stop"),
            "ok",
            _sum(rows, arm, "successful_completion"),
        )


if __name__ == "__main__":
    main()
