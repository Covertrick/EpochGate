"""从 v22 JSONL 重算整体效应和双臂都触发的条件效应。区间是配对重采样，不是显著性检验。"""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "experiments" / "v1_3"
BOOT_SEED = 20261102
BOOT_N = 10000


def load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _mean(values: list[float]) -> float | None:
    return _round(statistics.mean(values)) if values else None


def _quantiles(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)

    def pick(q: float) -> float:
        pos = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
        return round(ordered[pos], 4)

    return {"p10": pick(0.10), "p25": pick(0.25), "p50": pick(0.50), "p75": pick(0.75), "p90": pick(0.90)}


def _bootstrap(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "low": None, "high": None}
    rng = random.Random(BOOT_SEED)
    n = len(values)
    means = []
    for _ in range(BOOT_N):
        draw = [values[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.mean(draw))
    means.sort()
    return {
        "n": n,
        "low": round(means[int(0.025 * (BOOT_N - 1))], 4),
        "high": round(means[int(0.975 * (BOOT_N - 1))], 4),
    }


def _primary(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("task_id") in {None, "t0"} or int(row.get("queue_position") or 0) == 0]


def _index(rows: list[dict]) -> dict[str, dict[str, dict[str, dict]]]:
    by_cell: dict[str, dict[str, dict[str, dict]]] = defaultdict(lambda: defaultdict(dict))
    for row in _primary(rows):
        by_cell[row.get("cell_id") or ""][row["pair_id"]][row["arm"]] = row
    return by_cell


def _triggered(row: dict) -> bool:
    return row.get("trigger_outcome") == "triggered" and not row.get("transport_error") and not row.get("error")


def pair_report(rows: list[dict], left: str, right: str, field: str) -> dict:
    cells = {}
    all_assigned: list[float] = []
    all_conditional: list[float] = []
    for cell, pairs in sorted(_index(rows).items()):
        states = {"both": 0, "left_only": 0, "right_only": 0, "neither": 0}
        assigned: list[float] = []
        conditional: list[float] = []
        for arms in pairs.values():
            if left not in arms or right not in arms:
                continue
            a = arms[left]
            b = arms[right]
            ta, tb = _triggered(a), _triggered(b)
            if ta and tb:
                states["both"] += 1
            elif ta:
                states["left_only"] += 1
            elif tb:
                states["right_only"] += 1
            else:
                states["neither"] += 1
            av, bv = a.get(field), b.get(field)
            if av is None or bv is None:
                continue
            diff = float(av) - float(bv)
            assigned.append(diff)
            if ta and tb:
                conditional.append(diff)
        all_assigned.extend(assigned)
        all_conditional.extend(conditional)
        sd = statistics.stdev(conditional) if len(conditional) > 1 else None
        cells[cell] = {
            "n_pairs": states["both"] + states["left_only"] + states["right_only"] + states["neither"],
            "trigger_states": states,
            "assigned_mean": _mean(assigned),
            "assigned_quantiles": _quantiles(assigned),
            "assigned_interval": _bootstrap(assigned),
            "conditional_n": len(conditional),
            "conditional_mean": _mean(conditional),
            "conditional_sd": _round(sd),
            "conditional_quantiles": _quantiles(conditional),
            "conditional_interval": _bootstrap(conditional),
        }
    return {
        "contrast": f"{left}-{right}",
        "field": field,
        "cells": cells,
        "pooled_assigned_mean": _mean(all_assigned),
        "pooled_assigned_interval": _bootstrap(all_assigned),
        "pooled_conditional_mean": _mean(all_conditional),
        "pooled_conditional_interval": _bootstrap(all_conditional),
        "pooled_conditional_n": len(all_conditional),
    }


def safety(rows: list[dict]) -> dict:
    return {
        "rows": len(rows),
        "old_attempt_committed": sum(1 for row in rows if row.get("old_attempt_committed")),
        "duplicate_effect": sum(1 for row in rows if int(row.get("valid_commits") or 0) > 1 or row.get("duplicate_effect")),
        "false_cancel_c": sum(1 for row in rows if row.get("arm") == "C" and row.get("false_cancel")),
        "transport_errors": sum(1 for row in rows if row.get("transport_error") or row.get("error")),
        "provider_abort_not_issued": sum(1 for row in rows if row.get("provider_abort_not_issued")),
    }


def false_cancel(rows: list[dict]) -> dict:
    out = {}
    for arm in ("B", "C"):
        chosen = [row for row in _primary(rows) if row.get("arm") == arm]
        revoked = [row for row in chosen if row.get("false_cancel") or row.get("eligibility_revoked_at")]
        out[arm] = {"n": len(chosen), "revoked": len(revoked)}
    return out


def queue_followup(rows: list[dict]) -> dict:
    waits: dict[tuple, list[float]] = defaultdict(list)
    for row in rows:
        if int(row.get("queue_position") or 0) == 0:
            continue
        waits[(row.get("cell_id"), row.get("batch_id"), row.get("arm"))].append(
            float(row.get("first_queue_wait_s") or 0.0)
        )
    batch_mean: dict[tuple, dict[str, float]] = defaultdict(dict)
    cells: dict[str, dict] = {}
    cell_values: dict[tuple, list[float]] = defaultdict(list)
    for (cell, batch, arm), values in waits.items():
        batch_mean[(cell, batch)][arm] = statistics.mean(values)
        cell_values[(cell, arm)].append(statistics.mean(values))
    for (cell, arm), values in sorted(cell_values.items()):
        cells[f"{cell}:{arm}"] = {"batches": len(values), "mean_followup_wait_s": _mean(values)}
    pooled: dict[tuple, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for (cell, _batch), arms in batch_mean.items():
        parts = str(cell).split("_")
        if len(parts) < 3:
            continue
        load, order = parts[1], parts[2]
        for other in ("B", "C"):
            if "A" in arms and other in arms:
                pooled[(load, order)][other].append(arms["A"] - arms[other])
    contrasts = {}
    for key, diffs_by_arm in sorted(pooled.items()):
        item = {}
        for other, diffs in diffs_by_arm.items():
            item[f"A_minus_{other}"] = {
                "n_batches": len(diffs),
                "mean": _mean(diffs),
                "interval": _bootstrap(diffs),
                "meets_1s": _mean(diffs) is not None and _mean(diffs) >= 1.0,
            }
        contrasts[f"{key[0]}_{key[1]}"] = item
    return {"cells": cells, "contrasts": contrasts}


def recovery(rows: list[dict]) -> dict:
    by_cell: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in _primary(rows):
        by_cell[row.get("cell_id") or ""][row["arm"]].append(row)

    def ok(row: dict) -> bool:
        if row.get("transport_error") or row.get("error"):
            return False
        return bool(row.get("valid_commit")) and not row.get("old_attempt_committed")

    cells = {}
    for cell, arms in sorted(by_cell.items()):
        rates = {arm: _mean([1.0 if ok(row) else 0.0 for row in chosen]) for arm, chosen in arms.items()}
        by_seed: dict[int, dict[str, dict]] = defaultdict(dict)
        for arm, chosen in arms.items():
            for row in chosen:
                by_seed[int(row["seed"])][arm] = row
        diffs = []
        for seed_arms in by_seed.values():
            if "A" in seed_arms and "C" in seed_arms:
                diffs.append((1.0 if ok(seed_arms["C"]) else 0.0) - (1.0 if ok(seed_arms["A"]) else 0.0))
        gap = None if rates.get("A") is None or rates.get("C") is None else round(rates["A"] - rates["C"], 4)
        cells[cell] = {
            "n": {arm: len(chosen) for arm, chosen in arms.items()},
            "completion_rate": rates,
            "A_minus_C_rate": gap,
            "within_10pp": gap is not None and gap <= 0.10,
            "paired_C_minus_A": {"mean": _mean(diffs), "interval": _bootstrap(diffs), "n": len(diffs)},
        }
    return cells


def workflow(rows: list[dict]) -> dict:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in _primary(rows):
        grouped[(row.get("cell_id"), row.get("arm"))].append(row)
    out = {}
    for (cell, arm), chosen in sorted(grouped.items()):
        out[f"{cell}:{arm}"] = {
            "n": len(chosen),
            "read_tool_seen": sum(1 for row in chosen if row.get("read_tool_seen")),
            "tool_call_seen_before_revoke": sum(1 for row in chosen if row.get("tool_call_seen_before_revoke")),
            "valid_commits": sum(int(row.get("valid_commits") or 0) for row in chosen),
            "old_attempt_committed": sum(1 for row in chosen if row.get("old_attempt_committed")),
            "duplicate_effect": sum(1 for row in chosen if int(row.get("valid_commits") or 0) > 1),
            "revoked": sum(1 for row in chosen if row.get("eligibility_revoked_at")),
            "transport_errors": sum(1 for row in chosen if row.get("transport_error") or row.get("error")),
            "status": dict(sorted(_count(row.get("task_status") for row in chosen).items())),
        }
    return out


def _count(values) -> dict[str, int]:
    found: dict[str, int] = defaultdict(int)
    for value in values:
        found[str(value)] += 1
    return found


def pilot_sd_rule(rows: list[dict]) -> dict:
    report = pair_report(rows, "A", "C", "slot_release_latency_s")
    flagged = {}
    for cell, item in report["cells"].items():
        sd = item.get("conditional_sd")
        n = item.get("conditional_n") or 0
        if sd is None:
            continue
        se12 = sd / (12**0.5)
        flagged[cell] = {
            "pilot_conditional_sd": sd,
            "pilot_n": n,
            "exceeds_1_5s": sd > 1.5,
            "se_if_n12": round(se12, 4),
            "n12_meets_se_0_5": se12 < 0.5,
        }
    return flagged


def main() -> None:
    groups = {
        "pilot_harmful": load(OUT / "pilot" / "harmful.jsonl"),
        "pilot_harmless": load(OUT / "pilot" / "harmless.jsonl"),
        "confirm_harmful": load(OUT / "confirmatory" / "harmful.jsonl"),
        "confirm_harmless": load(OUT / "confirmatory" / "harmless.jsonl"),
        "queue": load(OUT / "queue" / "batches.jsonl"),
        "recovery": load(OUT / "recovery" / "real.jsonl"),
        "workflow": load(OUT / "workflow" / "runs.jsonl"),
    }
    summary = {
        "pilot_slot_sd_rule": pilot_sd_rule(groups["pilot_harmful"]),
        "groups": {},
    }
    for name, rows in groups.items():
        block = {
            "safety": safety(rows),
            "slot_release_A_minus_C": pair_report(rows, "A", "C", "slot_release_latency_s"),
            "slot_hold_A_minus_C": pair_report(rows, "A", "C", "old_attempt_slot_hold_s"),
            "post_text_A_minus_C": pair_report(rows, "A", "C", "post_revocation_observed_text_tokens"),
            "post_text_A_minus_B": pair_report(rows, "A", "B", "post_revocation_observed_text_tokens"),
        }
        if "harmless" in name:
            block["false_cancel"] = false_cancel(rows)
        if name == "queue":
            block["queue_followup"] = queue_followup(rows)
        if name == "recovery":
            block["recovery"] = recovery(rows)
        if name == "workflow":
            block["workflow"] = workflow(rows)
        summary["groups"][name] = block
    report = OUT / "reports"
    report.mkdir(parents=True, exist_ok=True)
    (report / "v22_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({name: item["safety"] for name, item in summary["groups"].items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
