from frozen_proposals.hashing import sha256_bytes
from frozen_proposals.oracle_test_suite.test_golden import _accept, _obs, world
from frozen_proposals.oracles import authority_oracle, physical_effect_state, recovery_state, semantics_oracle, successor_oracle
from frozen_proposals.predicate import legal_payload


def test_flipping_restored_bytes_does_not_keep_confirmed_effect():
    legal = legal_payload(world())
    old = legal_payload(world(qty=9))
    obs = _obs(
        physical_bytes=legal,
        proposal_sha256=sha256_bytes(legal),
        receiver_log=[_accept(sha256_bytes(legal))],
        recovery_attempted=True,
        recovery_bytes_sha256=sha256_bytes(legal),
        successor_obligation="NOT_REQUIRED",
        stop_condition="NOT_A_STOP_SCENARIO",
    )
    before_authority = authority_oracle(obs)
    before_successor = successor_oracle(obs)
    obs.physical_bytes = old
    obs.recovery_bytes_sha256 = sha256_bytes(old)
    obs.receiver_log = [_accept(sha256_bytes(old))]
    assert recovery_state(obs) == "RESTORED_BYTES"
    assert physical_effect_state(obs) == "NOT_LEGAL"
    assert physical_effect_state(obs) != "CONFIRMED_EFFECT"
    assert authority_oracle(obs) == before_authority
    assert successor_oracle(obs) == before_successor


def test_flipping_outdated_label_does_not_create_stale_authority():
    original = legal_payload(world())
    obs = _obs(
        physical_bytes=original,
        scoring_world=world(qty=4),
        proposal_sha256=sha256_bytes(original),
        receiver_log=[_accept(sha256_bytes(original))],
        outdated_scenario_label="NOTICE_MISSING_OR_DELAYED",
    )
    assert authority_oracle(obs) == "NO_STALE_ACCEPTANCE_RECORDED"
    obs.outdated_scenario_label = "TIMELY_NOTICE"
    assert authority_oracle(obs) == "NO_STALE_ACCEPTANCE_RECORDED"
    assert "STALE_AUTHORITY" not in authority_oracle(obs)


def test_flipping_self_report_does_not_turn_correct_stop_into_successor_failure():
    obs = _obs(
        physical_bytes=None,
        scoring_world=world(effect_forbidden=True),
        successor_obligation="NOT_REQUIRED",
        stop_condition="MUST_STOP",
        withheld=True,
        path_self_report_successor_failed=False,
    )
    assert successor_oracle(obs)["status"] == "CORRECT_STOP"
    obs.path_self_report_successor_failed = True
    flipped = successor_oracle(obs)
    assert flipped["status"] == "CORRECT_STOP"
    assert flipped["week1_successor_status"] == "NOT_APPLICABLE"
    assert flipped["week1_successor_status"] != "NOT_COMPLETED"


def test_flipping_unknown_self_report_does_not_create_failure():
    from frozen_proposals.oracles import adjudicate, authority_oracle, evidence_oracle

    obs = _obs(receiver_provenance_missing=True, receiver_log=None, authority_log=None)
    decision, _basis = adjudicate(
        terminal="UNKNOWN",
        authority=authority_oracle(obs),
        physical="UNKNOWN",
        evidence=evidence_oracle(obs),
        duplicate_effect=False,
        runner_status="COMPLETED",
    )
    assert decision == "UNRESOLVED"
    obs.path_self_report_terminal_match = False
    obs.path_self_report_successor_failed = True
    again, _basis = adjudicate(
        terminal="UNKNOWN",
        authority=authority_oracle(obs),
        physical="UNKNOWN",
        evidence=evidence_oracle(obs),
        duplicate_effect=False,
        runner_status="COMPLETED",
    )
    assert again == "UNRESOLVED"


def test_semantic_self_report_does_not_override_conflict_or_bytes():
    legal = legal_payload(world())
    digest = sha256_bytes(legal)
    obs = _obs(
        physical_bytes=legal,
        proposal_sha256=digest,
        receiver_log=[_accept(digest), _accept(sha256_bytes(b"other"), seq=3, detail="conflict")],
        path_self_report_semantic_valid=False,
    )
    assert semantics_oracle(obs) == "VALID"
    from frozen_proposals.oracles import evidence_oracle

    assert evidence_oracle(obs) == "CONFLICT"
    obs.path_self_report_semantic_valid = True
    obs.physical_bytes = legal_payload(world(qty=9))
    assert semantics_oracle(obs) == "INVALID"
    assert evidence_oracle(obs) == "CONFLICT"
