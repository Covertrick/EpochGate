"""区间上的继续、中止和一次 probe。不接收 latent。"""

from __future__ import annotations

from epochgate.ucr2_protocol import ETA, MARGIN


def bounds_overlap(s_lo: float, s_hi: float, c_lo: float, c_hi: float) -> bool:
    return c_hi >= s_lo and s_hi >= c_lo


def overlap_width(s_lo: float, s_hi: float, c_lo: float, c_hi: float) -> float:
    if not bounds_overlap(s_lo, s_hi, c_lo, c_hi):
        return 0.0
    return min(s_hi, c_hi) - max(s_lo, c_lo)


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
