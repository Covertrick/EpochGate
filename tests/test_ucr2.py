from epochgate.ucr2_audit import gate_a0
from epochgate.ucr2_protocol import FORBIDDEN_FEATURE_KEYS
from epochgate.ucr2_workload import after_probe, iter_dev, reveal


def test_gate_a0_passes_without_a_model():
    report = gate_a0()
    assert report["pass"], report["checks"]


def test_features_hide_probe_class_and_future_predicate():
    example = next(iter_dev("F9"))
    assert "probe_class" not in example.features
    assert not set(example.features) & FORBIDDEN_FEATURE_KEYS
    revealed = reveal(example)
    assert revealed.features["generated_tokens"] == example.features["generated_tokens"]
    consumed = after_probe(example)
    assert consumed.features["generated_tokens"] >= example.features["generated_tokens"]
