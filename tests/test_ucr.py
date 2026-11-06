import inspect

from epochgate.ucr_cost import action_cost
from epochgate.ucr_policy import decide
from epochgate.ucr_protocol import FEATURE_KEYS, FORBIDDEN_FEATURE_KEYS
from epochgate.ucr_replay import cost_oracle
from epochgate.ucr_workload import iter_family, make_example


def test_features_do_not_carry_latent_costs() -> None:
    example = make_example("F5", 3002)
    assert set(example.features) == set(FEATURE_KEYS)
    assert not set(example.features) & FORBIDDEN_FEATURE_KEYS
    assert "plan_epoch" not in example.features
    assert example.predicate_now == "changed"
    assert example.features["predicate_valid"] == 1.0


def test_changed_predicate_is_hidden_until_probe() -> None:
    example = next(item for item in iter_family("F1") if item.predicate_now == "changed")
    assert example.features["predicate_valid"] == 1.0
    assert example.predicate_after_probe == "invalid"


def test_probe_cannot_be_chosen_twice() -> None:
    features = make_example("F2", 1002).features
    overlapping = (1.0, 3.0)
    first = decide(
        features, overlapping, overlapping,
        information_value=5.0, probe_cost=0.1, allow_probe=True, already_probed=False,
    )
    second = decide(
        features, overlapping, overlapping,
        information_value=5.0, probe_cost=0.1, allow_probe=True, already_probed=True,
    )
    assert first == "probe"
    assert second == "continue"


def test_invalid_predicate_aborts_without_looking_at_cost() -> None:
    features = dict(make_example("F2", 1001).features)
    features["predicate_valid"] = 0.0
    action = decide(
        features, (0.0, 0.1), (10.0, 11.0),
        information_value=0.0, probe_cost=1.0, allow_probe=True, already_probed=False,
    )
    assert action == "abort-and-replan"


def test_separated_intervals_pick_the_cheaper_side() -> None:
    features = make_example("F2", 1002).features
    abort = decide(
        features, (5.0, 6.0), (1.0, 2.0),
        information_value=0.0, probe_cost=1.0, allow_probe=True, already_probed=False,
    )
    hold = decide(
        features, (1.0, 2.0), (5.0, 6.0),
        information_value=0.0, probe_cost=1.0, allow_probe=True, already_probed=False,
    )
    assert abort == "abort-and-replan"
    assert hold == "continue"


def test_cost_oracle_does_not_accept_a_future_predicate() -> None:
    assert "predicate_after" not in inspect.signature(cost_oracle).parameters
    example = make_example("F6", 3000)
    action = cost_oracle(example)
    cheaper = "abort-and-replan" if example.true_restart_s <= example.true_remaining_s else "continue"
    if example.predicate_now == "invalid":
        cheaper = "abort-and-replan"
    assert action == cheaper


def test_covered_separation_implies_the_cheaper_action() -> None:
    example = make_example("F2", 1100)
    if example.true_restart_s < example.true_remaining_s:
        assert action_cost(example.true_restart_s, example, None) <= action_cost(example.true_remaining_s, example, None)
    else:
        assert action_cost(example.true_remaining_s, example, None) <= action_cost(example.true_restart_s, example, None)
