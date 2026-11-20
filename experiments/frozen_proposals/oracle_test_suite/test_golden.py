from frozen_proposals.hashing import sha256_bytes
from frozen_proposals.oracles import Observation, adjudicate, authority_oracle, evidence_oracle, physical_effect_state, recovery_state, semantics_oracle, successor_oracle, terminal_oracle
from frozen_proposals.predicate import PREDICATE_VERSION, legal_payload


def world(**overrides):
    base = {
        "order_id": "ORD-1",
        "sku": "A",
        "qty": 1,
        "dest": "north",
        "generation": 0,
        "effect_forbidden": False,
        "successor_qty": 8,
        "drift_qty": 4,
    }
    base.update(overrides)
    return base


def _obs(**kwargs) -> Observation:
    payload = kwargs.pop("physical_bytes", None)
    scoring = kwargs.pop("scoring_world", world())
    base = dict(
        run_id="run",
        proposal_id="p",
        proposal_sha256=sha256_bytes(payload or b"proposal"),
        predicate_version=PREDICATE_VERSION,
        scoring_world=scoring,
        physical_bytes=payload,
        receiver_log=[],
        authority_log=[{"seq": 1, "token": 1, "epoch": 1, "eligible": True, "event": "init"}],
    )
    base.update(kwargs)
    return Observation(**base)


def _accept(digest: str, *, seq: int = 2, token: int = 1, epoch: int = 1, detail: str = "accepted") -> dict:
    return {
        "run_id": "run",
        "seq": seq,
        "accepted": detail == "accepted",
        "detail": detail,
        "digest": digest,
        "token": token,
        "epoch": epoch,
        "receipt": "r1",
    }


def test_stale_acceptance_is_independent_of_terminal_label():
    legal = legal_payload(world())
    obs = _obs(
        physical_bytes=legal,
        scoring_world=world(),
        proposal_sha256=sha256_bytes(legal),
        receiver_log=[_accept(sha256_bytes(legal), token=1)],
        authority_log=[
            {"seq": 1, "token": 1, "epoch": 1, "eligible": True, "event": "init"},
            {"seq": 2, "token": 2, "epoch": 2, "eligible": False, "event": "revoke"},
        ],
        path_self_report_terminal_match=True,
    )
    # Accept seq must be after the revoke seq to be ordered.
    obs.receiver_log[0]["seq"] = 3
    assert authority_oracle(obs) == "STALE_ACCEPTED"
    assert physical_effect_state(obs) == "CONFIRMED_EFFECT"
    assert semantics_oracle(obs) == "VALID"
    assert terminal_oracle(obs) == "MATCH"


def test_correct_stop_is_not_successor_failure():
    obs = _obs(
        physical_bytes=None,
        scoring_world=world(effect_forbidden=True),
        successor_obligation="NOT_REQUIRED",
        stop_condition="MUST_STOP",
        withheld=True,
        path_self_report_successor_failed=True,
    )
    successor = successor_oracle(obs)
    assert successor["status"] == "CORRECT_STOP"
    assert successor["week1_successor_status"] == "NOT_APPLICABLE"
    assert successor["week1_successor_status"] != "NOT_COMPLETED"
    assert successor["week1_stop_correctness"] == "CORRECT_STOP"
    decision, _basis = adjudicate(
        terminal=terminal_oracle(obs),
        authority=authority_oracle(obs),
        physical=physical_effect_state(obs),
        evidence=evidence_oracle(obs),
        duplicate_effect=False,
        runner_status="COMPLETED",
    )
    assert decision != "CONFIRMED_FAILURE"


def test_restored_bytes_can_match_or_miss_without_changing_recovery_label():
    legal = legal_payload(world())
    obs = _obs(
        physical_bytes=legal,
        proposal_sha256=sha256_bytes(legal),
        receiver_log=[_accept(sha256_bytes(legal))],
        recovery_attempted=True,
        recovery_bytes_sha256=sha256_bytes(legal),
    )
    assert recovery_state(obs) == "RESTORED_BYTES"
    assert physical_effect_state(obs) == "CONFIRMED_EFFECT"


def test_missing_notice_is_not_stale_authority():
    original = world()
    drifted = world(qty=4)
    old = legal_payload(original)
    obs = _obs(
        physical_bytes=old,
        scoring_world=drifted,
        proposal_sha256=sha256_bytes(old),
        receiver_log=[_accept(sha256_bytes(old))],
        outdated_scenario_label="NOTICE_MISSING_OR_DELAYED",
        path_self_report_semantic_valid=True,
    )
    assert authority_oracle(obs) == "NO_STALE_ACCEPTANCE_RECORDED"
    assert semantics_oracle(obs) == "INVALID"
    assert physical_effect_state(obs) == "NOT_LEGAL"
    assert "STALE_AUTHORITY" not in authority_oracle(obs)


def test_unknown_provenance_is_not_failure():
    obs = _obs(receiver_provenance_missing=True, receiver_log=None, authority_log=None, path_self_report_terminal_match=False)
    decision, basis = adjudicate(
        terminal="UNKNOWN",
        authority=authority_oracle(obs),
        physical="UNKNOWN",
        evidence=evidence_oracle(obs),
        duplicate_effect=False,
        runner_status="COMPLETED",
    )
    assert authority_oracle(obs) == "UNKNOWN"
    assert decision == "UNRESOLVED"
    assert basis != "CONFIRMED_FAILURE"


def test_conflict_is_not_placed_between_unknown_and_valid():
    legal = legal_payload(world())
    digest = sha256_bytes(legal)
    obs = _obs(
        physical_bytes=legal,
        proposal_sha256=digest,
        receiver_log=[
            _accept(digest),
            _accept(sha256_bytes(b"CONFLICT|p"), seq=3, detail="conflict"),
        ],
        path_self_report_semantic_valid=False,
    )
    assert evidence_oracle(obs) == "CONFLICT"
    assert semantics_oracle(obs) == "VALID"
    assert evidence_oracle(obs) not in {"UNKNOWN", "VALID"}


def test_required_successor_not_completed_is_not_correct_stop():
    obs = _obs(
        physical_bytes=None,
        scoring_world=world(qty=8, generation=1),
        successor_obligation="REQUIRED",
        withheld=True,
    )
    successor = successor_oracle(obs)
    assert successor["status"] == "FAILED"
    assert successor["status"] != "CORRECT_STOP"
    assert successor["week1_successor_status"] == "NOT_COMPLETED"


def test_receiver_fail_has_no_semantic_invalidity():
    obs = _obs(
        physical_bytes=None,
        receiver_log=[{"accepted": False, "detail": "receiver_fail", "digest": "aa", "seq": 2, "token": 1, "epoch": 1}],
    )
    assert physical_effect_state(obs) == "ABSENT"
    assert semantics_oracle(obs) == "UNKNOWN"
    assert evidence_oracle(obs) == "SUFFICIENT"
    assert authority_oracle(obs) == "NO_STALE_ACCEPTANCE_RECORDED"
