from epochgate.eg2_m0r import engineering_gate, research_suitability


def test_old_astropy_pass_stays_limited_until_four_cells_exist():
    report = {
        "case_id": "astropy__astropy-12907",
        "shared_files": [],
        "admission": {"admitted": True},
        "linux_post_state": {"pytest_started": True, "exit_code": 0},
    }
    decision = research_suitability(report)
    assert decision["historical_admission_unchanged"] is True
    assert decision["old_admitted"] is True
    assert decision["research_suitability"] == "limited"
    assert decision["discriminates_wrong_from_right"] is False
    assert decision["information"] == "insufficient"


def test_engineering_gate_needs_five_pairs_from_two_repositories():
    records = [
        {"repo": "astropy/astropy", "m0r_status": "complete"},
        {"repo": "astropy/astropy", "m0r_status": "complete"},
        {"repo": "django/django", "m0r_status": "AccessBlocked"},
    ]
    assert engineering_gate(records)["data_gate"] == "stop"
    ready = [{"repo": "astropy/astropy" if index < 3 else "django/django", "m0r_status": "complete"} for index in range(5)]
    assert engineering_gate(ready)["data_gate"] == "pass"
    assert engineering_gate(ready)["paper_sample_frozen"] is False
