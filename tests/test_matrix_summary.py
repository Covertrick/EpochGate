"""矩阵汇总按对计算，不把未触发填成 0。"""

from epochgate.matrix_run import _arm_row, _pair_status
from epochgate.matrix_summary import pair_metrics, summarize_cell
from epochgate.models import TraceEvent


def test_untriggered_pair_does_not_become_zero_saving() -> None:
    metrics = pair_metrics(
        {"seed": 1, "stock_change_fired": False, "pair_status": "trigger_not_fired", "post_revoke_local_deltas": 0},
        {"seed": 1, "stock_change_fired": False, "pair_status": "trigger_not_fired", "post_revoke_local_deltas": 0},
    )
    assert metrics["usable"] is False
    assert metrics["system_level_local_delta_saving_ratio"] is None


def test_trigger_not_fired_is_not_a_usable_pair() -> None:
    metrics = pair_metrics(
        {
            "seed": 1,
            "stock_change_fired": True,
            "pair_status": "trigger_not_fired",
            "post_revoke_local_deltas": 0,
            "task_total_local_deltas": 10,
            "revoked_attempt_local_deltas": 10,
        },
        {
            "seed": 1,
            "stock_change_fired": True,
            "pair_status": "completed",
            "post_revoke_local_deltas": 0,
            "task_total_local_deltas": 10,
            "revoked_attempt_local_deltas": 10,
        },
    )
    assert metrics["triggered"] is False
    assert metrics["usable"] is False
    assert metrics["revoked_attempt_local_delta_saving_ratio"] is None


def test_negative_saving_is_kept() -> None:
    metrics = pair_metrics(
        {
            "seed": 1,
            "stock_change_fired": True,
            "pair_status": "completed",
            "post_revoke_local_deltas": 1,
            "task_total_local_deltas": 10,
            "revoked_attempt_local_deltas": 4,
        },
        {
            "seed": 1,
            "stock_change_fired": True,
            "pair_status": "completed",
            "post_revoke_local_deltas": 5,
            "task_total_local_deltas": 10,
            "revoked_attempt_local_deltas": 6,
        },
    )
    assert metrics["post_revoke_delta"] == -4
    assert metrics["system_level_local_delta_saving_ratio"] < 0


def test_cell_median_is_not_a_pooled_ratio() -> None:
    rows = []
    for seed, post_a in ((1, 10), (2, 100)):
        rows.append(
            {
                "seed": seed,
                "arm": "A",
                "stock_change_fired": True,
                "pair_status": "completed",
                "post_revoke_local_deltas": post_a,
                "task_total_local_deltas": post_a,
                "revoked_attempt_local_deltas": post_a,
                "replan_local_deltas": 0,
                "end_to_end_latency_s": 1.0,
                "valid_commit": False,
            }
        )
        rows.append(
            {
                "seed": seed,
                "arm": "B",
                "stock_change_fired": True,
                "pair_status": "completed",
                "post_revoke_local_deltas": 0,
                "task_total_local_deltas": 1,
                "revoked_attempt_local_deltas": 1,
                "replan_local_deltas": 0,
                "end_to_end_latency_s": 1.0,
                "valid_commit": False,
            }
        )
    summary = summarize_cell(rows, planned_pairs=2)
    assert summary["triggered_pairs"] == 2
    assert summary["post_revoke_delta_median"] == 55
    assert summary["system_saving_ratio_median"] != (110 / 112)


def test_replan_without_a_tool_is_not_a_model_error() -> None:
    events = [
        _event("cancel_requested", 2.0),
        _event("backend_failed", 3.0, reason="backend returned no action"),
    ]
    assert _pair_status(events, error="", fired=True, revoked=events[0], tool_at=None) == "completed"


def test_http_failure_stays_a_model_error() -> None:
    events = [_event("backend_failed", 1.0, reason="http_error:timeout")]
    assert _pair_status(events, error="", fired=True, revoked=events[0], tool_at=None) == "model_error"


def test_position_ratio_is_null_when_the_tool_never_arrives() -> None:
    events = [
        _event("runtime_start", 1.0),
        _event("token_delta", 2.0, reason="one two"),
        _event("cancel_requested", 3.0),
    ]
    row = _arm_row(
        events,
        seed=1,
        arm="A",
        words=50,
        delay_s=0.5,
        concurrency=1,
        stock_change_fired=True,
        scheduled_at=2.0,
        applied_at=2.1,
        error="",
        final_stock=0,
        wall_s=1.0,
        code_version="test",
        valid_commits=0,
    )
    assert row["tool_call_seen"] is False
    assert row["revocation_position_ratio"] is None
    assert row["actual_words_before_tool"] == 2


def _event(event_type: str, mono_time: float, reason: str | None = None) -> TraceEvent:
    return TraceEvent(
        run_id="t",
        seq=1,
        mono_time=mono_time,
        event_type=event_type,
        identity={"attempt_id": "att-0001"},
        reason=reason,
    )
