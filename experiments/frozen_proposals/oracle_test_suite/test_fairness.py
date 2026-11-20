from frozen_proposals.fairness import check_fairness


def _row(path: str, **overrides):
    base = {
        "proposal_id": "p01",
        "path": path,
        "runner_status": "COMPLETED",
        "common_core": True,
        "payload_sha256": "abc",
        "task_semantic_version": "predicate.fulfillment.v1",
        "world_before_hash": "w",
        "fault_schedule_hash": "f",
        "authority_initial_hash": "a",
        "receiver_impl": "reliability_contract.SharedReceiver",
        "receiver_version": "m4-shared-receiver-1",
        "oracle_versions_canonical": "{}",
        "timeout_ms": 2000,
        "budget_units": 10,
        "code_commit": "abc",
        "environment_hash": "env",
        "implementation_hash": "impl",
    }
    base.update(overrides)
    return base


def test_equal_inputs_pass():
    rows = [_row(path) for path in ("Direct", "Admission", "Claim", "Outbox")]
    assert check_fairness(rows)["pass"] is True


def test_payload_mismatch_fails():
    rows = [_row(path) for path in ("Direct", "Admission", "Claim", "Outbox")]
    rows[1]["payload_sha256"] = "different"
    result = check_fairness(rows)
    assert result["pass"] is False
    assert result["mismatches"][0]["field"] == "payload_sha256"
