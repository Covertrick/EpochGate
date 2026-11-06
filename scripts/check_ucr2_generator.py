"""写出 Gate A0 审计。不拟合模型。"""

from __future__ import annotations

import json
from pathlib import Path

from epochgate.ucr2_audit import gate_a0
from epochgate.ucr2_protocol import (
    ALPHA,
    BENEFIT_S,
    CELL_SIZE,
    C_FEATURES,
    DEV_FAMILIES,
    FORBIDDEN_FEATURE_KEYS,
    HOLDOUT_FAMILIES,
    PRIMARY_ESTIMATOR,
    S_FEATURES,
    STRESS_FAMILIES,
    VOI_FORBIDDEN,
)

OUT = Path("results/experiments/ucr2")


def main() -> None:
    report = gate_a0()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "generator_audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    spec = {
        "version": "ucr-2.1",
        "alpha": ALPHA,
        "cell_size": CELL_SIZE,
        "benefit_s": BENEFIT_S,
        "information_probability": "min(1, 0.70 + 0.25 * tau)",
        "s_features": S_FEATURES,
        "c_features": C_FEATURES,
        "forbidden_features": sorted(FORBIDDEN_FEATURE_KEYS),
        "voi_buckets": ["overlap_width", "queue_length", "slot_occupancy", "predicate_valid", "probe_budget"],
        "voi_forbidden": sorted(VOI_FORBIDDEN),
        "primary_candidate": PRIMARY_ESTIMATOR,
        "families": {"dev": DEV_FAMILIES, "holdout": HOLDOUT_FAMILIES, "stress": STRESS_FAMILIES},
        "label": {
            "S": "t_fixed + max(1, N-g)/r + t_replan*tau + eps",
            "C": "(0.52 + 0.16*scale/1.2)*full + t_replan*tau + eps",
            "N": "a_tool + b_tool*fields + 10*tau",
        },
        "gate_a0_pass": report["pass"],
    }
    (OUT / "generator_spec_v2_1.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    if not report["pass"]:
        raise SystemExit("Gate A0 failed")
    print("gate_a0", True)


if __name__ == "__main__":
    main()
