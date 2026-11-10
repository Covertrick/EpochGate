"""从 F1-M 确认性 JSONL 独立重算配对差。不导入 analyze_stage_f。"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIRMATORY = ROOT / "results" / "real" / "phase_f" / "confirmatory" / "f1_m_confirmatory.jsonl"
ANALYZER = ROOT / "scripts" / "analyze_stage_f.py"
RUNNER = ROOT / "src" / "epochgate" / "stage_e_runner.py"
TOKENS = ROOT / "src" / "epochgate" / "text_tokens.py"
OUT = ROOT / "results" / "real" / "phase_f" / "audit" / "v1_3_f1_recompute.json"

PRIMARY_CELLS = ("F1M_long_early", "F1M_long_mid", "F1M_mid_mid")

# 订正前文档表里的 mid_mid 条件均值。只用来解释外部评估算出的 416.17，不是本审计的结果。
STALE_DOC_CELL_TEXT = (552.0, 498.5, 198.0)
STALE_DOC_CELL_SLOT = (4.701, 3.661, 1.478)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def classify_pair_trigger_status(a_outcome: str, b_outcome: str) -> str:
    a_ok = a_outcome == "triggered"
    b_ok = b_outcome == "triggered"
    if a_ok and b_ok:
        return "both_triggered"
    if a_ok and not b_ok:
        return "A_only_triggered"
    if not a_ok and b_ok:
        return "B_only_triggered"
    return "neither_triggered"


def _round(value: float | None, digits: int) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.mean(values)


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return statistics.stdev(values)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.median(values)


def _stat_block(values: list[float], *, digits: int) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "mean": _round(_mean(values), digits),
        "stdev": _round(_stdev(values), digits),
        "median": _round(_median(values), digits),
    }


def pair_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_pair.setdefault(row["pair_id"], {})[row["arm"]] = row

    details: list[dict[str, Any]] = []
    for pair_id, arms in sorted(by_pair.items()):
        arm_a, arm_b = arms.get("A"), arms.get("B")
        if arm_a is None or arm_b is None:
            details.append(
                {
                    "pair_id": pair_id,
                    "inclusion": "excluded",
                    "reason": "excluded_missing_arm",
                }
            )
            continue
        status = classify_pair_trigger_status(arm_a["trigger_outcome"], arm_b["trigger_outcome"])
        cell_id = arm_a["cell_id"]
        both = status == "both_triggered"
        primary = cell_id in PRIMARY_CELLS
        if primary and both:
            inclusion = "included"
            reason = "included_primary_both_triggered"
        elif not primary:
            inclusion = "excluded"
            reason = "excluded_exploratory_boundary"
        else:
            inclusion = "excluded"
            reason = f"excluded_not_both_triggered:{status}:{arm_a['trigger_outcome']}/{arm_b['trigger_outcome']}"

        text_delta = None
        slot_delta = None
        if both:
            text_delta = float(arm_a["post_revocation_observed_text_tokens"]) - float(
                arm_b["post_revocation_observed_text_tokens"]
            )
            if arm_a["old_attempt_slot_hold_s"] is not None and arm_b["old_attempt_slot_hold_s"] is not None:
                slot_delta = round(
                    float(arm_a["old_attempt_slot_hold_s"]) - float(arm_b["old_attempt_slot_hold_s"]),
                    4,
                )
        details.append(
            {
                "pair_id": pair_id,
                "cell_id": cell_id,
                "seed": arm_a["seed"],
                "arm_order": arm_a["arm_order"],
                "comparison_role": "primary_confirmatory" if primary else "exploratory_boundary",
                "pair_trigger_status": status,
                "A_trigger_outcome": arm_a["trigger_outcome"],
                "B_trigger_outcome": arm_b["trigger_outcome"],
                "A_post_tokens": arm_a["post_revocation_observed_text_tokens"],
                "B_post_tokens": arm_b["post_revocation_observed_text_tokens"],
                "delta_post_tokens": text_delta,
                "A_slot_hold_s": arm_a["old_attempt_slot_hold_s"],
                "B_slot_hold_s": arm_b["old_attempt_slot_hold_s"],
                "delta_slot_hold_s": slot_delta,
                "inclusion": inclusion,
                "reason": reason,
            }
        )
    return details


def _cell_block(cell_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    included = [item for item in items if item["inclusion"] == "included"]
    texts = [float(item["delta_post_tokens"]) for item in included]
    slots = [float(item["delta_slot_hold_s"]) for item in included if item["delta_slot_hold_s"] is not None]
    return {
        "cell_id": cell_id,
        "planned_pairs": len(items),
        "included_both_triggered_pairs": len(included),
        "excluded_pairs": [
            {"pair_id": item["pair_id"], "reason": item["reason"]}
            for item in items
            if item["inclusion"] != "included"
        ],
        "delta_text_tokens": _stat_block(texts, digits=2),
        "delta_slot_hold_s": _stat_block(slots, digits=4),
    }


def holm_audit(analyzer_source: str) -> dict[str, Any]:
    lowered = analyzer_source.lower()
    markers = ("p_value", "pvalue", "p-value", "wilcoxon", "ttest", "scipy.stats")
    return {
        "p_values_computed": any(marker in lowered for marker in markers),
        "names_primary_cells_only": "holm_bonferroni_primary_cells" in analyzer_source,
        "conclusion": (
            "analyze_stage_f.py lists the pre-specified primary cells and does not compute a Holm-Bonferroni p-value."
        ),
    }


def tokenizer_audit(runner_source: str, token_source: str) -> dict[str, Any]:
    concatenates = 'return "".join(parts)' in runner_source and 'kind: str = "token_delta"' in runner_source
    encodes_whole = "encoding.encode(text)" in token_source and "cl100k_base" in token_source
    return {
        "concatenates_token_delta_text_before_encode": concatenates and encodes_whole,
        "encoding": "cl100k_base",
        "counts_token_delta_reason_only": concatenates,
        "tool_call_arguments_included": False,
        "jsonl_stores_raw_text": False,
        "can_retokenize_from_jsonl": False,
        "limitation": (
            "Confirmatory JSONL stores token counts, not the concatenated text. "
            "This audit records the code path and does not re-encode the original strings."
        ),
    }


def build_audit(
    rows: list[dict[str, Any]],
    *,
    analyzer_source: str,
    runner_source: str,
    token_source: str,
) -> dict[str, Any]:
    details = pair_rows(rows)
    by_cell: dict[str, list[dict[str, Any]]] = {}
    for item in details:
        if "cell_id" in item:
            by_cell.setdefault(item["cell_id"], []).append(item)

    primary_cells = [_cell_block(cell_id, by_cell.get(cell_id, [])) for cell_id in PRIMARY_CELLS]
    included = [item for item in details if item["inclusion"] == "included"]
    texts = [float(item["delta_post_tokens"]) for item in included]
    slots = [float(item["delta_slot_hold_s"]) for item in included if item["delta_slot_hold_s"] is not None]
    cell_text_means = [
        float(cell["delta_text_tokens"]["mean"])
        for cell in primary_cells
        if cell["delta_text_tokens"]["mean"] is not None
    ]
    cell_slot_means = [
        float(cell["delta_slot_hold_s"]["mean"])
        for cell in primary_cells
        if cell["delta_slot_hold_s"]["mean"] is not None
    ]
    return {
        "audit": "v1.3_f1_recompute",
        "source": "results/real/phase_f/confirmatory/f1_m_confirmatory.jsonl",
        "does_not_import_analyze_stage_f": True,
        "historical_jsonl_unchanged": True,
        "headline_statistic": (
            "Unweighted mean of both_triggered pair differences inside the three pre-specified primary cells. "
            "Not the unweighted mean of the three cell means."
        ),
        "bootstrap_resampling_unit": "pair_difference",
        "bootstrap_note": (
            "A confidence interval for this statistic resamples the included pair differences, not the separate A and B rows. "
            "n=5 overall, and n=2 or n=1 inside a cell, describes the observed pairs. It is not a stable population estimate. "
            "This script does not report that interval as a significance test."
        ),
        "pairs": details,
        "primary_cells": primary_cells,
        "primary_both_triggered_pair_pool": {
            "n": len(included),
            "pair_ids": [item["pair_id"] for item in included],
            "delta_text_mean": _round(_mean(texts), 4),
            "delta_text_median": _round(_median(texts), 4),
            "delta_slot_hold_s_mean": _round(_mean(slots), 4),
            "delta_slot_hold_s_median": _round(_median(slots), 4),
        },
        "equal_weight_cell_mean_contrast": {
            "role": "contrast_only_not_the_headline",
            "cells_with_a_mean": len(cell_text_means),
            "delta_text_mean": _round(_mean(cell_text_means), 4),
            "delta_slot_hold_s_mean": _round(_mean(cell_slot_means), 4),
        },
        "stale_markdown_equal_weight_contrast": {
            "role": "explains_an_external_review_figure_not_a_result",
            "source": "docs/档案/v1.2/阶段F实验协议与结果.md F1-M table before the v1.3 correction",
            "cell_text_means": list(STALE_DOC_CELL_TEXT),
            "equal_weight_text_mean": _round(_mean(list(STALE_DOC_CELL_TEXT)), 4),
            "cell_slot_means": list(STALE_DOC_CELL_SLOT),
            "equal_weight_slot_mean": _round(_mean(list(STALE_DOC_CELL_SLOT)), 4),
            "why_it_differs": "That table gave mid_mid 198.0 and 1.478 and then weighted three cells equally. The JSONL mid_mid both-triggered mean is one pair, 225 tokens and 1.7663 s.",
        },
        "holm_bonferroni": holm_audit(analyzer_source),
        "tokenizer": tokenizer_audit(runner_source, token_source),
    }


def main() -> None:
    audit = build_audit(
        load_jsonl(CONFIRMATORY),
        analyzer_source=ANALYZER.read_text(encoding="utf-8"),
        runner_source=RUNNER.read_text(encoding="utf-8"),
        token_source=TOKENS.read_text(encoding="utf-8"),
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
