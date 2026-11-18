import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_e10_publish import _host_fault  # noqa: E402
from runtime_e12_diagnosis import diagnose_d2, oracle_label, _view_d2  # noqa: E402
from runtime_e9_factors import run  # noqa: E402


def test_pre_specified_e9_directions(tmp_path: Path):
    summary = run("unit", tmp_path)
    assert summary["h1_composition"]["pairs"] >= 1
    assert summary["h2_stale_difference_b1_minus_b2_strong"] >= 1
    assert summary["missing_notice_still_accepts_frozen"] is True
    delivered = {
        cell["mechanism"]: cell
        for cell in summary["cells"]
        if cell["study"] == "supersede" and cell["notice"] == "delivered"
    }
    assert delivered["B1"]["stale_credential_accepted"] is True
    assert delivered["B2_strong"]["stale_credential_accepted"] is False
    delayed = {
        cell["mechanism"]: cell
        for cell in summary["cells"]
        if cell["study"] == "revoke" and cell["notice"] == "delayed"
    }
    assert delayed["B1"]["stale_credential_accepted"] is True
    assert delayed["B2_strong"]["old_accepted"] is True
    assert delayed["B2_strong"]["stale_credential_accepted"] is False
    delivered_revoke = {
        cell["mechanism"]: cell
        for cell in summary["cells"]
        if cell["study"] == "revoke" and cell["notice"] == "delivered"
    }
    assert delivered_revoke["B2_strong"]["old_accepted"] is False


def test_h3_protected_bytes_survive_staging_mutation(tmp_path: Path):
    historical = _host_fault(tmp_path / "h0", "H0", "staging_replace", "unit-h0")
    protected = _host_fault(tmp_path / "h1", "H1", "staging_replace", "unit-h1")
    assert historical["physical_payload_matches_claim"] is False
    assert historical["unknown_outcome"] is True
    assert protected["physical_payload_matches_claim"] is True
    assert protected["status"] == "completed"


def test_unprovable_receiver_effect_is_abstention():
    record = {
        "unprovable_receiver": True,
        "receiver_effect": "happened",
        "notice": None,
        "file_kind": "absent",
        "authority_updated": False,
        "old_rejected": False,
        "claim_phase": None,
        "receipt_digest": None,
        "physical_digest": None,
    }
    assert oracle_label(record) == "effect_happened_unprovable"
    assert diagnose_d2(_view_d2(record)) == "abstain"
