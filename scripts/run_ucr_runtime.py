"""240 个 attempt 的安全闭环。后悔不在这里计算。

场景区间只用来让四种动作都出现。模型预测的区间另记一栏，不覆盖场景动作。
取消回执保持 UNKNOWN。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from epochgate.events import EventLog
from epochgate.followup_runtime import ScriptedGeneration, VllmGeneration
from epochgate.gateway import ActionGateway
from epochgate.ids import IdFactory
from epochgate.inventory import InventoryEnvironment
from epochgate.models import AdmissionLease, CandidateAction, InferenceRequest, Task
from epochgate.observation import ObservationStore
from epochgate.registry import Registry
from epochgate.ucr_policy import decide
from epochgate.ucr_workload import make_example

OUT = Path("results/experiments/ucr")
SCENARIOS = ("predicate-invalid", "predicate-valid", "candidate-formed", "version-race")


def _features(stock: int, tokens: float) -> dict[str, float]:
    example = make_example("F2", 1002)
    features = dict(example.features)
    features["predicate_valid"] = 1.0 if stock >= 1 else 0.0
    features["generated_tokens"] = tokens
    features["token_rate"] = 20.0
    features["queue_length"] = 1.0
    features["slot_occupancy"] = 0.5
    return features


def _intervals(scenario: str, index: int) -> tuple[tuple[float, float], tuple[float, float], float]:
    if scenario == "predicate-valid" and index % 2 == 1:
        return (2.0, 4.0), (2.5, 3.5), 5.0
    if scenario == "predicate-valid":
        return (1.0, 1.5), (4.0, 5.0), 0.0
    return (3.0, 4.0), (1.0, 1.5), 0.0


async def run_attempt(generation, *, scenario: str, batch: int, index: int) -> dict:
    ids = IdFactory(batch * 1000 + index + 1)
    env = InventoryEnvironment(stock=5, schedule=[], seed=index + 1, ids=ids)
    log = EventLog()
    registry = Registry(log, run_id=f"ucr-{batch}-{scenario}-{index}", ids=ids)
    observations = ObservationStore(ids=ids)
    gateway = ActionGateway(env, registry, observations, log, run_id=registry._run_id)
    import time

    now = time.monotonic()
    task = Task(task_id="t", arrival_mono=now, deadline_mono=now + 60, goal="reserve one unit")
    registry.register_task(task)
    attempt = registry.begin_attempt(task.task_id)
    if scenario == "predicate-invalid":
        env.apply_exogenous(-5)
    observed = await env.observe(env.default_refresh())
    obs = observations.record(observed.observations[0], task_id=task.task_id)
    request = InferenceRequest(
        attempt_id=attempt.attempt_id,
        task_id=task.task_id,
        plan_epoch=attempt.plan_epoch,
        obs_revision=attempt.obs_revision,
        goal="Reply with the single word ready.",
        observation_ids=[obs.observation_id],
    )
    handle = await generation.start(request)
    features = _features(env.stock, 8.0)
    s_interval, c_interval, value = _intervals(scenario, index)
    formed = scenario in {"candidate-formed", "version-race"}
    action = decide(
        features, s_interval, c_interval,
        information_value=value, probe_cost=0.2,
        allow_probe=True, already_probed=False, candidate_formed=formed,
    )
    second = None
    gateway_result = None
    cancel_receipt = None
    generation_end = None
    if action == "probe":
        generation_end = await generation.finish(handle)
        env.apply_exogenous(-env.stock)
        features = _features(env.stock, features["generated_tokens"] + 8.0)
        second = decide(
            features, s_interval, c_interval,
            information_value=0.0, probe_cost=0.2,
            allow_probe=False, already_probed=True, candidate_formed=False,
        )
        action = second
    if action == "abort-and-replan":
        if generation_end is None:
            cancel_receipt = (await generation.cancel(handle)).model_dump()
        registry.supersede_attempt(attempt.attempt_id, reason="predicate-invalid")
        lease = AdmissionLease(lease_id=f"lease-{batch}-{index}", attempt_id=attempt.attempt_id, earliest_start_mono=now, expires_at_mono=now + 60)
        candidate = _candidate(batch, index, attempt, obs.observation_id)
        rejected = await gateway.submit(candidate, lease)
        gateway_result = {"accepted": rejected.accepted, "reasons": list(rejected.reasons)}
    elif action == "accept-if-valid" and scenario == "version-race":
        registry.advance_plan_epoch(task.task_id, reason="version-race")
        lease = AdmissionLease(lease_id=f"lease-{batch}-{index}", attempt_id=attempt.attempt_id, earliest_start_mono=now, expires_at_mono=now + 60)
        candidate = _candidate(batch, index, attempt, obs.observation_id)
        rejected = await gateway.submit(candidate, lease)
        gateway_result = {"accepted": rejected.accepted, "reasons": list(rejected.reasons)}
        if generation_end is None:
            generation_end = await generation.finish(handle)
    elif action == "accept-if-valid":
        lease = AdmissionLease(lease_id=f"lease-{batch}-{index}", attempt_id=attempt.attempt_id, earliest_start_mono=now, expires_at_mono=now + 60)
        candidate = _candidate(batch, index, attempt, obs.observation_id)
        accepted = await gateway.submit(candidate, lease)
        gateway_result = {"accepted": accepted.accepted, "reasons": list(accepted.reasons)}
        if generation_end is None:
            generation_end = await generation.finish(handle)
    elif generation_end is None:
        generation_end = await generation.finish(handle)
    return {
        "batch": batch,
        "index": index,
        "scenario": scenario,
        "stock": env.stock,
        "first_decision": "probe" if second is not None else action,
        "decision": action,
        "probe_followup": second,
        "interval_source": "scenario",
        "gateway": gateway_result,
        "cancel_receipt": cancel_receipt,
        "generation": generation_end,
        "resource_state": await generation.resource_state(),
        "provider_reclaim_observed": False,
    }


def _candidate(batch: int, index: int, attempt, observation_id: str) -> CandidateAction:
    return CandidateAction(
        action_id=f"ucr-{batch}-{index}",
        task_id="t",
        attempt_id=attempt.attempt_id,
        plan_epoch=attempt.plan_epoch,
        obs_revision=attempt.obs_revision,
        action_type="reserve",
        parameters={"qty": 1},
        observation_ids=[observation_id],
    )


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("vllm", "scripted"), default="vllm")
    parser.add_argument("--batches", type=int, default=3)
    parser.add_argument("--tasks", type=int, default=20)
    args = parser.parse_args()
    generation = VllmGeneration() if args.engine == "vllm" else ScriptedGeneration()
    rows = []
    try:
        for scenario in SCENARIOS:
            for batch in range(args.batches):
                for index in range(args.tasks):
                    row = await run_attempt(generation, scenario=scenario, batch=batch, index=index)
                    rows.append(row)
                    print(f"{len(rows)} {scenario} {row['first_decision']} -> {row['decision']}", flush=True)
    finally:
        close = getattr(generation, "aclose", None)
        if close is not None:
            await close()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "runtime.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    summary = {
        "engine": args.engine,
        "attempts": len(rows),
        "actions": sorted({row["decision"] for row in rows}),
        "probes": sum(row["first_decision"] == "probe" for row in rows),
        "probe_then_abort": sum(row["probe_followup"] == "abort-and-replan" for row in rows),
        "stale_accepted_commit": sum(row["scenario"] == "version-race" and row["gateway"] and row["gateway"]["accepted"] for row in rows),
        "invalid_accepted": sum(row["scenario"] == "predicate-invalid" and row["gateway"] and row["gateway"]["accepted"] for row in rows),
        "cancel_unknown": sum(
            1 for row in rows
            if row["cancel_receipt"] and row["cancel_receipt"]["grade"] == "UNKNOWN" and row["cancel_receipt"]["reclaimed"] is False
        ),
        "provider_reclaim_observed": False,
        "used_for_regret": False,
    }
    (OUT / "runtime_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_main())
