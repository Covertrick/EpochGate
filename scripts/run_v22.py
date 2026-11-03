"""按冻结协议跑 v1.6–v2.0。已完成的 (phase, cell, seed, arm, scheduler) 会跳过。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from epochgate.cli import load_project_env  # noqa: E402
from epochgate.v22_runner import run_v22_case  # noqa: E402

OUT = ROOT / "results" / "experiments" / "v1_3"
LENGTHS = ("short", "mid", "long")
WORDS = {"short": 50, "mid": 200, "long": 450}
TIMINGS = (("early", 0.25), ("mid", 0.80), ("late", 1.80))
ARMS = ("A", "B", "C")


def _cells():
    for i, length in enumerate(LENGTHS):
        for j, (timing, delay) in enumerate(TIMINGS):
            yield i, j, length, timing, delay


def protocol() -> dict:
    return {
        "name": "epochgate-v22",
        "model": "deepseek-chat",
        "primary_metrics": [
            "harmful C minus A revoke-to-slot is not the claim; slot hold C vs A",
            "C vs B false_cancel and old result acceptance",
            "queue follow-up first wait, unit is batch",
            "recovery rate C is non-inferior to A within 10 percentage points",
        ],
        "minimum_effects": {
            "queue_followup_wait_s": 1.0,
            "recovery_noninferiority_pp": 10,
        },
        "seed_rule": "phase base + length index * stride + timing index * stride + repetition; confirmatory seeds do not overlap pilot",
        "pilot_harmful_reps": 4,
        "confirm_harmful_reps": 12,
        "pilot_harmless_reps": 4,
        "confirm_harmless_reps": 20,
        "queue_primary_batches": 20,
        "queue_low_batches": 10,
        "recovery_reps": 20,
        "workflow_reps": 20,
        "slot_sd_rule": "if pilot slot-delta sd > 1.5s, raise that cell until se < 0.5s; do not change the metric",
    }


def _load_done(path: Path) -> set[tuple]:
    done: set[tuple] = set()
    if not path.is_file():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("task_id") not in {None, "t0"} and row.get("queue_position", 0) != 0:
            continue
        done.add(
            (
                row.get("phase"),
                row.get("cell_id"),
                row.get("seed"),
                row.get("arm"),
                row.get("scheduler_order"),
                row.get("batch_id"),
            )
        )
    return done


def _append(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


async def _one(spec: dict, path: Path, sem: asyncio.Semaphore) -> None:
    async with sem:
        rows = await run_v22_case(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            seed=spec["seed"],
            arm=spec["arm"],
            task_kind=spec["task_kind"],
            cell_id=spec["cell_id"],
            pair_id=spec["pair_id"],
            batch_id=spec["batch_id"],
            length_tier=spec["length_tier"],
            timing_tier=spec["timing_tier"],
            target_words=spec["target_words"],
            t_change_s=spec["t_change_s"],
            t_restore_s=spec.get("t_restore_s"),
            initial_stock=spec.get("initial_stock", 1),
            scheduler_order=spec.get("scheduler_order", "fifo"),
            task_plan=spec.get("task_plan"),
            code_version=spec["phase"],
            experiment_block=spec["phase"],
            goal_text=spec.get("goal_text"),
            system_prompt=spec.get("system_prompt"),
            workflow_class=spec.get("workflow_class") or "",
        )
        for row in rows:
            row["phase"] = spec["phase"]
        _append(path, rows)
        print(f"done {spec['phase']} {spec['cell_id']} {spec['arm']} seed={spec['seed']}", flush=True)


def _jobs(phase: str) -> list[tuple[dict, Path]]:
    jobs: list[tuple[dict, Path]] = []
    if phase == "pilot_harmful":
        path = OUT / "pilot" / "harmful.jsonl"
        for i, j, length, timing, delay in _cells():
            for rep in range(4):
                seed = 400000 + i * 1000 + j * 100 + rep
                cell = f"harmful_{length}_{timing}"
                for arm in ARMS:
                    jobs.append((_job(phase, cell, seed, arm, "invalidate", length, timing, delay, path), path))
    elif phase == "pilot_harmless":
        path = OUT / "pilot" / "harmless.jsonl"
        for i, j, length, timing, delay in _cells():
            for rep in range(4):
                seed = 410000 + i * 1000 + j * 100 + rep
                cell = f"harmless_{length}_{timing}"
                for arm in ("B", "C"):
                    jobs.append((_job(phase, cell, seed, arm, "harmless", length, timing, delay, path), path))
    elif phase == "confirm_harmful":
        path = OUT / "confirmatory" / "harmful.jsonl"
        for i, j, length, timing, delay in _cells():
            for rep in range(12):
                seed = 500000 + i * 1000 + j * 100 + rep
                cell = f"harmful_{length}_{timing}"
                for arm in ARMS:
                    jobs.append((_job(phase, cell, seed, arm, "invalidate", length, timing, delay, path), path))
    elif phase == "confirm_harmless":
        path = OUT / "confirmatory" / "harmless.jsonl"
        for i, j, length, timing, delay in _cells():
            for rep in range(20):
                seed = 610000 + i * 1000 + j * 100 + rep
                cell = f"harmless_{length}_{timing}"
                for arm in ("B", "C"):
                    jobs.append((_job(phase, cell, seed, arm, "harmless", length, timing, delay, path), path))
    elif phase == "queue":
        path = OUT / "queue" / "batches.jsonl"
        jobs.extend(_queue_jobs(path))
    elif phase == "recovery":
        path = OUT / "recovery" / "real.jsonl"
        for i, length in enumerate(LENGTHS):
            for rep in range(20):
                seed = 800000 + i * 100 + rep
                for arm in ARMS:
                    spec = _job(
                        phase,
                        f"recover_{length}",
                        seed,
                        arm,
                        "recoverable",
                        length,
                        "early",
                        0.25,
                        path,
                        t_restore_s=1.5,
                        initial_stock=1,
                    )
                    jobs.append((spec, path))
    elif phase == "workflow":
        path = OUT / "workflow" / "runs.jsonl"
        jobs.extend(_workflow_jobs(path))
    return jobs


def _job(phase, cell, seed, arm, kind, length, timing, delay, path, **extra) -> dict:
    return {
        "phase": phase,
        "cell_id": cell,
        "seed": seed,
        "arm": arm,
        "task_kind": kind,
        "length_tier": length,
        "timing_tier": timing,
        "target_words": WORDS[length],
        "t_change_s": delay,
        "pair_id": f"{cell}-s{seed}",
        "batch_id": f"{cell}-s{seed}",
        "scheduler_order": extra.get("scheduler_order", "fifo"),
        "t_restore_s": extra.get("t_restore_s"),
        "initial_stock": extra.get("initial_stock", 1),
        "task_plan": extra.get("task_plan"),
    }


def _queue_jobs(path: Path) -> list[tuple[dict, Path]]:
    jobs = []
    loads = {
        "high": {"burst3": [0.0, 0.05, 0.1], "mix4": [0.0, 0.05, 0.1, 0.15]},
        "medium": {"burst3": [0.0, 6.0, 12.0], "mix4": [0.0, 5.0, 10.0, 15.0]},
        "low": {"burst3": [0.0, 25.0, 50.0], "mix4": [0.0, 25.0, 50.0, 75.0]},
    }
    shapes = {
        "burst3": [("t0", "long", 450), ("t1", "short", 50), ("t2", "short", 50)],
        "mix4": [("t0", "short", 50), ("t1", "long", 450), ("t2", "short", 50), ("t3", "long", 450)],
    }
    plan = [
        ("high", "fifo", ARMS, 20),
        ("high", "sjf", ARMS, 20),
        ("medium", "fifo", ARMS, 20),
        ("medium", "sjf", ARMS, 20),
        ("low", "fifo", ("A", "C"), 10),
        ("low", "sjf", ("A", "C"), 10),
    ]
    for load, order, arms, n_batch in plan:
        for index in range(n_batch):
            shape = "burst3" if index < n_batch / 2 else "mix4"
            offsets = loads[load][shape]
            members = shapes[shape]
            task_plan = [
                {
                    "task_id": tid,
                    "length_tier": tier,
                    "words": words,
                    "arrival_offset_s": offsets[pos],
                }
                for pos, (tid, tier, words) in enumerate(members)
            ]
            seed = 700000 + {"high": 0, "medium": 100000, "low": 200000}[load] + {"fifo": 0, "sjf": 10000}[order] + index
            cell = f"queue_{load}_{order}_{shape}"
            for arm in arms:
                spec = _job(
                    "queue",
                    cell,
                    seed,
                    arm,
                    "invalidate",
                    "long",
                    "early",
                    0.25,
                    path,
                    scheduler_order=order,
                    task_plan=task_plan,
                )
                spec["batch_id"] = f"{cell}-b{index}"
                spec["pair_id"] = spec["batch_id"]
                jobs.append((spec, path))
    return jobs


COMPARE_SYSTEM = (
    "First call read_inventory and no other tool. "
    "Then write at least 200 words comparing that stock reading with the option of waiting. "
    "After the comparison, call reserve with qty set to the integer 1. "
    "The arguments must be a JSON object and qty must be an integer."
)
COMPARE_GOAL = (
    "Compare the live inventory reading with waiting, then reserve one unit. "
    "Call read_inventory before the explanation. Before reserve, write at least 200 words."
)
MIDPLAN_SYSTEM = (
    "Write the plan first. Call the reserve tool with qty set to the integer 1 only after the plan. "
    "The arguments must be a JSON object and qty must be an integer."
)
MIDPLAN_GOAL = (
    "Write a 200 word reservation plan that names the stock you observed, one sentence at a time. "
    "After the plan, reserve one unit."
)


def _workflow_jobs(path: Path) -> list[tuple[dict, Path]]:
    jobs = []
    classes = {
        "inventory": ("invalidate", ("A", "B", "C"), None, None),
        "compare": ("invalidate", ("A", "C"), COMPARE_GOAL, COMPARE_SYSTEM),
        "midplan": ("invalidate", ("A", "C"), MIDPLAN_GOAL, MIDPLAN_SYSTEM),
    }
    for i, (name, (kind, arms, goal, system)) in enumerate(classes.items()):
        for rep in range(20):
            seed = 900000 + i * 100 + rep
            for arm in arms:
                spec = _job("workflow", f"flow_{name}", seed, arm, kind, "mid", "mid", 0.8, path)
                spec["workflow_class"] = name
                spec["goal_text"] = goal
                spec["system_prompt"] = system
                jobs.append((spec, path))
    return jobs


async def run_phase(phase: str, concurrency: int = 4) -> None:
    jobs = _jobs(phase)
    if not jobs:
        raise SystemExit(f"unknown phase {phase}")
    path = jobs[0][1]
    done = _load_done(path)
    pending = []
    for spec, dest in jobs:
        key = (spec["phase"], spec["cell_id"], spec["seed"], spec["arm"], spec["scheduler_order"], spec["batch_id"])
        if key in done:
            continue
        pending.append((spec, dest))
    print(f"{phase}: {len(pending)} runs pending, {len(jobs) - len(pending)} already stored", flush=True)
    sem = asyncio.Semaphore(concurrency)
    await asyncio.gather(*[_one(spec, dest, sem) for spec, dest in pending])


def main() -> None:
    load_project_env(ROOT / ".env")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY missing")
    OUT.joinpath("protocol").mkdir(parents=True, exist_ok=True)
    (OUT / "protocol" / "v22_protocol.json").write_text(
        json.dumps(protocol(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    phases = [
        "pilot_harmful",
        "pilot_harmless",
        "confirm_harmful",
        "confirm_harmless",
        "queue",
        "recovery",
        "workflow",
    ]
    if phase != "all":
        phases = [phase]
    asyncio.run(_all(phases))


async def _all(phases: list[str]) -> None:
    for name in phases:
        await run_phase(name)


if __name__ == "__main__":
    main()
