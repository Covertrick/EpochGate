import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from e4_audit import state_vector  # noqa: E402


def _load_matrix():
    spec = importlib.util.spec_from_file_location("runtime_e4_matrix", ROOT / "scripts" / "runtime_e4_matrix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nested_usage_is_not_summed():
    matrix = _load_matrix()
    frames = [
        {
            "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            "stream": [{"usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 1400}}],
        },
        {"usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}},
        {"usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 99}},
    ]
    usage = matrix.token_usage(frames)
    assert usage == {"input": 13, "output": 6, "total": 19}


def test_receipt_loss_file_does_not_make_the_outcome_known():
    vector = state_vector({
        "task": "coding",
        "fault": "receipt_loss",
        "arm": "full",
        "repeat": 0,
        "terminal_correct": False,
        "stale_effect_accepted": False,
        "eligible_successor_completed": False,
        "unknown_fraction": 0.0,
        "eligible": True,
        "file_text": "def value():\n    return 0\n",
        "effects": [{"phase": "started", "effect_state": "DISPATCHED", "receipt": ""}],
        "reconcile": [{"status": "unknown", "published": False}],
        "event_types": ["publish_claimed"],
    })
    assert vector["task_success"] is False
    assert vector["outcome_known"] is False
    assert vector["correct_abstention"] is False
    assert vector["unknown_fraction"] == 0.0
    assert vector["historical_fault_note"] == "claimed_before_replace"


def test_revoked_approval_keeps_task_failure_and_records_abstention():
    vector = state_vector({
        "task": "approval",
        "fault": "permission_revoked",
        "arm": "full",
        "repeat": 0,
        "terminal_correct": False,
        "stale_effect_accepted": False,
        "eligible_successor_completed": False,
        "unknown_fraction": 0.0,
        "eligible": False,
        "file_text": None,
        "effects": [{"phase": "started", "effect_state": "INTENT_RECORDED", "receipt": ""}],
        "reconcile": [],
        "event_types": ["intent_recorded", "eligibility_revoked", "result_withheld"],
    })
    assert vector["task_success"] is False
    assert vector["stale_effect"] is False
    assert vector["safety"] is True
    assert vector["correct_abstention"] is True
    assert vector["outcome_known"] is True


def test_smoke_expectations_stay_fixed():
    from runtime_e4_smoke import expectation

    base = {"status": "completed", "scored": True, "file_text": None, "stale_effect_accepted": False, "eligible_successor_completed": False, "terminal_correct": False, "reconcile": []}
    revoked = {**base, "task": "approval", "fault": "permission_revoked", "file_text": "permit ALLOW"}
    assert expectation({**revoked, "arm": "direct"})[0] is True
    assert expectation({**revoked, "arm": "commit_only"})[0] is False
    assert expectation({**revoked, "arm": "full", "file_text": None})[0] is True
    old = {**base, "task": "inventory", "fault": "state_change", "arm": "commit_only", "stale_effect_accepted": True, "file_text": "order 3"}
    assert expectation(old)[0] is True
    full = {**base, "task": "inventory", "fault": "state_change", "arm": "full", "terminal_correct": False}
    assert expectation(full)[0] is False


def test_version_mismatch_is_relabeled_without_changing_the_fault():
    vector = state_vector({
        "task": "inventory",
        "fault": "version_mismatch",
        "arm": "full",
        "terminal_correct": True,
        "stale_effect_accepted": False,
        "eligible_successor_completed": True,
        "eligible": True,
        "file_text": "order 0\n",
        "effects": [{"phase": "completed", "effect_state": "RESULT_PERSISTED", "receipt": "file:order.txt:abc"}],
        "reconcile": [],
        "event_types": [],
    })
    assert vector["fault"] == "version_mismatch"
    assert vector["mechanism_label"] == "file_successor_token_refresh"
