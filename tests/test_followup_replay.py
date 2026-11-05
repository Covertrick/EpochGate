from epochgate.followup_matrix import policy_replay_arm, replay_policy
from epochgate.replay import replay
from epochgate.workload import build_workload


def test_always_continue_holds_the_slot_on_a_harmful_event() -> None:
    arm, action = policy_replay_arm("AlwaysContinue", "harmful", 1.0, 8.0, 0.25)
    assert arm == "G0"
    assert action == "continue"
    held = replay_policy(
        seed=5000, trigger="harmful", timing="mid", load="high", interarrival_s=1.7824,
        slots=4, rho=1.0, error=0.25, policy="AlwaysContinue", scheduler="fcfs",
    )
    aborted = replay_policy(
        seed=5000, trigger="harmful", timing="mid", load="high", interarrival_s=1.7824,
        slots=4, rho=1.0, error=0.25, policy="AlwaysAbort", scheduler="fcfs",
    )
    assert held[2].focal_release_at > aborted[2].focal_release_at
    assert held[2].w95 != aborted[2].w95 or held[2].focal_release_at != aborted[2].focal_release_at


def test_defer_keeps_the_slot_and_does_not_replan() -> None:
    workload = build_workload(
        seed=5000, trigger_class="harmless", timing="mid", rho=1.0, load="high",
        interarrival_s=1.7824, slots=4,
    )
    continued = replay(workload, policy="G0", scheduler="fcfs")
    deferred = replay(workload, policy="defer", scheduler="fcfs")
    aborted = replay(workload, policy="G1", scheduler="fcfs")
    assert deferred.summary["focal_release_at"] == continued.summary["focal_release_at"]
    assert deferred.summary["restart_service_s"] == 0
    assert aborted.summary["restart_service_s"] > 0
    assert deferred.summary["target_recovery_success"] is False
    assert continued.summary["target_recovery_success"] is True
    assert deferred.summary["old_attempt_committed"] == 0


def test_uncertainty_policy_defers_inside_the_band_without_aborting() -> None:
    arm, action = policy_replay_arm("UncertaintyAwarePolicy", "harmless", 1.0, 8.0, 0.25)
    assert arm == "defer"
    assert action == "continue/defer"
    harmful_arm, harmful_action = policy_replay_arm("UncertaintyAwarePolicy", "harmful", 2.0, 8.0, 0.0)
    assert harmful_arm == "G1"
    assert harmful_action == "abort-and-replan"
