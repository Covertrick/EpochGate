"""从已保存的闸门文件生成停止说明。不发明成功率。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "results" / "semantic-state" / "recovery"


def main() -> None:
    m1 = json.loads((RECOVERY / "m1" / "m1r_gate_decision.json").read_text(encoding="utf-8"))
    m0 = json.loads((RECOVERY / "m0r_gate_decision.json").read_text(encoding="utf-8"))
    report = {
        "m1r_engineering_loop": m1.get("engineering_loop"),
        "m1r_repair_example": m1.get("repair_example"),
        "m0r_data_gate": m0.get("data_gate"),
        "complete_pairs": m0.get("complete_pairs"),
        "success_rates_filled": False,
    }
    (RECOVERY / "reports").mkdir(parents=True, exist_ok=True)
    (RECOVERY / "reports" / "gate_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
