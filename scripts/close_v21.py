"""封存 v2.1 主结果，并另写敏感性分析。不覆盖确认性 JSONL。"""

from __future__ import annotations

import hashlib
import json
import platform
import random
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from epochgate import __version__
from epochgate.replay import replay
from epochgate.workload import build_workload

ROOT = Path("results/experiments/v2_1")
OUT = Path("results/experiments/v4_0")
PROTOCOL = ROOT / "protocol" / "v21_protocol.json"
CONFIRM = ROOT / "confirmatory" / "traces.jsonl"
SENS_SEEDS = list(range(12000, 12030))
SCHEDULERS = ("fcfs", "srpt", "runtime-plas")
RHOS = (0.5, 1.0, 2.0)
MIXES = {
    "short_heavy": (("long", 0.20), ("short", 0.60), ("mid", 0.20)),
    "long_heavy": (("long", 0.60), ("short", 0.20), ("mid", 0.20)),
    "balanced": (("long", 1.0), ("short", 1.0), ("mid", 1.0)),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _bootstrap(values: list[float], seed: int, replicates: int = 10000) -> tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    means = []
    n = len(values)
    for _ in range(replicates):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    point = sum(values) / n
    return point, means[int(0.025 * (replicates - 1))], means[int(0.975 * (replicates - 1))]


def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _expected_policy(row: dict) -> bool:
    if row["load"] == "high":
        return row["trigger_class"] == "harmful" and row["rho"] == 0.5 and row["policy"] in {"G0", "G2"}
    if row["trigger_class"] == "harmful" and row["policy"] == "G1":
        return row["rho"] == 1.0
    if row["trigger_class"] == "harmless" and row["policy"] == "G0":
        return row["rho"] == 1.0
    if row["trigger_class"] == "harmful":
        return row["policy"] in {"G0", "G2"}
    return row["policy"] in {"G1", "G2"}


def audit_rows(rows: list[dict], protocol: dict) -> dict:
    """检查确认性行的样本数、配对和操作检验。

    输入：确认性汇总行和协议。
    输出：审计字典。不改原始文件。
    副作用：无。
    失败：无。不一致写入 ``issues``。
    """
    issues = []
    grouped: dict[tuple, list] = defaultdict(list)
    for row in rows:
        if not _expected_policy(row):
            issues.append({"kind": "unexpected_policy", "cell": row.get("cell_id"), "seed": row.get("seed")})
        key = (row["trigger_class"], row["load"], row["timing"], row["scheduler"], row["rho"], row["policy"])
        grouped[key].append(row)
    cell_counts = []
    for key, items in sorted(grouped.items()):
        seeds = [item["seed"] for item in items]
        cell_counts.append({"cell": list(key), "n": len(items), "unique_seeds": len(set(seeds))})
        if len(items) != 50 or len(set(seeds)) != 50:
            issues.append({"kind": "sample_count", "cell": list(key), "n": len(items)})
        if set(seeds) != set(range(5000, 5050)):
            issues.append({"kind": "seed_set", "cell": list(key)})
    hash_groups: dict[tuple, set] = defaultdict(set)
    for row in rows:
        hash_groups[(row["seed"], row["trigger_class"], row["load"], row["timing"], row["rho"], row["scheduler"])].add(row["arrival_hash"])
    hash_mismatch = sum(1 for hashes in hash_groups.values() if len(hashes) != 1)
    if hash_mismatch:
        issues.append({"kind": "arrival_hash", "n": hash_mismatch})
    checks = {
        "n_rows": len(rows),
        "old_attempt_committed": sum(row["old_attempt_committed"] for row in rows),
        "duplicate_side_effects": sum(row["duplicate_side_effects"] for row in rows),
        "harmless_g2_false_cancel": sum(row["false_cancel"] for row in rows if row["trigger_class"] == "harmless" and row["policy"] == "G2"),
        "harmless_g2_restart": sum(row["false_restart_count"] for row in rows if row["trigger_class"] == "harmless" and row["policy"] == "G2"),
        "harmless_g1_missing_restart": sum(1 for row in rows if row["trigger_class"] == "harmless" and row["policy"] == "G1" and row["false_restart_count"] < 1),
        "protocol_hash_values": sorted({row["protocol_hash"] for row in rows}),
        "code_versions": sorted({row["code_version"] for row in rows}),
        "expected_protocol_hash": protocol["protocol_hash"],
    }
    if checks["protocol_hash_values"] != [protocol["protocol_hash"]]:
        issues.append({"kind": "protocol_hash", "found": checks["protocol_hash_values"]})
    if any(checks[name] for name in ("old_attempt_committed", "duplicate_side_effects", "harmless_g2_false_cancel", "harmless_g2_restart", "harmless_g1_missing_restart")):
        issues.append({"kind": "manipulation_check", "checks": checks})
    release = defaultdict(dict)
    for row in rows:
        if row["trigger_class"] == "harmful" and row["policy"] in {"G1", "G2"} and row["rho"] == 1.0 and row["load"] == "medium":
            release[(row["seed"], row["scheduler"])][row["policy"]] = row["focal_release_at"]
    mismatch = 0
    for arms in release.values():
        if "G1" in arms and "G2" in arms and abs(arms["G1"] - arms["G2"]) > 1e-6:
            mismatch += 1
    if mismatch:
        issues.append({"kind": "release_mismatch", "n": mismatch})
    return {"cells": cell_counts, "checks": checks, "issues": issues, "passed": not issues and len(rows) == 2400}


def _replay_stored(row: dict, *, audit_order: bool) -> dict:
    workload = build_workload(
        seed=row["seed"],
        trigger_class=row["trigger_class"],
        timing=row["timing"],
        rho=row["rho"],
        load=row["load"],
        interarrival_s=row["interarrival_s"],
        slots=row["slots"],
    )
    result = replay(
        workload,
        policy=row["policy"],
        scheduler=row["scheduler"],
        phase="verify",
        protocol_hash=row["protocol_hash"],
        audit_order=audit_order,
    )
    return result.summary


def verify_replay(rows: list[dict]) -> dict:
    """用当前默认回放重算确认性，核对 W95 是否与冻结文件一致。

    输入：冻结汇总行。
    输出：不一致条数，以及 P90/P99 和 PLAS 排序差异。
    副作用：无。不写回确认性文件。
    失败：无。
    """
    mismatches = []
    quantiles = []
    order_rows = []
    for index, row in enumerate(rows):
        fresh = _replay_stored(row, audit_order=row["scheduler"] == "runtime-plas")
        if abs(fresh["W95"] - row["W95"]) > 1e-6 or fresh["arrival_hash"] != row["arrival_hash"]:
            mismatches.append({"cell_id": row["cell_id"], "seed": row["seed"], "stored": row["W95"], "fresh": fresh["W95"]})
        quantiles.append({**{key: row[key] for key in ("trigger_class", "load", "timing", "scheduler", "rho", "policy", "seed")}, "W90": fresh["W90"], "W95": fresh["W95"], "W99": fresh["W99"]})
        if row["scheduler"] == "runtime-plas":
            order_rows.append(
                {
                    "trigger_class": row["trigger_class"],
                    "load": row["load"],
                    "rho": row["rho"],
                    "policy": row["policy"],
                    "seed": row["seed"],
                    "dispatch_decisions": fresh.get("dispatch_decisions", 0),
                    "order_differs_from_fcfs": fresh.get("order_differs_from_fcfs", 0),
                }
            )
        if index and index % 400 == 0:
            print(f"verified {index}", flush=True)
    return {"n": len(rows), "mismatches": mismatches, "quantiles": quantiles, "order_rows": order_rows}


def _paired(rows: list[dict], trigger: str, base: str, alt: str, metric: str) -> dict[tuple, dict]:
    grouped: dict[tuple, dict] = defaultdict(dict)
    for row in rows:
        if row["trigger_class"] != trigger or row["policy"] not in {base, alt}:
            continue
        key = (row["load"], row["timing"], row["scheduler"], row["rho"], row["seed"])
        grouped[key][row["policy"]] = row[metric]
    cells: dict[tuple, list] = defaultdict(list)
    for key, arms in grouped.items():
        if base in arms and alt in arms:
            cell = key[:4]
            cells[cell].append({"seed": key[4], "delta": arms[base] - arms[alt], "base": arms[base], "alt": arms[alt]})
    return cells


def quantile_report(quantiles: list[dict], protocol: dict) -> list[dict]:
    report = []
    for trigger, base, alt in (("harmful", "G0", "G2"), ("harmless", "G1", "G2")):
        for metric in ("W90", "W95", "W99"):
            cells = _paired(quantiles, trigger, base, alt, metric)
            for cell, items in sorted(cells.items()):
                deltas = [item["delta"] for item in items]
                bases = [item["base"] for item in items]
                point, low, high = _bootstrap(deltas, protocol["bootstrap_seed"])
                base_mean = sum(bases) / len(bases)
                report.append(
                    {
                        "trigger": trigger,
                        "metric": metric,
                        "load": cell[0],
                        "timing": cell[1],
                        "scheduler": cell[2],
                        "rho": cell[3],
                        "n": len(items),
                        "delta_mean": point,
                        "ci_low": low,
                        "ci_high": high,
                        "relative": point / base_mean if abs(base_mean) > 1e-9 else 0.0,
                        "sign": "positive" if point > 1e-9 else "negative" if point < -1e-9 else "zero",
                    }
                )
    return report


def leave_one_out(rows: list[dict]) -> list[dict]:
    report = []
    for trigger, base, alt in (("harmful", "G0", "G2"), ("harmless", "G1", "G2")):
        cells = _paired(rows, trigger, base, alt, "W95")
        for cell, items in sorted(cells.items()):
            full = [item["delta"] for item in items]
            full_mean = sum(full) / len(full)
            base_mean = sum(item["base"] for item in items) / len(items)
            loo = []
            for dropped in items:
                kept = [item["delta"] for item in items if item["seed"] != dropped["seed"]]
                loo.append(sum(kept) / len(kept))
            relative = full_mean / base_mean if abs(base_mean) > 1e-9 else 0.0
            loo_rel = [value / base_mean if abs(base_mean) > 1e-9 else 0.0 for value in loo]
            negatives = sum(1 for value in full if value < -1e-9)
            zeros = sum(1 for value in full if abs(value) <= 1e-9)
            report.append(
                {
                    "trigger": trigger,
                    "load": cell[0],
                    "scheduler": cell[2],
                    "rho": cell[3],
                    "n": len(items),
                    "delta_mean": full_mean,
                    "relative": relative,
                    "loo_min": min(loo),
                    "loo_max": max(loo),
                    "loo_relative_min": min(loo_rel),
                    "loo_relative_max": max(loo_rel),
                    "negative_seeds": negatives,
                    "zero_seeds": zeros,
                    "positive_seeds": len(full) - negatives - zeros,
                    "loo_crosses_zero": min(loo) < 0 < max(loo),
                    "loo_relative_falls_below_10pct": relative >= 0.10 and min(loo_rel) < 0.10,
                }
            )
    return report


def scheduler_report(order_rows: list[dict]) -> dict:
    buckets: dict[tuple, dict] = {}
    for row in order_rows:
        key = (row["trigger_class"], row["load"], row["rho"], row["policy"])
        bucket = buckets.setdefault(key, {"decisions": 0, "diffs": 0, "traces_with_diff": 0, "n": 0})
        bucket["decisions"] += row["dispatch_decisions"]
        bucket["diffs"] += row["order_differs_from_fcfs"]
        bucket["traces_with_diff"] += int(row["order_differs_from_fcfs"] > 0)
        bucket["n"] += 1
    cells = [
        {
            "trigger": key[0],
            "load": key[1],
            "rho": key[2],
            "policy": key[3],
            **value,
            "diff_rate": value["diffs"] / value["decisions"] if value["decisions"] else 0.0,
        }
        for key, value in sorted(buckets.items())
    ]
    return {"cells": cells}


def _run_factor(spec: dict) -> list[dict]:
    rows = []
    for seed in SENS_SEEDS:
        for scheduler in SCHEDULERS:
            for rho in spec["rhos"]:
                for trigger, policies in spec["arms"]:
                    for policy in policies:
                        workload = build_workload(
                            seed=seed,
                            trigger_class=trigger,
                            timing=spec["timing"],
                            rho=rho,
                            load=spec["load"],
                            interarrival_s=spec["interarrival_s"],
                            slots=spec["slots"],
                            mix=spec["mix"],
                            service_noise=spec["service_noise"],
                            arrival_jitter=spec["arrival_jitter"],
                        )
                        result = replay(workload, policy=policy, scheduler=scheduler, phase="sensitivity")
                        row = dict(result.summary)
                        row["factor"] = spec["factor"]
                        rows.append(row)
    return rows


def sensitivity_specs(protocol: dict) -> list[dict]:
    medium = float(protocol["interarrival_s"]["medium"])
    high = float(protocol["interarrival_s"]["high"])
    both = (("harmful", ("G0", "G2")), ("harmless", ("G1", "G2")))
    harmful = (("harmful", ("G0", "G2")),)
    specs = []
    for slots in (2, 8):
        specs.append(_spec(f"slots-{slots}", slots, medium, "medium", "early", 0.0, 0.0, None, RHOS, both))
        specs.append(_spec(f"slots-{slots}-high", slots, high, "high", "early", 0.0, 0.0, None, (0.5,), harmful))
    for noise in (0.10, 0.25):
        specs.append(_spec(f"service-noise-{noise:.2f}", 4, medium, "medium", "early", noise, 0.0, None, RHOS, both))
        specs.append(_spec(f"service-noise-{noise:.2f}-high", 4, high, "high", "early", noise, 0.0, None, (0.5,), harmful))
    for jitter in (0.10, 0.25):
        specs.append(_spec(f"arrival-jitter-{jitter:.2f}", 4, medium, "medium", "early", 0.0, jitter, None, RHOS, both))
        specs.append(_spec(f"arrival-jitter-{jitter:.2f}-high", 4, high, "high", "early", 0.0, jitter, None, (0.5,), harmful))
    for name, mix in MIXES.items():
        specs.append(_spec(f"mix-{name}", 4, medium, "medium", "early", 0.0, 0.0, mix, RHOS, both))
    specs.append(_spec("timing-mid", 4, medium, "medium", "mid", 0.0, 0.0, None, RHOS, both))
    return specs


def _spec(factor, slots, dt, load, timing, noise, jitter, mix, rhos, arms) -> dict:
    return {
        "factor": factor,
        "slots": slots,
        "interarrival_s": dt,
        "load": load,
        "timing": timing,
        "service_noise": noise,
        "arrival_jitter": jitter,
        "mix": mix,
        "rhos": rhos,
        "arms": arms,
    }


def summarize_sensitivity(rows: list[dict]) -> list[dict]:
    report = []
    grouped: dict[tuple, dict] = defaultdict(dict)
    for row in rows:
        if row["policy"] not in {"G0", "G1", "G2"}:
            continue
        key = (row["factor"], row["trigger_class"], row["load"], row["scheduler"], row["rho"], row["seed"])
        grouped[key][row["policy"]] = row
    clean: dict[tuple, list] = defaultdict(list)
    bases: dict[tuple, list] = defaultdict(list)
    recipients: dict[tuple, list] = defaultdict(list)
    for key, arms in grouped.items():
        trigger = key[1]
        base_name, alt_name = ("G0", "G2") if trigger == "harmful" else ("G1", "G2")
        if base_name not in arms or alt_name not in arms:
            continue
        cell = key[:5]
        clean[cell].append(arms[base_name]["W95"] - arms[alt_name]["W95"])
        bases[cell].append(arms[base_name]["W95"])
        recipients[cell].append(arms[alt_name]["slot_reacquired_by"])
    for cell, deltas in sorted(clean.items()):
        point, low, high = _bootstrap(deltas, 20261103)
        base_mean = sum(bases[cell]) / len(bases[cell])
        report.append(
            {
                "factor": cell[0],
                "trigger": cell[1],
                "load": cell[2],
                "scheduler": cell[3],
                "rho": cell[4],
                "n": len(deltas),
                "delta_mean": point,
                "ci_low": low,
                "ci_high": high,
                "relative": point / base_mean if abs(base_mean) > 1e-9 else 0.0,
                "sign": "positive" if low > 0 else "negative" if high < 0 else "zero_or_crosses",
                "point_sign": "positive" if point > 1e-9 else "negative" if point < -1e-9 else "zero",
                "slot_reacquired_by_g_alt": dict(__import__("collections").Counter(recipients[cell])),
            }
        )
    return report


def timeline(protocol: dict, seed: int = 5000) -> dict:
    medium = float(protocol["interarrival_s"]["medium"])
    payload = {"seed": seed, "scheduler": "fcfs", "rho": 0.5, "load": "medium", "timing": "early", "trigger_class": "harmful", "arms": {}}
    for policy in ("G0", "G2", "G2-delayed-release"):
        workload = build_workload(
            seed=5000,
            trigger_class="harmful",
            timing="early",
            rho=0.5,
            load="medium",
            interarrival_s=medium,
            slots=4,
        )
        result = replay(workload, policy=policy, scheduler="fcfs", phase="timeline")
        payload["arms"][policy] = {
            "summary": {key: result.summary[key] for key in ("W95", "focal_release_at", "slot_reacquired_by", "successor_service_s", "reclaimed_slot_s")},
            "attempts": [
                {
                    "role": row["workflow_role"],
                    "workflow_id": row["workflow_id"],
                    "arrival_time": row["arrival_time"],
                    "service_demand": row["service_demand"],
                    "service_started_at": row["service_started_at"],
                    "slot_released_at": row["slot_released_at"],
                    "service_completed_at": row["service_completed_at"],
                    "revocation_time": row["revocation_time"],
                }
                for row in result.rows
                if row["workflow_role"] != "bystander" or row["workflow_id"] in {"wf-b00", "wf-b01", "wf-b02"}
            ],
        }
    payload["event_time"] = 3.2
    payload["note"] = "机制图数据。FCFS、有害、rho_h=0.5、中负载、早时机。旁观者只保留最先到达的三个。种子 5000 的 P95 差为 0；种子 5005 有可见差距。"
    return payload


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    """执行封存审计和敏感性回放。

    输入：无。读取冻结的 v2.1 文件。
    输出：``results/experiments/v4_0`` 下的审计 JSON。
    副作用：写新目录。不修改确认性 JSONL。
    失败：确认性文件缺失时抛出 ``FileNotFoundError``。
    """
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    rows = _read(CONFIRM)
    data_audit = audit_rows(rows, protocol)
    print("data audit", data_audit["passed"], "issues", len(data_audit["issues"]), flush=True)
    verified = verify_replay(rows)
    print("replay mismatches", len(verified["mismatches"]), flush=True)
    quantiles = quantile_report(verified["quantiles"], protocol)
    loo = leave_one_out(rows)
    order = scheduler_report(verified["order_rows"])
    sens_rows = []
    for spec in sensitivity_specs(protocol):
        print("sensitivity", spec["factor"], flush=True)
        sens_rows.extend(_run_factor(spec))
    sens_path = OUT / "sensitivity" / "traces.jsonl"
    sens_path.parent.mkdir(parents=True, exist_ok=True)
    with sens_path.open("w", encoding="utf-8") as handle:
        for row in sens_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    sensitivity = summarize_sensitivity(sens_rows)
    figure = timeline(protocol, 5000)
    _write(OUT / "figures" / "timeline_seed5005.json", timeline(protocol, 5005))
    files = {
        "protocol": PROTOCOL,
        "confirmatory": CONFIRM,
        "boundary": ROOT / "boundary" / "traces.jsonl",
        "pilot": ROOT / "pilot" / "traces.jsonl",
        "api": ROOT / "api" / "events.jsonl",
        "analyze": Path("scripts/analyze_v21.py"),
        "replay": Path("src/epochgate/replay.py"),
        "workload": Path("src/epochgate/workload.py"),
        "close": Path("scripts/close_v21.py"),
    }
    manifest = {
        "dataset": "v2.1 confirmatory",
        "package_version": __version__,
        "source_commit": _git_head(),
        "protocol_hash": protocol["protocol_hash"],
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "bootstrap_seed": protocol["bootstrap_seed"],
        "bootstrap_replicates": protocol["bootstrap_replicates"],
        "threshold_definition": {
            "relative_10pct": "点估计 delta_mean / baseline_mean >= 0.10",
            "ci_low_positive": "绝对差值 W(base)-W(G2) 的 bootstrap 2.5% 分位 > 0",
            "not_claimed": "区间下界本身达到 10%",
        },
        "commands": [
            "python -m uv run python scripts/analyze_v21.py",
            "python -m uv run python scripts/close_v21.py",
        ],
        "file_sha256": {name: _sha256(path) for name, path in files.items() if path.exists()},
        "sensitivity_seeds": SENS_SEEDS,
        "sensitivity_n": len(SENS_SEEDS),
        "exclusions": [],
        "replay_mismatch_count": len(verified["mismatches"]),
        "data_audit_passed": data_audit["passed"],
        "confirmatory_rows": len(rows),
        "note": "确认性 JSONL 在包版本 3.0.0 的回放器上生成，本脚本只重算核对。敏感性种子从 12000 起，不进入主表。",
    }
    _write(OUT / "manifest" / "v21_final_manifest.json", manifest)
    _write(OUT / "audit" / "v21_data_audit.json", data_audit)
    _write(OUT / "audit" / "v21_replay_verify.json", {"n": verified["n"], "mismatches": verified["mismatches"]})
    _write(OUT / "audit" / "v21_quantile_sensitivity.json", quantiles)
    _write(OUT / "audit" / "v21_leave_one_out.json", loo)
    _write(OUT / "audit" / "v21_scheduler_audit.json", order)
    _write(OUT / "sensitivity" / "v21_sensitivity.json", sensitivity)
    _write(OUT / "figures" / "timeline_seed5000.json", figure)
    _write(OUT / "figures" / "main_table_source.json", json.loads((ROOT / "reports" / "v21_summary.json").read_text(encoding="utf-8")))
    print(json.dumps({"audit_passed": data_audit["passed"], "mismatches": len(verified["mismatches"]), "sensitivity_rows": len(sens_rows), "sensitivity_cells": len(sensitivity)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
