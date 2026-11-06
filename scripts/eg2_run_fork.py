"""M2 只有在 M1-R 工程闭环和 M0-R 启动门都通过时才开始。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "results" / "semantic-state" / "recovery"


def main() -> None:
    m1_path = RECOVERY / "m1" / "m1r_gate_decision.json"
    m0_path = RECOVERY / "m0r_gate_decision.json"
    m1 = json.loads(m1_path.read_text(encoding="utf-8")) if m1_path.exists() else {}
    m0 = json.loads(m0_path.read_text(encoding="utf-8")) if m0_path.exists() else {}
    both = m1.get("engineering_loop") is True and m0.get("data_gate") == "pass"
    decision = {
        "m2_started": False,
        "m3_started": False,
        "m4_started": False,
        "m5_started": False,
        "m6_started": False,
        "arxiv": False,
        "both_gates": both,
        "reason": (
            "两个启动门都已通过，但这一轮还没有执行分叉实验。"
            if both
            else "M1-R 工程闭环或 M0-R 启动门没有同时通过。M2 到 M6 不开始，也不填写论文数字。"
        ),
    }
    RECOVERY.mkdir(parents=True, exist_ok=True)
    (RECOVERY / "later_gate_decision.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"both_gates": both, "m2_started": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
