"""阶段 B：事件与词元分开，停读不等于资源回收。"""

from epochgate.matrix_run import _arm_row
from epochgate.models import TraceEvent
from epochgate.text_tokens import TOKENIZER_ENCODING, TOKENIZER_NAME, count_observed_text_tokens


def test_tokenizer_is_frozen() -> None:
    assert TOKENIZER_NAME == "tiktoken"
    assert TOKENIZER_ENCODING == "cl100k_base"
    assert count_observed_text_tokens("hello world") > 0


def test_post_revocation_events_split_text_and_tool() -> None:
    events = [
        _event("runtime_start", 1.0),
        _event("backend_request", 1.1),
        _event("token_delta", 1.2, reason="one "),
        _event("eligibility_revoked", 2.0),
        _event("cancel_requested", 2.01),
        _event("token_delta", 2.1, reason="two "),
        _event("tool_call_delta", 2.2, reason='{"qty":'),
        _event("local_read_stopped", 2.3),
    ]
    row = _arm_row(
        events,
        seed=1,
        arm="A",
        words=50,
        delay_s=0.5,
        concurrency=1,
        stock_change_fired=True,
        scheduled_at=1.2,
        applied_at=1.9,
        error="",
        final_stock=0,
        wall_s=1.0,
        code_version="test",
        valid_commits=0,
    )
    assert row["post_revocation_delta_events"] == 1
    assert row["post_revocation_tool_call_delta_events"] == 1
    assert row["post_revocation_observed_text_tokens"] == count_observed_text_tokens("two ")
    assert row["provider_completion_tokens"] == "UNKNOWN"
    assert row["detection_latency_s"] == pytest_approx(0.1)
    assert row["cancel_latency_s"] == pytest_approx(0.29)
    assert row["eligibility_revoked_at"] == 2.0
    assert row["cancel_requested_at"] == 2.01


def pytest_approx(value: float, tol: float = 1e-9):
    class _Approx:
        def __eq__(self, other: object) -> bool:
            return isinstance(other, (int, float)) and abs(float(other) - value) <= tol

    return _Approx()


def _event(event_type: str, mono_time: float, reason: str | None = None) -> TraceEvent:
    return TraceEvent(
        run_id="t",
        seq=1,
        mono_time=mono_time,
        event_type=event_type,
        identity={"attempt_id": "att-0001", "task_id": "t0"},
        reason=reason,
    )
