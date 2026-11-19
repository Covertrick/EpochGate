"""把已经落盘的 LangGraph 观测收成一份边界说明。不调用模型，不改历史 JSON。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "correctness" / "langgraph-boundary" / "summary.json"


def main() -> None:
    m5 = json.loads((ROOT / "results/runtime/correctness/m5/summary.json").read_text(encoding="utf-8"))
    coding = json.loads((ROOT / "results/runtime/correctness/v2-m5-coding/summary.json").read_text(encoding="utf-8"))
    faults = sorted({row["fault"] for row in m5["rows"] if row.get("fault")})
    owners = {}
    for row in m5["rows"]:
        owners.setdefault(row.get("capability_owner") or "absent", set()).add(row.get("arm"))
    record = {
        "new_model_run": False,
        "claim_recovery_equivalent": False,
        "portability_claim": "lowered",
        "portability_note": "Coding remains a historical-patch smoke. Do not claim the method has migrated to a second agent runtime.",
        "faults_observed": faults,
        "arms_by_owner": {owner: sorted(arms) for owner, arms in owners.items()},
        "r1_idempotency_owner": "external_receiver",
        "r0_claim": "NOT_SUPPORTED",
        "fulfillment_rows": m5["n"],
        "fulfillment_blocked": m5["blocked"],
        "coding_status": coding["status"],
        "coding_reason": coding["reason"],
        "sources": [
            "results/runtime/correctness/m5/summary.json",
            "results/runtime/correctness/v2-m5-coding/summary.json",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"faults": faults, "coding": coding["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
