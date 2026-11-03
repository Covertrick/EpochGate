"""汇总确认性回放。Bootstrap 以 trace 为单位。"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("results/experiments/v2_1")
OUT = ROOT / "reports"


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _pair(rows: list[dict], base: str, alt: str) -> list[dict]:
    grouped: dict[tuple, dict] = defaultdict(dict)
    for row in rows:
        if row["policy"] not in {base, alt}:
            continue
        key = (row["load"], row["timing"], row["scheduler"], row["rho"], row["seed"], row["trigger_class"])
        grouped[key][row["policy"]] = row
    pairs = []
    for key, arms in grouped.items():
        if base in arms and alt in arms:
            pairs.append({"key": key, "base": arms[base], "alt": arms[alt]})
    return pairs


def _bootstrap(values: list[float], seed: int, replicates: int) -> tuple[float, float, float]:
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
    low = means[int(0.025 * (replicates - 1))]
    high = means[int(0.975 * (replicates - 1))]
    return point, low, high


def _cell_table(pairs: list[dict], protocol: dict, metric: str) -> list[dict]:
    buckets: dict[tuple, list] = defaultdict(list)
    for item in pairs:
        load, timing, scheduler, rho, _seed, trigger = item["key"]
        buckets[(trigger, load, timing, scheduler, rho)].append(item)
    table = []
    for key, items in sorted(buckets.items()):
        base_vals = [item["base"][metric] for item in items]
        alt_vals = [item["alt"][metric] for item in items]
        deltas = [b - a for b, a in zip(base_vals, alt_vals)]
        point, low, high = _bootstrap(deltas, protocol["bootstrap_seed"], protocol["bootstrap_replicates"])
        base_mean = sum(base_vals) / len(base_vals)
        relative = point / base_mean if abs(base_mean) > 1e-9 else 0.0
        table.append(
            {
                "trigger": key[0],
                "load": key[1],
                "timing": key[2],
                "scheduler": key[3],
                "rho": key[4],
                "n": len(items),
                "baseline_mean": base_mean,
                "delta_mean": point,
                "ci_low": low,
                "ci_high": high,
                "relative_to_baseline": relative,
                "meets_10pct": relative >= 0.10 and low > 0,
            }
        )
    return table


def _completion(pairs: list[dict]) -> list[dict]:
    buckets: dict[tuple, list] = defaultdict(list)
    for item in pairs:
        load, timing, scheduler, rho, _seed, trigger = item["key"]
        buckets[(trigger, load, timing, scheduler, rho)].append(item)
    rows = []
    for key, items in sorted(buckets.items()):
        base = sum(item["base"]["bystander_completion_rate"] for item in items) / len(items)
        alt = sum(item["alt"]["bystander_completion_rate"] for item in items) / len(items)
        rows.append(
            {
                "trigger": key[0],
                "load": key[1],
                "scheduler": key[3],
                "rho": key[4],
                "n": len(items),
                "baseline_completion": base,
                "alt_completion": alt,
                "completion_drop": base - alt,
                "harmful_drop": (base - alt) > 0.02,
            }
        )
    return rows


def _checks(rows: list[dict]) -> dict:
    harmful = [row for row in rows if row["trigger_class"] == "harmful"]
    harmless = [row for row in rows if row["trigger_class"] == "harmless"]
    release_mismatch = 0
    grouped: dict[tuple, dict] = defaultdict(dict)
    for row in harmful:
        if row["policy"] in {"G1", "G2"} and row["rho"] == 1.0 and row["load"] == "medium":
            grouped[(row["seed"], row["scheduler"])][row["policy"]] = row["focal_release_at"]
    for arms in grouped.values():
        if "G1" in arms and "G2" in arms and abs(arms["G1"] - arms["G2"]) > 1e-6:
            release_mismatch += 1
    g2 = [row for row in harmless if row["policy"] == "G2"]
    g1 = [row for row in harmless if row["policy"] == "G1"]
    return {
        "harmful_release_mismatch": release_mismatch,
        "harmless_g2_false_cancel": sum(row["false_cancel"] for row in g2),
        "harmless_g2_restart": sum(row["false_restart_count"] for row in g2),
        "harmless_g1_missing_restart": sum(1 for row in g1 if row["false_restart_count"] < 1),
        "old_attempt_committed": sum(row["old_attempt_committed"] for row in rows),
        "duplicate_side_effects": sum(row["duplicate_side_effects"] for row in rows),
        "arrival_hash_mismatch": _hash_mismatch(rows),
    }


def _hash_mismatch(rows: list[dict]) -> int:
    grouped: dict[tuple, set] = defaultdict(set)
    for row in rows:
        grouped[(row["seed"], row["trigger_class"], row["load"], row["timing"], row["rho"], row["scheduler"])].add(row["arrival_hash"])
    return sum(1 for hashes in grouped.values() if len(hashes) > 1)


def _mode(rows: list[dict], policy: str) -> dict:
    picked = [row["slot_reacquired_by"] for row in rows if row["policy"] == policy]
    return dict(Counter(picked))


def _markdown_table(rows: list[dict], columns: list[str]) -> str:
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        cells = []
        for column in columns:
            value = row[column]
            if isinstance(value, float):
                cells.append(f"{value:.4f}")
            else:
                cells.append(str(value))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep, *body])


def main() -> None:
    """写出检查、四张主表和机制计数。

    输入：无。读取确认性 JSONL 和协议。
    输出：``results/experiments/v2_1/reports`` 下的 JSON。
    副作用：覆盖报告文件。
    失败：协议缺失时抛出 ``FileNotFoundError``。
    """
    protocol = json.loads((ROOT / "protocol" / "v21_protocol.json").read_text(encoding="utf-8"))
    rows = _read(ROOT / "confirmatory" / "traces.jsonl")
    boundary = _read(ROOT / "boundary" / "traces.jsonl")
    checks = _checks(rows)
    harmful = _pair([row for row in rows if row["trigger_class"] == "harmful" and row["policy"] in {"G0", "G2"}], "G0", "G2")
    harmless = _pair([row for row in rows if row["trigger_class"] == "harmless" and row["policy"] in {"G1", "G2"}], "G1", "G2")
    report = {
        "checks": checks,
        "checks_passed": all(value == 0 for value in checks.values()),
        "harmful_w95": _cell_table(harmful, protocol, "W95"),
        "harmful_f95": _cell_table(harmful, protocol, "F95"),
        "harmful_completion": _completion(harmful),
        "harmless_w95": _cell_table(harmless, protocol, "W95"),
        "harmless_f95": _cell_table(harmless, protocol, "F95"),
        "harmless_completion": _completion(harmless),
        "slot_reacquired_by_g2_harmful": _mode([row for row in rows if row["trigger_class"] == "harmful"], "G2"),
        "slot_reacquired_by_g2_harmless": _mode([row for row in rows if row["trigger_class"] == "harmless"], "G2"),
        "false_restart_g1": sum(row["false_restart_count"] for row in rows if row["policy"] == "G1"),
    }
    delayed = [row for row in boundary if row["policy"] == "G2-delayed-release"]
    g0_same = [row for row in boundary if row["policy"] == "G0" and row["load"] == "medium" and row["timing"] == "early"]
    report["boundary_delayed_n"] = len(delayed)
    report["boundary_g0_w95_mean"] = sum(row["W95"] for row in g0_same) / len(g0_same) if g0_same else None
    report["boundary_delayed_w95_mean"] = sum(row["W95"] for row in delayed) / len(delayed) if delayed else None
    low = _pair([row for row in boundary if row["load"] == "low"], "G0", "G2")
    mid = _pair([row for row in boundary if row["timing"] == "mid" and row["load"] == "medium"], "G0", "G2")
    report["boundary_low_w95"] = _cell_table(low, protocol, "W95")
    report["boundary_mid_w95"] = _cell_table(mid, protocol, "W95")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "v21_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    columns = ["load", "scheduler", "rho", "n", "baseline_mean", "delta_mean", "ci_low", "ci_high", "relative_to_baseline", "meets_10pct"]
    text = [
        "# 确认性汇总",
        "",
        "## 操作检验",
        "",
        "```json",
        json.dumps(checks, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 有害 ΔW95 = W(G0)−W(G2)",
        "",
        _markdown_table(report["harmful_w95"], columns),
        "",
        "## 有害 ΔF95 = F(G0)−F(G2)",
        "",
        _markdown_table(report["harmful_f95"], columns),
        "",
        "## 无害 ΔW95 = W(G1)−W(G2)",
        "",
        _markdown_table(report["harmless_w95"], columns),
        "",
        "## 无害 ΔF95 = F(G1)−F(G2)",
        "",
        _markdown_table(report["harmless_f95"], columns),
        "",
    ]
    (OUT / "v21_tables.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps({"checks_passed": report["checks_passed"], "checks": checks, "harmful_w95": report["harmful_w95"], "harmless_w95": report["harmless_w95"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
