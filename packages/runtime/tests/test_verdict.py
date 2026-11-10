from epochgate_runtime.verdict import evaluate_coding_run


def _event(kind: str, **payload: object) -> dict:
    return {"event_type": kind, "run_id": "run", "payload": payload}


def test_read_only_events_do_not_complete_a_write():
    decision = evaluate_coding_run(
        [_event("intent_recorded", tool_name="eg_read")],
        run_id="run",
        expected_path="bug.py",
        baseline_failed=True,
        scorer_passed=False,
    )
    assert decision == {"chain_completed": False, "repair_demonstrated": False}


def test_verified_publish_is_not_the_same_as_a_repair():
    published = _event("file_published", path="bug.py", receipt_verified=True, sha256="abc")
    failed = evaluate_coding_run([published], run_id="run", expected_path="bug.py", baseline_failed=True, scorer_passed=False)
    assert failed == {"chain_completed": True, "repair_demonstrated": False}
    repaired = evaluate_coding_run([published], run_id="run", expected_path="bug.py", baseline_failed=True, scorer_passed=True)
    assert repaired["repair_demonstrated"] is True


def test_unverified_publish_does_not_count():
    event = _event("file_published", path="bug.py", receipt_verified=False)
    decision = evaluate_coding_run([event], run_id="run", expected_path="bug.py", baseline_failed=True, scorer_passed=True)
    assert decision["chain_completed"] is False
