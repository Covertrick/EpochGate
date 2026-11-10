from pathlib import Path

from epochgate_runtime.ledger import ControlError, RunLedger, SlotPool, transition
from epochgate_runtime.scheduler import ClassScheduler
from epochgate_runtime.f2p import assess_f2p, recorded_12907


def test_illegal_transition_is_rejected():
    try:
        transition("committed", "running")
    except ControlError:
        return
    raise AssertionError("committed run must stay committed")


def test_revoked_token_does_not_apply():
    ledger = RunLedger()
    ledger.create_run("r")
    ledger.advance("r", "running")
    before = ledger.get_run("r")
    ledger.revoke("r")
    calls = []
    result = ledger.execute(
        "r",
        idempotency_key="a",
        fencing_token=before.fencing_token,
        state_version=before.state_version,
        effect=lambda: calls.append(1) or "x",
    )
    assert result["status"] == "rejected"
    assert calls == []
    ledger.close()


def test_retry_replays_completed_effect():
    ledger = RunLedger()
    ledger.create_run("r")
    ledger.advance("r", "running")
    state = ledger.get_run("r")
    calls = []
    ledger.execute("r", idempotency_key="a", fencing_token=state.fencing_token, state_version=state.state_version, effect=lambda: calls.append(1) or "ok")
    again = ledger.execute("r", idempotency_key="a", fencing_token=state.fencing_token, state_version=state.state_version, effect=lambda: calls.append(1) or "ok")
    assert again["status"] == "replayed"
    assert calls == [1]
    ledger.close()


def test_crash_before_result_is_unknown():
    ledger = RunLedger()
    ledger.create_run("r")
    ledger.advance("r", "running")
    state = ledger.get_run("r")
    ledger.mark_started_only("r", "a", state.fencing_token)
    calls = []
    recovered = ledger.execute(
        "r",
        idempotency_key="a",
        fencing_token=state.fencing_token,
        state_version=state.state_version,
        effect=lambda: calls.append(1) or "again",
    )
    assert recovered["status"] == "unknown"
    assert calls == []
    ledger.close()


def test_unacked_cancel_does_not_release_slot_or_gpu():
    ledger = RunLedger()
    ledger.create_run("r")
    ledger.advance("r", "running")
    ledger.revoke("r")
    stopped = ledger.signal_executor("r", acked=False)
    assert stopped.cancel.cancellation_confirmed is False
    assert stopped.cancel.slot_released is False
    assert stopped.cancel.gpu_reclaimed is False
    seen = ledger.observe_slot("r", free=True)
    assert seen.cancel.slot_released is True
    assert seen.cancel.gpu_reclaimed is False
    ledger.close()


def test_reopened_ledger_does_not_repeat_completed_effect(tmp_path: Path):
    path = tmp_path / "ledger.sqlite"
    first = RunLedger(path)
    first.create_run("r")
    first.advance("r", "running")
    state = first.get_run("r")
    calls = []
    first.execute("r", idempotency_key="a", fencing_token=state.fencing_token, state_version=state.state_version, effect=lambda: calls.append(1) or "ok")
    first.close()
    second = RunLedger(path)
    again = second.execute("r", idempotency_key="a", fencing_token=state.fencing_token, state_version=state.state_version, effect=lambda: calls.append(1) or "ok")
    assert again["status"] == "replayed"
    assert calls == [1]
    second.close()


def test_queue_rejects_when_full():
    pool = SlotPool(capacity=1, queue_limit=1)
    assert pool.submit("a", service_s=1.0) == "running"
    assert pool.submit("b", service_s=1.0) == "queued"
    assert pool.submit("c", service_s=1.0) == "rejected"
    assert pool.rejected == 1


def test_12907_file_pytest_does_not_demonstrate_repair():
    report = recorded_12907()
    assert report["scoring_chain_present"] is True
    assert report["repair_demonstrated"] is False
    fresh = assess_f2p(
        tests_ran=True,
        base_fail_to_pass_failed=True,
        patched_fail_to_pass_passed=True,
        patch_changes_task_source=True,
    )
    assert fresh["repair_demonstrated"] is True


def _running(ledger: RunLedger, run_id: str) -> None:
    ledger.create_run(run_id)
    ledger.advance(run_id, "running")


def test_command_reaches_persisted_and_revoked_finish_stays_unknown():
    ledger = RunLedger()
    _running(ledger, "bash")
    state = ledger.get_run("bash")
    reserved = ledger.record_intent(
        "bash", "cmd-1", state.fencing_token, state.state_version, effect_id="cmd-1", arguments_hash="echo", tool_name="eg_bash"
    )
    assert reserved["allowed"] is True
    started = ledger.start_command("bash", "cmd-1", state.fencing_token)
    assert started == {"allowed": True, "effect_state": "STARTED"}
    finished = ledger.finish_command("bash", "cmd-1", state.fencing_token, 0)
    assert finished["effect_state"] == "RESULT_PERSISTED"
    assert ledger.effect_row("cmd-1")["effect_state"] == "RESULT_PERSISTED"

    late_state = ledger.get_run("bash")
    ledger.record_intent(
        "bash", "cmd-2", late_state.fencing_token, late_state.state_version, effect_id="cmd-2", arguments_hash="echo", tool_name="eg_bash"
    )
    ledger.revoke("bash")
    refused = ledger.start_command("bash", "cmd-2", late_state.fencing_token)
    assert refused["allowed"] is False
    assert refused["effect_state"] == "OUTCOME_UNKNOWN"
    ledger.close()


def test_missing_exit_and_external_http_are_not_retried():
    ledger = RunLedger()
    _running(ledger, "ext")
    state = ledger.get_run("ext")
    ledger.record_intent(
        "ext", "cmd-x", state.fencing_token, state.state_version, effect_id="cmd-x", arguments_hash="x", tool_name="eg_bash"
    )
    ledger.start_command("ext", "cmd-x", state.fencing_token)
    unknown = ledger.finish_command("ext", "cmd-x", state.fencing_token, None)
    assert unknown["effect_state"] == "OUTCOME_UNKNOWN"
    again = ledger.finish_command("ext", "cmd-x", state.fencing_token, 0)
    assert again["effect_state"] == "OUTCOME_UNKNOWN"
    assert again["persisted"] is False

    fresh = ledger.get_run("ext")
    first = ledger.seal_unconfirmed_external("ext", "http-1", fresh.fencing_token, fresh.state_version)
    second = ledger.seal_unconfirmed_external("ext", "http-1", fresh.fencing_token, fresh.state_version)
    assert first["recorded"] is True and first["retry"] is False
    assert second["recorded"] is False and second["retry"] is False
    ledger.close()


def test_run_model_and_tool_slots_are_independent():
    ledger = RunLedger()
    _running(ledger, "slots")
    scheduler = ClassScheduler(ledger, {"run": 2, "model": 1, "tool": 1})
    assert scheduler.acquire("slots", "tool")["status"] == "held"
    assert scheduler.acquire("slots", "tool")["status"] == "rejected"
    assert scheduler.acquire("slots", "model")["status"] == "held"
    assert scheduler.acquire("slots", "run")["status"] == "held"
    assert scheduler.acquire("slots", "run")["status"] == "held"
    assert scheduler.acquire("slots", "run")["status"] == "rejected"
    held = ledger.held_leases()
    assert {item["resource_class"] for item in held} == {"tool", "model", "run"}
    ledger.close()
