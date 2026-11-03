"""审计冻结确认性里的 P95 定义，以及事件后服务量如何传到旁观者等待。

不重写 ``results/experiments/v2_1/confirmatory/traces.jsonl``。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path("results/experiments/v2_1/confirmatory/traces.jsonl")
OUT = Path("results/experiments/v4_0/audit/v40_p95_and_service.json")
EARLY_REMAINING_S = 12.8


def _read(path: Path) -> list[dict]:
    """读冻结汇总。输入是 JSONL 路径，输出是行列表。不写文件。路径不存在时抛出 FileNotFoundError。"""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _pair(rows: list[dict], trigger: str, base: str, alt: str) -> list[tuple[tuple, dict, dict]]:
    """按种子配对照臂。输入是汇总行和政策名，输出是配对。不写文件。缺臂的种子被跳过。"""
    grouped: dict[tuple, dict] = defaultdict(dict)
    for row in rows:
        if row["trigger_class"] != trigger or row["timing"] != "early":
            continue
        if row["policy"] not in {base, alt}:
            continue
        key = (row["load"], row["scheduler"], float(row["rho"]), row["seed"])
        grouped[key][row["policy"]] = row
    pairs = []
    for key, arms in grouped.items():
        if base in arms and alt in arms:
            pairs.append((key, arms[base], arms[alt]))
    return pairs


def _mean(values: list[float]) -> float:
    """算术平均。输入是数值，输出是平均。空列表返回 0。无副作用。"""
    return sum(values) / len(values) if values else 0.0


def _cell(pairs: list[tuple[tuple, dict, dict]], base_name: str) -> list[dict]:
    """按负载、调度器和 rho 汇总配对差。输入是配对，输出是格子。无副作用。"""
    buckets: dict[tuple, list] = defaultdict(list)
    for key, base, alt in pairs:
        buckets[key[:3]].append((base, alt))
    cells = []
    for key, items in sorted(buckets.items()):
        deltas = [base["W95"] - alt["W95"] for base, alt in items]
        releases = [base["focal_release_at"] - alt["focal_release_at"] for base, alt in items]
        successor_gap = [abs(base["successor_service_s"] - alt["successor_service_s"]) for base, alt in items]
        hash_gap = sum(base["arrival_hash"] != alt["arrival_hash"] for base, alt in items)
        cells.append(
            {
                "load": key[0],
                "scheduler": key[1],
                "rho": key[2],
                "n_traces": len(items),
                "baseline": base_name,
                "delta_w95_mean": _mean(deltas),
                "release_gap_mean_s": _mean(releases),
                "max_successor_service_gap_s": max(successor_gap),
                "arrival_hash_mismatches": hash_gap,
                "alt_reclaimed_slot_s": _mean([alt["reclaimed_slot_s"] for _, alt in items]),
                "baseline_restart_s": _mean([base["restart_service_s"] for base, _ in items]),
                "alt_restart_s": _mean([alt["restart_service_s"] for _, alt in items]),
                "alt_next_slot": sorted({alt["slot_reacquired_by"] for _, alt in items}),
            }
        )
    return cells


def build_report(rows: list[dict]) -> dict:
    """汇总删失检查和服务量传导。输入是确认性行，输出是审计字典。不写文件。空表时计数字段为 0。"""
    waits = [float(row["W95"]) for row in rows]
    horizons = [float(row["horizon_s"]) for row in rows]
    censored = [int(row["censored_wait_count"]) for row in rows]
    completion = [float(row["bystander_completion_rate"]) for row in rows]
    harmful = _cell(_pair(rows, "harmful", "G0", "G2"), "G0")
    harmless = _cell(_pair(rows, "harmless", "G1", "G2"), "G1")
    for cell in harmful:
        cell["saved_invalid_tail_s"] = EARLY_REMAINING_S
        cell["transmission_per_saved_slot_second"] = cell["delta_w95_mean"] / EARLY_REMAINING_S
        cell["sign_matches_saved_work"] = cell["delta_w95_mean"] > 0
    for cell in harmless:
        advantage = cell["baseline_restart_s"] - EARLY_REMAINING_S
        cell["g2_post_event_work_advantage_s"] = advantage
        cell["transmission_per_work_advantage_second"] = (
            None if abs(advantage) < 1e-9 else cell["delta_w95_mean"] / advantage
        )
        if abs(advantage) < 1e-9:
            cell["sign_matches_work"] = abs(cell["delta_w95_mean"]) < 1e-9
        else:
            cell["sign_matches_work"] = (cell["delta_w95_mean"] > 0) == (advantage > 0)
    return {
        "source": str(ROOT).replace("\\", "/"),
        "n_summary_rows": len(rows),
        "wait_definition": {
            "unit": "每个 trace 内 36 个旁观者的首次拿到槽时刻减去到达",
            "percentile": "线性插值，秩 (n-1)*0.95。n=36 时是第 34 与第 35 个顺序统计量的 0.75/0.25 加权",
            "cell_estimate": "50 个同种子配对差的平均。差是基线 W95 减 G2 W95",
            "bootstrap": "对这 50 个配对差做有放回重采样，10000 次，种子 20261103。两臂不单独打散",
            "not_independent_units": "同一 trace 里的 36 个旁观者不是 36 次独立实验",
            "cap_in_code": "未启动的等待在代码里记为观察窗长度。这是封顶等待，不是右删失下的真实等待",
        },
        "confirmatory_censoring": {
            "censored_wait_count_min": min(censored),
            "censored_wait_count_max": max(censored),
            "rows_with_censoring": sum(item != 0 for item in censored),
            "completion_min": min(completion),
            "completion_max": max(completion),
            "rows_completion_below_one": sum(abs(item - 1.0) > 1e-9 for item in completion),
            "w95_max_s": max(waits),
            "horizon_min_s": min(horizons),
            "rows_w95_reaches_horizon": sum(wait >= horizon - 1e-6 for wait, horizon in zip(waits, horizons)),
            "interpretation": "封顶分支没有被确认性主表用到。这里的 W95 是观察窗内的精确首次拿到槽时间。",
        },
        "harmful_g0_minus_g2": harmful,
        "harmless_g1_minus_g2": harmless,
        "sign_matches_post_event_work": all(cell["sign_matches_saved_work"] for cell in harmful)
        and all(cell["sign_matches_work"] for cell in harmless),
    }


def main() -> None:
    """写出审计 JSON。输入无。输出打印路径。副作用是覆盖审计文件。确认性文件缺失时失败。"""
    report = build_report(_read(ROOT))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"wrote": str(OUT), "rows": report["n_summary_rows"], "sign_matches": report["sign_matches_post_event_work"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
