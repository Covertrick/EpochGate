"""按已经写好的统计协议，从各批原始 JSON 分开描述。不把批次加在一起，不读取确认集来改故障。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "correctness" / "v4-coding" / "descriptive.json"


def load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    gate = load(ROOT / "results/runtime/correctness/v4-coding/dev/gate.json")
    confirm = load(ROOT / "results/runtime/correctness/v4-coding/confirm/summary.json")
    blocked = load(ROOT / "results/runtime/correctness/v4-coding/confirm/environment.json")
    v4b = load(ROOT / "results/runtime/correctness/v4b-coding/summary.json")
    feasibility = load(ROOT / "results/runtime/correctness/v4-coding/feasibility/summary.json")
    populations = None
    if gate is not None:
        rows = gate["rows"]
        assigned = len(rows)
        produced = [row for row in rows if row["initial_label"] in {"HIDDEN_PASSED", "HIDDEN_FAILED"}]
        populations = {
            "all_assigned": assigned,
            "effect_produced": len(produced),
            "initial_labels": [row["initial_label"] for row in rows],
            "agent_successor_labels": [row["agent_successor_label"] for row in rows],
            "agent_successor_not_demonstrated": gate["agent_successor_not_demonstrated"],
            "confirm_started": gate["confirm_started"],
            "unnecessary_rejection_v1": sum(1 for row in rows if row.get("unnecessary_rejection_v1")),
            "semantic_invalid": sum(1 for row in rows if row.get("semantic_invalid")),
        }
    confirm_pop = None
    if confirm is not None:
        rows = confirm["rows"]
        confirm_pop = {
            "all_assigned": confirm.get("denominator", len(rows)),
            "effect_produced": sum(1 for row in rows if row["initial_label"] in {"HIDDEN_PASSED", "HIDDEN_FAILED"}),
            "initial_hidden_passed": sum(1 for row in rows if row.get("initial_hidden_passed") is True),
            "agent_successor_passed": sum(1 for row in rows if row.get("agent_successor_passed") is True),
            "initial_labels": [row["initial_label"] for row in rows],
            "agent_successor_labels": [row["agent_successor_label"] for row in rows],
            "mechanism_old_rejected_b2_b3": all(
                row["mechanism_v1"]["B2"]["old_attempt_rejected"] and row["mechanism_v1"]["B3"]["old_attempt_rejected"]
                for row in rows
            ),
            "note": "Separate from the development population. Not added to v3 or to the development rows.",
        }
    v4b_pairs = None
    if v4b is not None:
        usable = [row for row in v4b["rows"] if row.get("content_invalid") and row.get("arms")]
        discordant = 0
        for row in usable:
            b1 = row["arms"].get("B1", {}).get("stale_credential_accepted")
            b2 = row["arms"].get("B2", {}).get("stale_credential_accepted")
            if b1 and not b2:
                discordant += 1
        v4b_pairs = {
            "rows": len(v4b["rows"]),
            "invalid": sum(1 for row in v4b["rows"] if row.get("invalid")),
            "patch_rejected": sum(1 for row in v4b["rows"] if row.get("patch_rejected")),
            "content_invalid_applicable": len(usable),
            "b1_accept_b2_reject": discordant,
            "note": "Two discordant pairs are not six. The protocol's p=0.03125 illustration does not apply. These pairs are not added to historical H5.",
        }
    record = {
        "protocol": "docs/研究/统计协议.md",
        "batches_added_together": False,
        "hypotheses_on_this_batch": {
            "H-A": "v4b applicable invalid-content cells only. See v4b.b1_accept_b2_reject. Not pooled with historical pairs.",
            "H-B": "v4 used S1, notice delivered. It does not re-estimate interrupt recovery. Recovery evidence stays in the final regression tests.",
            "H-C": "Development and confirmation successor passes are reported as separate populations."
        },
        "confirm_read_before_protocol": False,
        "feasibility_ready": None if feasibility is None else all(row["ready_for_model"] for row in feasibility["rows"]),
        "populations_dev": populations,
        "populations_confirm": confirm_pop,
        "v4b": v4b_pairs,
        "confirm_summary_present": confirm is not None,
        "confirm_environment": None if blocked is None else blocked.get("status"),
        "interpretation": "Six confirmation tasks, even if all succeed, are not a cross-project conclusion. A missing confirm summary means the dev gate did not start it or the run has not finished.",
    }
    OUT.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"populations": populations is not None, "confirm": confirm is not None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
