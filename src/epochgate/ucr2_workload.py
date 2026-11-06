"""UCR-2.1 生成器。真实成本留在 latent，特征里没有 probe 之后的状态。"""

from __future__ import annotations

import random
from dataclasses import dataclass

from epochgate.ucr2_protocol import (
    BAD_NEWS_S,
    BENEFIT_S,
    CELL_SIZE,
    COSTLY_PROBE_SECONDS,
    information_probability,
    DEV_FAMILIES,
    FEATURE_KEYS,
    FORBIDDEN_FEATURE_KEYS,
    HOLDOUT_FAMILIES,
    PROBE_SECONDS,
    PROBE_TOKENS,
    RATES,
    SCALES,
    STRESS_FAMILIES,
    TAU_BANDS,
    TIME_BINS,
    TOOL_CODES,
    TOOLS,
    FIELDS,
)


@dataclass(frozen=True)
class UCR2Example:
    family: str
    seed: int
    features: dict[str, float]
    true_remaining_s: float
    true_restart_s: float
    predicate_now: str
    predicate_after_probe: str
    candidate_now: bool
    candidate_after: bool
    probe_class: str
    n_total: float
    token_rate_true: float
    completion_horizon: float
    queue_length: float
    slot_occupancy: float
    arrival_factor: float
    nominal_rate: float
    replan_scale: float
    delta_s: float
    delta_c: float


def time_bin(tau: float) -> int:
    if tau < TIME_BINS[0]:
        return 0
    if tau < TIME_BINS[1]:
        return 1
    return 2


def n_total(tool: str, fields: int, tau: float) -> float:
    spec = TOOLS[tool]
    return spec["a"] + spec["b"] * fields + 10.0 * tau


def horizon_for(tool: str, fields: int, tau_hi: float, rate_lo: float) -> float:
    """族内最大名义剩余服务，再留出噪声余量。不读取测试标签。"""
    spec = TOOLS[tool]
    longest = spec["t_fixed"] + n_total(tool, fields, tau_hi) / rate_lo + 0.25
    return longest + 1.0


def _rng(family: str, seed: int) -> random.Random:
    return random.Random((seed * 1009) ^ (sum(ord(ch) for ch in family) * 9176))


def _sample(
    family: str,
    seed: int,
    *,
    tool: str,
    band: str,
    nominal_rate: float,
    fields: int,
    scale: float,
) -> UCR2Example:
    rng = _rng(family, seed)
    lo, hi = TAU_BANDS[band]
    tau = rng.uniform(lo, hi)
    rate = nominal_rate * rng.uniform(0.9, 1.1)
    total = n_total(tool, fields, tau)
    generated = rng.randrange(int(total))
    spec = TOOLS[tool]
    eps_s = rng.uniform(-0.15, 0.15)
    eps_c = rng.uniform(-0.15, 0.15)
    shared = spec["t_replan"] * tau
    true_s = spec["t_fixed"] + max(1.0, total - generated) / rate + shared + eps_s
    full = spec["t_fixed"] + total / rate
    true_c = (0.52 + 0.16 * (scale / 1.2)) * full + shared + eps_c
    true_c = max(0.05, true_c)
    true_s = max(0.05, true_s)
    probe_class = ("uninformative", "informative", "costly")[seed % 3]
    invalid = rng.random() < 0.08
    predicate_now = "invalid" if invalid else "valid"
    delta_s = 0.0
    delta_c = 0.0
    predicate_after = predicate_now
    candidate_after = False
    if probe_class != "uninformative" and rng.random() < information_probability(tau):
        if rng.random() < 0.5 and predicate_now == "valid":
            candidate_after = True
        else:
            delta_s = BAD_NEWS_S
            delta_c = BAD_NEWS_S
    tau_hi = TAU_BANDS[band][1]
    rate_lo = nominal_rate * 0.9
    horizon = horizon_for(tool, fields, tau_hi, rate_lo)
    budget = COSTLY_PROBE_SECONDS if probe_class == "costly" else min(PROBE_TOKENS / max(rate, 1e-6), PROBE_SECONDS)
    observed_rate = rate * rng.uniform(0.85, 1.15)
    restart_proxy = scale * rng.uniform(0.85, 1.15)
    queue = float(rng.randint(0, 8))
    slots = (2, 4, 8)[seed % 3]
    features = {
        "generated_tokens": float(generated),
        "tool_type": TOOL_CODES[tool],
        "tool_param_fields": float(fields),
        "token_rate": observed_rate,
        "predicate_time": tau,
        "predicate_valid": 0.0 if predicate_now == "invalid" else 1.0,
        "queue_length": queue,
        "slot_occupancy": min(1.0, queue / slots),
        "recent_restart_service": restart_proxy,
        "completion_horizon": horizon,
        "probe_budget": budget,
        "nominal_rate": nominal_rate,
    }
    unknown = set(features) - set(FEATURE_KEYS)
    leaked = set(features) & FORBIDDEN_FEATURE_KEYS
    if unknown or leaked:
        raise RuntimeError(f"feature schema violation: {unknown | leaked}")
    return UCR2Example(
        family=family,
        seed=seed,
        features=features,
        true_remaining_s=true_s,
        true_restart_s=true_c,
        predicate_now=predicate_now,
        predicate_after_probe=predicate_after,
        candidate_now=False,
        candidate_after=candidate_after,
        probe_class=probe_class,
        n_total=total,
        token_rate_true=rate,
        completion_horizon=horizon,
        queue_length=queue,
        slot_occupancy=features["slot_occupancy"],
        arrival_factor=1.0,
        nominal_rate=nominal_rate,
        replan_scale=scale,
        delta_s=delta_s,
        delta_c=delta_c,
    )


