from pathlib import Path

import pytest

from epochgate_runtime.measurements import (
    apply_host_arm,
    commit_matrix_rows,
    measure_container_read_race,
    measure_host_read_race,
    measure_stale_world,
)
from epochgate_runtime.sandbox import docker_available


def test_commit_matrix_covers_duplicate_and_revoke_orders(tmp_path: Path):
    rows = {item["fault"]: item for item in commit_matrix_rows(tmp_path)}
    assert rows["duplicate_commit"]["second_status"] == "replayed"
    assert rows["duplicate_commit"]["sink_accepts"] == 1
    assert rows["revoke_then_commit"]["commit_status"] == "rejected"
    assert rows["revoke_then_commit"]["sink_accepts"] == 0
    assert rows["commit_then_revoke"]["commit_status"] == "completed"
    assert rows["commit_then_revoke"]["sink_accepts"] == 1
    assert rows["commit_then_revoke"]["retry_with_old_token_status"] == "rejected"
    assert rows["commit_then_revoke"]["retry_sink_accepts"] == 0
    assert rows["commit_then_revoke"]["receipt_still_present"] is True
    assert rows["commit_then_revoke"]["gpu_reclaimed"] is False
    assert rows["revoke_vs_commit_concurrent"]["n"] == 1
    assert rows["revoke_vs_commit_concurrent"]["status_matches_accepts"] is True
    assert rows["revoke_vs_commit_concurrent"]["gpu_reclaimed"] is False


def test_host_publish_file_does_not_verify_bytes_replaced_after_the_hash(tmp_path: Path):
    report = measure_host_read_race(tmp_path)
    assert report["kernel_toctou"] is False
    by_kind = {item["mutation"]: item for item in report["cases"]}
    for kind in ("replace", "truncate"):
        case = by_kind[kind]
        assert case["published"] is False
        assert case["status"] == "unknown"
        assert case["published_bytes_match_hashed_bytes"] is False
        assert case["effect_state"] == "OUTCOME_UNKNOWN"
        assert case["receipt"] in {None, ""}


def test_stale_inventory_expires_only_after_the_epoch_advances(tmp_path: Path):
    report = measure_stale_world(tmp_path)
    untouched = report["without_epoch_advance"]
    assert untouched["published"] is True
    assert untouched["bytes"] == "reserve:1\n"
    assert untouched["inventory_before_commit"] == "0"
    advanced = report["with_epoch_advance"]
    assert advanced["expired_token"] != advanced["valid_token"]
    assert advanced["expired_published"] is False
    assert advanced["expired_status"] == "withheld"
    assert advanced["valid_published"] is True
    assert advanced["bytes"] == "inventory:0\n"
    assert advanced["replay_status"] == "replayed"
    assert advanced["bytes_unchanged_after_replay"] is True
    inventory = report["inventory_commit"]
    assert inventory["stale_status"] == "rejected"
    assert inventory["stale_sink_accepts"] == 0
    assert inventory["successor_commit_status"] == "rejected"
    assert inventory["successor_sink_accepts"] == 0
    assert inventory["aligned_status"] == "completed"
    assert inventory["aligned_sink_accepts"] == 1
    assert inventory["duplicate_status"] == "replayed"
    assert inventory["gpu_reclaimed"] is False


def test_host_arms_keep_receipt_on_b2_only(tmp_path: Path):
    content = b"def value():\n    return 1\n"
    arms = {item["arm"]: item for item in (apply_host_arm(tmp_path, arm, content) for arm in ("B0", "B1", "B2"))}
    assert arms["B0"]["ledger"] is False
    assert arms["B0"]["receipt"] is None
    assert arms["B0"]["score_passed"] is True
    assert arms["B1"]["receipt"] is None
    assert arms["B1"]["effect_state"] == "INTENT_RECORDED"
    assert arms["B1"]["score_passed"] is True
    assert arms["B2"]["published"] is True
    assert arms["B2"]["receipt"]
    assert arms["B2"]["score_passed"] is True


@pytest.mark.skipif(not docker_available(), reason="Docker engine is not available")
def test_container_publisher_keeps_the_hashed_bytes_after_staging_changes():
    report = measure_container_read_race()
    assert report["docker"] is True
    assert report.get("error") in {None, ""}
    assert report["kernel_toctou"] is False
    by_kind = {item["mutation"]: item for item in report["cases"]}
    for kind in ("replace", "truncate"):
        case = by_kind[kind]
        assert case["published"] is True
        assert case["published_bytes_match_hashed_bytes"] is True
        assert case["published_sha256"] == case["hashed_sha256"]
        assert case["workspace_sha256"] == case["hashed_sha256"]
        assert case["staging_after_sha256"] != case["hashed_sha256"]
        assert case["receipt_verified"] is True
