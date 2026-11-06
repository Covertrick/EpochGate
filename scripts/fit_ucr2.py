"""只在 F9/F10 和校准池上拟合，并在读取留出集之前锁死估计器。"""

from __future__ import annotations

import json
from pathlib import Path

import joblib

from epochgate.ucr2_audit import gate_a0
from epochgate.ucr2_estimate import ConformalModel, RawModel, group_key
from epochgate.ucr2_protocol import PRIMARY_ESTIMATOR
from epochgate.ucr2_replay import FailureTable, ValueTable
from epochgate.ucr2_workload import iter_calibration, iter_dev

OUT = Path("results/experiments/ucr2")
KINDS = ("bucket", "linear", "hybrid", "tree")


def _cell_coverage(model: ConformalModel, rows: list, target: str) -> dict[str, float]:
    groups: dict[tuple, list[bool]] = {}
    for row in rows:
        lower, upper = model.interval(row)
        value = row.true_remaining_s if target == "S" else row.true_restart_s
        groups.setdefault(group_key(row), []).append(lower <= value <= upper)
    return {str(key): sum(vals) / len(vals) for key, vals in groups.items() if len(vals) >= 50}


def _width(model: ConformalModel, rows: list) -> float:
    widths = [model.interval(row)[1] - model.interval(row)[0] for row in rows]
    return sum(widths) / len(widths)


def main() -> None:
    if not gate_a0()["pass"]:
        raise SystemExit("Gate A0 failed; refuse to fit")
    dev = list(iter_dev("F9")) + list(iter_dev("F10"))
    calibration = list(iter_calibration())
    comparison = {}
    fitted: dict[str, tuple[ConformalModel, ConformalModel]] = {}
    for kind in KINDS:
        raw_s = RawModel.fit(dev, "S", kind)
        raw_c = RawModel.fit(dev, "C", kind)
        for grouped, label in ((False, "pooled"), (True, "group")):
            name = f"{kind}-{label}"
            remaining = ConformalModel(raw_s, grouped)
            restart = ConformalModel(raw_c, grouped)
            remaining.calibrate(calibration, "S")
            restart.calibrate(calibration, "C")
            fitted[name] = (remaining, restart)
            s_cells = _cell_coverage(remaining, calibration, "S")
            c_cells = _cell_coverage(restart, calibration, "C")
            ok = all(value >= 0.9 for value in s_cells.values()) and all(value >= 0.9 for value in c_cells.values())
            comparison[name] = {
                "calibrated": ok,
                "mean_width_s": _width(remaining, calibration),
                "mean_width_c": _width(restart, calibration),
                "cell_s": s_cells,
                "cell_c": c_cells,
            }
            print(name, ok, round(comparison[name]["mean_width_s"], 3), round(comparison[name]["mean_width_c"], 3), flush=True)
    qualified = [name for name, row in comparison.items() if row["calibrated"]]
    if PRIMARY_ESTIMATOR in qualified:
        chosen = PRIMARY_ESTIMATOR
    elif qualified:
        chosen = min(qualified, key=lambda name: comparison[name]["mean_width_s"] + comparison[name]["mean_width_c"])
    else:
        chosen = PRIMARY_ESTIMATOR
    remaining, restart = fitted[chosen]
    services = [max(row.true_remaining_s, row.true_restart_s) for row in dev]
    lock = {
        "estimator": chosen,
        "primary_candidate": PRIMARY_ESTIMATOR,
        "qualified": qualified,
        "M": 10.0 * max(services),
        "opened_holdout": False,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "estimator_comparison.json").write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    (OUT / "estimator_lock.json").write_text(json.dumps(lock, indent=2), encoding="utf-8")
    joblib.dump(
        {
            "remaining": remaining,
            "restart": restart,
            "value_table": ValueTable.fit(calibration, remaining, restart),
            "failure_table": FailureTable.fit(calibration),
            "lock": lock,
        },
        OUT / "model.joblib",
    )
    print("locked", chosen)


if __name__ == "__main__":
    main()