def iter_dev(family: str):
    spec = DEV_FAMILIES[family]
    for offset in range(spec["n"]):
        seed = spec["seed_start"] + offset
        rng = _rng(family + "-assign", seed)
        band = spec["bands"][offset % len(spec["bands"])]
        fields = spec["fields"][offset % len(spec["fields"])]
        scale = spec["scales"][(offset // 2) % len(spec["scales"])]
        yield _sample(
            family, seed, tool=spec["tool"], band=band, nominal_rate=spec["rate"], fields=fields, scale=scale,
        )
        del rng


def iter_calibration():
    cell = 0
    for tool in ("reserve", "query"):
        family = "F11" if tool == "reserve" else "F12"
        for band in ("early", "mid", "late"):
            for rate in RATES:
                for offset in range(CELL_SIZE):
                    seed = 10000 + cell * 100 + offset
                    fields = FIELDS[offset % len(FIELDS)]
                    scale = SCALES[(offset // len(FIELDS)) % len(SCALES)]
                    yield _sample(
                        family, seed, tool=tool, band=band, nominal_rate=rate, fields=fields, scale=scale,
                    )
                cell += 1


def iter_holdout(family: str):
    spec = HOLDOUT_FAMILIES[family]
    for offset in range(spec["n"]):
        yield _sample(
            family,
            spec["seed_start"] + offset,
            tool=spec["tool"],
            band=spec["band"],
            nominal_rate=spec["rate"],
            fields=spec["fields"],
            scale=spec["scale"],
        )


def iter_stress(family: str):
    spec = STRESS_FAMILIES[family]
    fields_spec = spec["fields"]
    for offset in range(spec["n"]):
        if isinstance(fields_spec, tuple):
            lo, hi = fields_spec
            fields = lo + (offset % (hi - lo + 1))
        else:
            fields = int(fields_spec)
        yield _sample(
            family,
            spec["seed_start"] + offset,
            tool=spec["tool"],
            band=spec["band"],
            nominal_rate=spec["rate"],
            fields=fields,
            scale=spec["scale"],
        )


def probe_service_s(example: UCR2Example) -> float:
    return min(float(example.features["probe_budget"]), example.true_remaining_s)


def reveal(example: UCR2Example) -> UCR2Example:
    """只揭露 probe 的信息，不把消耗的服务算进收益。"""
    return _after(example, spent=0.0)


def after_probe(example: UCR2Example) -> UCR2Example:
    return _after(example, spent=probe_service_s(example))


def _after(example: UCR2Example, spent: float) -> UCR2Example:
    features = dict(example.features)
    features["generated_tokens"] = features["generated_tokens"] + min(8.0, spent * example.token_rate_true)
    if spent > 0:
        features["token_rate"] = example.token_rate_true
    features["predicate_valid"] = 0.0 if example.predicate_after_probe == "invalid" else 1.0
    new_s = max(0.05, example.true_remaining_s - spent + example.delta_s)
    new_c = max(0.05, example.true_restart_s + example.delta_c)
    return UCR2Example(
        family=example.family,
        seed=example.seed,
        features=features,
        true_remaining_s=new_s,
        true_restart_s=new_c,
        predicate_now=example.predicate_after_probe,
        predicate_after_probe=example.predicate_after_probe,
        candidate_now=example.candidate_after,
        candidate_after=example.candidate_after,
        probe_class=example.probe_class,
        n_total=example.n_total,
        token_rate_true=example.token_rate_true,
        completion_horizon=example.completion_horizon,
        queue_length=example.queue_length,
        slot_occupancy=example.slot_occupancy,
        arrival_factor=example.arrival_factor,
        nominal_rate=example.nominal_rate,
        replan_scale=example.replan_scale,
        delta_s=0.0,
        delta_c=0.0,
    )


def accept_service(example: UCR2Example) -> float:
    base = min(example.true_remaining_s, example.true_restart_s)
    return max(0.05, base - BENEFIT_S)
