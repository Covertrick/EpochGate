"""回放合同。文档第 8.2 节的八条，不访问外网。"""

from epochgate.replay import replay
from epochgate.scheduler import plas_sort_key, srpt_sort_key
from epochgate.workload import build_workload


def _load(**overrides):
    params = dict(
        seed=7,
        trigger_class="harmful",
        timing="early",
        rho=0.5,
        load="medium",
        interarrival_s=1.0,
        slots=4,
    )
    params.update(overrides)
    return build_workload(**params)


def test_same_seed_same_arrival_hash():
    workload = _load()
    hashes = {replay(workload, policy=policy, scheduler="fcfs").summary["arrival_hash"] for policy in ("G0", "G1", "G2")}
    assert hashes == {workload.arrival_hash}


def test_harmless_g2_does_not_false_cancel_and_g1_restarts():
    workload = _load(trigger_class="harmless", slots=1)
    keep = replay(workload, policy="G2", scheduler="fcfs").summary
    drop = replay(workload, policy="G1", scheduler="fcfs").summary
    assert keep["false_cancel"] == 0
    assert keep["false_restart_count"] == 0
    assert drop["false_cancel"] == 1
    assert drop["false_restart_count"] == 1


def test_harmful_old_attempt_never_commits():
    workload = _load(slots=1)
    for policy in ("G0", "G1", "G2"):
        summary = replay(workload, policy=policy, scheduler="fcfs").summary
        assert summary["old_attempt_committed"] == 0
        assert summary["duplicate_side_effects"] == 0


def test_fcfs_replan_goes_to_tail():
    workload = _load(slots=1, interarrival_s=1.0)
    summary = replay(workload, policy="G2", scheduler="fcfs").summary
    assert summary["slot_reacquired_by"] == "bystander"


def test_plas_completed_service_ranks_behind_fresh_short():
    aged = plas_sort_key(16.0, 0.0, 1.0, "old")
    fresh = plas_sort_key(0.0, 0.0, 2.0, "new")
    assert fresh < aged


def test_srpt_shorter_remaining_first():
    short = srpt_sort_key(2.0, 0.0, 5.0, "short")
    long = srpt_sort_key(16.0, 0.0, 1.0, "long")
    assert short < long


def test_open_loop_arrival_does_not_move_when_g2_finishes_early():
    workload = _load()
    g0 = replay(workload, policy="G0", scheduler="fcfs")
    g2 = replay(workload, policy="G2", scheduler="fcfs")
    assert g2.summary["focal_release_at"] < g0.summary["focal_release_at"]
    g0_arrivals = {row["workflow_id"]: row["arrival_time"] for row in g0.rows if row["workflow_role"] == "bystander"}
    g2_arrivals = {row["workflow_id"]: row["arrival_time"] for row in g2.rows if row["workflow_role"] == "bystander"}
    assert g0_arrivals == g2_arrivals


def test_successor_service_matches_rho_times_remaining():
    workload = _load(rho=2.0)
    summary = replay(workload, policy="G2", scheduler="fcfs").summary
    assert summary["successor_service_s"] == workload.rho * workload.remaining_at_event_s
