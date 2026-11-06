"""重读冻结的闸门文件，确认分析没有改写原始记录。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "results" / "semantic-state" / "recovery"


def main() -> None:
    m0 = json.loads((RECOVERY / "m0r_gate_decision.json").read_text(encoding="utf-8"))
    summary = json.loads((RECOVERY / "reports" / "gate_summary.json").read_text(encoding="utf-8"))
    same = summary["m0r_data_gate"] == m0["data_gate"] and summary["success_rates_filled"] is False
    print(json.dumps({"analysis_matches_gate": same}, ensure_ascii=False))
    if not same:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
