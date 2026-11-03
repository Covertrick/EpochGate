"""F1-M 确认性 JSONL 的独立重算。不访问网络，不改历史行。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_stage_f_evidence",
    ROOT / "scripts" / "audit_stage_f_evidence.py",
)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def _built():
    return audit.build_audit(
        audit.load_jsonl(audit.CONFIRMATORY),
        analyzer_source=(ROOT / "scripts" / "analyze_stage_f.py").read_text(encoding="utf-8"),
        runner_source=(ROOT / "src" / "epochgate" / "stage_e_runner.py").read_text(encoding="utf-8"),
        token_source=(ROOT / "src" / "epochgate" / "text_tokens.py").read_text(encoding="utf-8"),
    )


def test_primary_pool_matches_pair_mean_and_excludes_late_mid_mid() -> None:
    built = _built()
    pool = built["primary_both_triggered_pair_pool"]
    assert pool["n"] == 5
    assert pool["delta_text_mean"] == 465.2
    assert pool["delta_slot_hold_s_mean"] == 3.6981
    excluded = next(item for item in built["pairs"] if item["pair_id"].endswith("seed5451"))
    assert excluded["inclusion"] == "excluded"
    assert excluded["A_trigger_outcome"] == "trigger_late"
    assert "trigger_late" in excluded["reason"]
    assert built["holm_bonferroni"]["p_values_computed"] is False
    assert built["equal_weight_cell_mean_contrast"]["delta_text_mean"] != 465.2
