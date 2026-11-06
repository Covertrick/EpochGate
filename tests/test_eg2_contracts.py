from epochgate.eg2_admission import evaluate_case, summarize
from epochgate.eg2_ledger import deployment_allowed, oracle_record, score_candidate
from epochgate.eg2_audit import audit_checkpoint, audit_gold_leakage, audit_reproducibility, fork_checkpoint


def _case(**overrides):
    base = {
        "case_id": "case-1",
        "issue_time": "2024-01-01T00:00:00Z",
        "base_time": "2024-01-02T00:00:00Z",
        "change_time": "2024-02-01T00:00:00Z",
        "base_is_ancestor": True,
        "change_url": "https://github.com/example/repo/commit/" + "a" * 40,
        "change_sha": "a" * 40,
        "apply_status": "applied",
        "contains_target_fix": False,
        "leaks_target_fix": False,
        "recovery_witness_present": True,
        "post_state_test_present": True,
    }
    base.update(overrides)
    return base


def test_six_checks_admit_only_a_complete_pair():
    assert evaluate_case(_case())["admitted"] is True
    solved = evaluate_case(_case(contains_target_fix=True))
    assert solved["admitted"] is False
    assert "task_not_pre_solved" in solved["reasons"]
    unapplied = evaluate_case(_case(apply_status="not_attempted", contains_target_fix=None, leaks_target_fix=None))
    assert unapplied["checks"]["applicable"] is False
    assert unapplied["reasons"]["task_not_pre_solved"] == "not established"


def test_five_admitted_pairs_open_the_data_gate():
    cases = [_case(case_id=f"case-{index}", change_sha=f"{index:040x}") for index in range(5)]
    cases.append(_case(case_id="rejected", apply_status="conflict"))
    summary = summarize(cases)
    assert summary["admitted"] == 5
    assert summary["data_gate"] == "pass"
    assert summarize(cases[:4])["data_gate"] == "stop"


def test_fork_prefix_and_secret_leak_are_rejected():
    left = {
        "checkpoint_id": "cp",
        "parent_episode_id": "ep",
        "branch_id": "clean",
        "session_id": "ses",
        "event_prefix_hash": "a" * 64,
        "boundary_seq": 1,
        "base_commit": "b" * 40,
        "workspace_hash": "c" * 64,
        "image_digest": "sha256:d",
        "config_hash": "e" * 64,
        "evidence_read_set": [
            {"path": "a.py", "content_hash": "f" * 64, "read_at": "t", "repo_version": "b" * 40}
        ],
    }
    right = dict(left, branch_id="changed", event_prefix_hash="9" * 64)
    assert any("prefix" in item for item in audit_checkpoint([left, right]))
    leaked = audit_gold_leakage(
        {
            "prompt": "see reference_recovery_witness",
            "mounts": ["D:/repo/.env"],
            "env": {"DEEPSEEK_API_KEY": "present"},
            "git_objects": ["c" * 40],
            "future_commits": ["c" * 40],
        }
    )
    assert leaked


def test_clean_fork_keeps_one_prefix():
    snapshot = {
        "checkpoint_id": "cp",
        "parent_episode_id": "ep",
        "branch_id": "prefix",
        "session_id": "ses",
        "event_prefix_hash": "a" * 64,
        "boundary_seq": 2,
        "base_commit": "b" * 40,
        "workspace_hash": "c" * 64,
        "image_digest": "sha256:d",
        "config_hash": "e" * 64,
        "evidence_read_set": [
            {"path": "a.py", "content_hash": "f" * 64, "read_at": "t", "repo_version": "b" * 40}
        ],
        "open_side_effects": False,
    }
    rows = fork_checkpoint(snapshot, ["clean", "changed"])
    assert audit_checkpoint(rows) == []
    assert rows[0]["event_prefix_hash"] == rows[1]["event_prefix_hash"]


def test_gateway_rejection_is_not_task_failure_and_oracle_stays_outside():
    scored = score_candidate(
        {
            "gateway_decision": "reject",
            "candidate_matches_current_requirements": True,
            "post_state_tests_passed": True,
        }
    )
    assert scored["gateway_rejection"] is True
    assert scored["final_task_failure"] is False
    assert deployment_allowed("llm_dependency") is True
    assert deployment_allowed("oracle_dependency") is False
    assert oracle_record(True)["comparable_as_fair_baseline"] is False


def test_replay_is_not_a_fresh_sample():
    manifest = {
        "dsh_version": "0.2.2-rc.1",
        "image_digest": "sha256:" + "a" * 64,
        "base_commit": "b" * 40,
        "change_commit": "c" * 40,
        "model_alias": "deepseek-flash",
        "reported_model": "returned-id",
        "run_date": "2026-11-06",
        "raw_response_sha256": "d" * 64,
        "score_sha256": "e" * 64,
        "sample_kind": "trace_replay",
        "counts_as_new_model_sample": True,
    }
    assert any("new model sample" in item for item in audit_reproducibility(manifest))
