"""校准区间上的继续、中止和一次 probe。策略函数不接收 latent。"""

from __future__ import annotations

from epochgate.ucr_protocol import ETA, MARGIN


def bounds_overlap(s_lo: float, s_hi: float, c_lo: float, c_hi: float) -> bool:
    return c_hi >= s_lo and s_hi >= c_lo


def overlap_width(s_lo: float, s_hi: float, c_lo: float, c_hi: float) -> float:
    if not bounds_overlap(s_lo, s_hi, c_lo, c_hi):
        return 0.0
    return min(s_hi, c_hi) - max(s_lo, c_lo)


def probe_budget_from_features(features: dict[str, float]) -> float:
    rate = max(float(features["token_rate"]), 1e-6)
    return min(8.0 / rate, 0.2)


def decide(
    features: dict[str, float],
    s_interval: tuple[float, float],
    c_interval: tuple[float, float],
    *,
    information_value: float,
    probe_cost: float,
    allow_probe: bool,
    already_probed: bool,
    candidate_formed: bool = False,
) -> str:
    """只看特征和区间。information_value 来自校准表，不是这条 trace 的未来。"""
    if float(features["predicate_valid"]) < 0.5:
        return "abort-and-replan"
    if candidate_formed:
        return "accept-if-valid"
    s_lo, s_hi = s_interval
    c_lo, c_hi = c_interval
    if c_hi + MARGIN < s_lo:
        return "abort-and-replan"
    if s_hi + MARGIN < c_lo:
        return "continue"
    if allow_probe and not already_probed and information_value > probe_cost + ETA:
        return "probe"
    return "continue"
