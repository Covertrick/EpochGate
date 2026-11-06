"""UCR 工作负载。features 里没有真实成本；latent 只给评分。"""

from __future__ import annotations

import random
from dataclasses import dataclass

from epochgate.ucr_protocol import (
    ARRIVAL_FACTOR,
    COST_RATIOS,
    FAMILIES,
    FEATURE_KEYS,
    FORBIDDEN_FEATURE_KEYS,
    PREDICATE_CODES,
    TOOL_CODES,
)


@dataclass(frozen=True)
class UCRExample:
    family: str
    seed: int
    features: dict[str, float]
    true_remaining_s: float
    true_restart_s: float
    predicate_now: str
    predicate_after_probe: str
    queue_length: float
    slot_occupancy: float
    arrival_factor: float
    token_rate_true: float


def iter_family(family: str):
    spec = FAMILIES[family]
    for offset in range(spec["n"]):
        yield make_example(family, spec["seed_start"] + offset)


def make_example(family: str, seed: int) -> UCRExample:
    spec = FAMILIES[family]
    rng = random.Random((seed * 1009) ^ (sum(ord(ch) for ch in family) * 9176))
    lo, hi = spec["fields"]
    fields = rng.randint(lo, hi)
    tool = spec["tools"][rng.randrange(len(spec["tools"]))]
    generated = rng.randint(4, 48)
    prefix = generated + rng.randint(0, 12)
    token_rate_true = spec["rate"] * rng.uniform(0.85, 1.15)
    tokens_left = max(6, int((0.55 * fields + 1.4) * token_rate_true * 6))
    true_s = tokens_left / token_rate_true
    ratio = COST_RATIOS[seed % len(COST_RATIOS)]
    true_c = max(0.05, true_s * ratio * spec["replan_scale"] / 1.0)
    true_c *= rng.uniform(0.9, 1.1)
    predicate_now = ("valid", "invalid", "changed")[seed % 3]
    predicate_after = "invalid" if predicate_now == "changed" else predicate_now
    # 决策当时只能看见当前谓词。即将在 probe 窗口里翻转的事实留在 latent。
    observed_predicate = "invalid" if predicate_now == "invalid" else "valid"
    slots = (2, 4, 8)[seed % 3]
    queue = float(rng.randint(0, 8))
    occupancy = min(1.0, queue / slots)
    arrival = ARRIVAL_FACTOR[spec["arrival"]]
    observed_rate = token_rate_true * rng.uniform(0.8, 1.2)
    history_c = spec["replan_scale"] * rng.uniform(0.8, 2.2)
    features = {
        "generated_tokens": float(generated),
        "prefix_length": float(prefix),
        "tool_type": TOOL_CODES[tool],
        "tool_param_fields": float(fields),
        "recent_same_tool_service": history_c * rng.uniform(0.8, 1.2),
        "token_rate": observed_rate,
        "epoch_changed_since_start": float(rng.randint(0, 1)),
        "epoch_age": rng.uniform(0.0, 5.0),
        "has_recent_revision": float(rng.randint(0, 1)),
        "predicate_valid": 0.0 if observed_predicate == "invalid" else 1.0,
        "predicate_change_type": PREDICATE_CODES[observed_predicate],
        "queue_length": queue,
        "slot_occupancy": occupancy,
        "recent_restart_service": history_c,
        "plan_depth": float(rng.randint(1, 4)),
        "recent_replan_count": float(rng.randint(0, 3)),
        "arrival_rate": arrival,
    }
    unknown = set(features) - set(FEATURE_KEYS)
    leaked = set(features) & FORBIDDEN_FEATURE_KEYS
    if unknown or leaked:
        raise RuntimeError(f"feature schema violation: {unknown | leaked}")
    return UCRExample(
        family=family,
        seed=seed,
        features=features,
        true_remaining_s=true_s,
        true_restart_s=true_c,
        predicate_now=predicate_now,
        predicate_after_probe=predicate_after,
        queue_length=queue,
        slot_occupancy=occupancy,
        arrival_factor=arrival,
        token_rate_true=token_rate_true,
    )


def probe_service_s(example: UCRExample, *, token_cap: int = 8, second_cap: float = 0.2) -> float:
    by_tokens = token_cap / max(example.token_rate_true, 1e-6)
    return min(by_tokens, second_cap, example.true_remaining_s)


def after_probe(example: UCRExample) -> UCRExample:
    """probe 实际发生之后才揭露新谓词，并缩短剩余服务。"""
    spent = probe_service_s(example)
    features = dict(example.features)
    features["generated_tokens"] = features["generated_tokens"] + min(8.0, spent * example.token_rate_true)
    features["token_rate"] = example.token_rate_true
    features["predicate_valid"] = 0.0 if example.predicate_after_probe == "invalid" else 1.0
    features["predicate_change_type"] = PREDICATE_CODES[example.predicate_after_probe]
    return UCRExample(
        family=example.family,
        seed=example.seed,
        features=features,
        true_remaining_s=max(0.05, example.true_remaining_s - spent),
        true_restart_s=example.true_restart_s,
        predicate_now=example.predicate_after_probe,
        predicate_after_probe=example.predicate_after_probe,
        queue_length=example.queue_length,
        slot_occupancy=example.slot_occupancy,
        arrival_factor=example.arrival_factor,
        token_rate_true=example.token_rate_true,
    )
