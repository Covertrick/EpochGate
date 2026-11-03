"""校准负载、跑 pilot，并在协议冻结后跑确认性回放。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from epochgate.replay import replay
from epochgate.workload import build_workload

ROOT = Path("results/experiments/v2_1")
PROTOCOL_PATH = ROOT / "protocol" / "v21_protocol.json"
SCHEDULERS = ("fcfs", "srpt", "runtime-plas")
RHOS = (0.5, 1.0, 2.0)


def _cell_id(phase: str, trigger: str, load: str, timing: str, scheduler: str, policy: str, rho: float) -> str:
    return f"{phase}_{trigger}_{load}_{timing}_{scheduler}_{policy}_rho{rho:g}"


def _run_one(seed: int, **kwargs) -> dict:
    policy = kwargs.pop("policy")
    scheduler = kwargs.pop("scheduler")
    phase = kwargs.pop("phase")
    protocol_hash = kwargs.pop("protocol_hash", "")
    workload = build_workload(seed=seed, **kwargs)
    result = replay(
        workload,
        policy=policy,
        scheduler=scheduler,
        phase=phase,
        protocol_hash=protocol_hash,
    )
    row = dict(result.summary)
    row["cell_id"] = _cell_id(
        phase,
        kwargs["trigger_class"],
        kwargs["load"],
        kwargs["timing"],
        scheduler,
        policy,
        kwargs["rho"],
    )
    return row


def _median_wait(interarrival: float, slots: int, seeds: list[int]) -> float:
    waits = []
    for seed in seeds:
        row = _run_one(
            seed,
            trigger_class="harmful",
            timing="early",
            rho=1.0,
            load="calibration",
            interarrival_s=interarrival,
            slots=slots,
            policy="G0",
            scheduler="fcfs",
            phase="calibration",
        )
        waits.append(row["W50"])
    waits.sort()
    return waits[len(waits) // 2]


def calibrate(slots: int, low: float, high: float, seeds: list[int]) -> float:
    """二分到达间隔，使 FCFS+G0 的旁观者等待中位数落在区间内。

    输入：槽位数、目标下界和上界、校准种子。
    输出：到达间隔秒数。
    副作用：无。
    失败：区间内找不到间隔时返回最接近的一端。
    """
    lo, hi = 0.15, 30.0
    best = hi
    best_gap = 1e9
    for _ in range(18):
        mid = (lo + hi) / 2
        median = _median_wait(mid, slots, seeds)
        target = (low + high) / 2
        gap = abs(median - target)
        if gap < best_gap and low <= median <= high:
            best, best_gap = mid, gap
        elif low <= median <= high:
            best, best_gap = mid, gap
        if median > high:
            lo = mid
        elif median < low:
            hi = mid
        else:
            best = mid
            break
    return round(best, 4)


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _main_jobs(phase: str, seeds: list[int], medium_dt: float, high_dt: float, slots: int) -> list[dict]:
    jobs = []
    def add(trigger, load, timing, policies, rhos, dt):
        for seed in seeds:
            for scheduler in SCHEDULERS:
                for policy in policies:
                    for rho in rhos:
                        jobs.append(
                            dict(
                                seed=seed,
                                trigger_class=trigger,
                                timing=timing,
                                rho=rho,
                                load=load,
                                interarrival_s=dt,
                                slots=slots,
                                policy=policy,
                                scheduler=scheduler,
                                phase=phase,
                            )
                        )
    add("harmful", "medium", "early", ("G0", "G2"), RHOS, medium_dt)
    add("harmless", "medium", "early", ("G1", "G2"), RHOS, medium_dt)
    add("harmful", "medium", "early", ("G1",), (1.0,), medium_dt)
    add("harmless", "medium", "early", ("G0",), (1.0,), medium_dt)
    add("harmful", "high", "early", ("G0", "G2"), (0.5,), high_dt)
    return jobs


def _boundary_jobs(seeds: list[int], medium_dt: float, low_dt: float, slots: int) -> list[dict]:
    jobs = []
    def add(seed, trigger, load, timing, policy, dt, rho=0.5, scheduler="fcfs"):
        jobs.append(
            dict(
                seed=seed,
                trigger_class=trigger,
                timing=timing,
                rho=rho,
                load=load,
                interarrival_s=dt,
                slots=slots,
                policy=policy,
                scheduler=scheduler,
                phase="boundary",
            )
        )
    for seed in seeds:
        for policy in ("G0", "G2"):
            add(seed, "harmful", "low", "early", policy, low_dt)
            add(seed, "harmful", "medium", "mid", policy, medium_dt)
        for policy in ("G0", "G2", "G2-delayed-release"):
            add(seed, "harmful", "medium", "early", policy, medium_dt)
        trigger = "harmful" if seed % 2 == 0 else "harmless"
        policies = ("G0", "G2") if trigger == "harmful" else ("G1", "G2")
        for policy in policies:
            add(seed, trigger, "mixed", "early", policy, medium_dt)
    return jobs


def _required_n(deltas: list[float], baseline: list[float]) -> int:
    if len(deltas) < 2:
        return 50
    mean = sum(deltas) / len(deltas)
    var = sum((item - mean) ** 2 for item in deltas) / (len(deltas) - 1)
    base = sum(baseline) / len(baseline)
    mde = 0.10 * abs(base)
    if mde <= 1e-9:
        return 200
    n = int((7.84 * var / (mde ** 2)) + 0.999)
    return max(1, n)


def freeze_protocol(pilot_path: Path, calibration: dict) -> dict:
    """用 pilot 配对差决定确认性 N，并写下不再改动的门槛。

    输入：pilot JSONL 和校准参数。
    输出：协议字典。
    副作用：写出协议 JSON。
    失败：pilot 里缺少 G0/G2 配对时仍写出 N=50，并记下原因。
    """
    rows = [json.loads(line) for line in pilot_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    grouped: dict[tuple, dict[str, dict]] = {}
    for row in rows:
        if row["policy"] not in {"G0", "G1", "G2"}:
            continue
        key = (row["trigger_class"], row["load"], row["scheduler"], row["rho"], row["seed"])
        grouped.setdefault(key, {})[row["policy"]] = row
    requirements = []
    for key, arms in grouped.items():
        trigger = key[0]
        base_name, alt_name = ("G0", "G2") if trigger == "harmful" else ("G1", "G2")
        if base_name not in arms or alt_name not in arms:
            continue
        requirements.append(
            {
                "trigger": trigger,
                "load": key[1],
                "scheduler": key[2],
                "rho": key[3],
                "delta": arms[base_name]["W95"] - arms[alt_name]["W95"],
                "baseline": arms[base_name]["W95"],
            }
        )
    by_cell: dict[tuple, dict] = {}
    for item in requirements:
        cell = (item["trigger"], item["load"], item["scheduler"], item["rho"])
        bucket = by_cell.setdefault(cell, {"deltas": [], "baseline": []})
        bucket["deltas"].append(item["delta"])
        bucket["baseline"].append(item["baseline"])
    cell_n = []
    dropped = []
    for cell, bucket in sorted(by_cell.items()):
        needed = _required_n(bucket["deltas"], bucket["baseline"])
        record = {
            "trigger": cell[0],
            "load": cell[1],
            "scheduler": cell[2],
            "rho": cell[3],
            "required_n_80": needed,
        }
        if needed > 200:
            dropped.append(record)
        else:
            cell_n.append(record)
    confirmatory_n = 50 if not cell_n else max(50, max(item["required_n_80"] for item in cell_n))
    protocol = {
        "protocol_version": "3.1.0",
        "package_version": "3.0.0",
        "slots": calibration["slots"],
        "interarrival_s": {
            "low": calibration["low"],
            "medium": calibration["medium"],
            "high": calibration["high"],
        },
        "calibration_seeds": calibration["seeds"],
        "calibration_median_wait_s": calibration["medians"],
        "pilot_seeds": list(range(1000, 1015)),
        "confirmatory_seeds_start": 5000,
        "boundary_seeds": list(range(8000, 8020)),
        "bootstrap_seed": 20261103,
        "bootstrap_replicates": 10000,
        "engineering_thresholds": {
            "relative_p95_wait_improvement": 0.10,
            "completion_rate_drop_pp": 0.02,
        },
        "confirmatory_n": confirmatory_n,
        "cell_power": cell_n,
        "dropped_cells": dropped,
        "drop_reason": "功效要求超过 200 条的格子不进入确认性主格。" if dropped else "",
    }
    raw = json.dumps(protocol, ensure_ascii=False, sort_keys=True).encode()
    protocol["protocol_hash"] = hashlib.sha256(raw).hexdigest()
    PROTOCOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROTOCOL_PATH.write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
    return protocol


def _execute(jobs: list[dict], path: Path, protocol_hash: str) -> None:
    if path.exists():
        path.unlink()
    checks = []
    for job in jobs:
        job = dict(job)
        job["protocol_hash"] = protocol_hash
        row = _run_one(job.pop("seed"), **job)
        _append(path, row)
        if row["trigger_class"] == "harmful" and row["policy"] in {"G1", "G2"}:
            checks.append(row)
    _assert_release_match(path)


def _assert_release_match(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    paired: dict[tuple, dict] = {}
    for row in rows:
        if row["trigger_class"] != "harmful" or row["policy"] not in {"G1", "G2"}:
            continue
        key = (row["seed"], row["scheduler"], row["rho"], row["load"], row["timing"])
        paired.setdefault(key, {})[row["policy"]] = row["focal_release_at"]
    for key, arms in paired.items():
        if "G1" in arms and "G2" in arms and abs(arms["G1"] - arms["G2"]) > 1e-6:
            raise SystemExit(f"harmful release mismatch at {key}: {arms}")


def main() -> None:
    """按阶段校准、试跑或确认。

    输入：命令行 ``--phase``。
    输出：JSONL 与协议文件。
    副作用：追加或覆盖本阶段结果文件。
    失败：有害臂释放时刻不一致时退出。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("pilot", "confirmatory"), required=True)
    args = parser.parse_args()
    if args.phase == "pilot":
        seeds = [1, 2, 3, 4, 5]
        slots = 4
        medium = calibrate(slots, 2.0, 3.5, seeds)
        median = _median_wait(medium, slots, seeds)
        if median < 2.0:
            slots = 2
            medium = calibrate(slots, 2.0, 3.5, seeds)
            median = _median_wait(medium, slots, seeds)
        high = calibrate(slots, 5.0, 7.0, seeds)
        low = 20.0
        calibration = {
            "slots": slots,
            "medium": medium,
            "high": high,
            "low": low,
            "seeds": seeds,
            "medians": {
                "medium": median,
                "high": _median_wait(high, slots, seeds),
                "low": _median_wait(low, slots, seeds),
            },
        }
        (ROOT / "protocol").mkdir(parents=True, exist_ok=True)
        (ROOT / "protocol" / "calibration.json").write_text(
            json.dumps(calibration, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        jobs = _main_jobs("pilot", list(range(1000, 1015)), medium, high, slots)
        _execute(jobs, ROOT / "pilot" / "traces.jsonl", "")
        protocol = freeze_protocol(ROOT / "pilot" / "traces.jsonl", calibration)
        print(json.dumps({"confirmatory_n": protocol["confirmatory_n"], "dropped": protocol["dropped_cells"], "slots": slots, "interarrival": protocol["interarrival_s"], "medians": calibration["medians"]}, ensure_ascii=False))
        return
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    n = int(protocol["confirmatory_n"])
    slots = int(protocol["slots"])
    medium = float(protocol["interarrival_s"]["medium"])
    high = float(protocol["interarrival_s"]["high"])
    low = float(protocol["interarrival_s"]["low"])
    dropped = {
        (item["trigger"], item["load"], item["scheduler"], item["rho"])
        for item in protocol["dropped_cells"]
    }
    jobs = []
    for job in _main_jobs("confirmatory", list(range(5000, 5000 + n)), medium, high, slots):
        key = (job["trigger_class"], job["load"], job["scheduler"], job["rho"])
        is_check = (job["trigger_class"] == "harmful" and job["policy"] == "G1" and job["rho"] == 1.0) or (
            job["trigger_class"] == "harmless" and job["policy"] == "G0" and job["rho"] == 1.0
        )
        if key in dropped and not is_check:
            continue
        jobs.append(job)
    _execute(jobs, ROOT / "confirmatory" / "traces.jsonl", protocol["protocol_hash"])
    boundary = _boundary_jobs(list(range(8000, 8020)), medium, low, slots)
    _execute(boundary, ROOT / "boundary" / "traces.jsonl", protocol["protocol_hash"])
    print(json.dumps({"confirmatory_jobs": len(jobs), "boundary_jobs": len(boundary)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
