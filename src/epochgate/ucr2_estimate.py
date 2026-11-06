"""开发集上的四类分位数模型，以及 pooled / group-conditional CQR。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import QuantileRegressor

from epochgate.ucr2_protocol import ALPHA, C_FEATURES, S_FEATURES
from epochgate.ucr2_workload import UCR2Example, time_bin


def matrix(examples: list[UCR2Example], keys: tuple[str, ...]) -> np.ndarray:
    return np.asarray([[row.features[key] for key in keys] for row in examples], dtype=float)


def values_of(examples: list[UCR2Example], target: str) -> np.ndarray:
    return np.asarray(
        [row.true_remaining_s if target == "S" else row.true_restart_s for row in examples],
        dtype=float,
    )


def group_key(example: UCR2Example) -> tuple[float, int, float]:
    return (example.features["tool_type"], time_bin(example.features["predicate_time"]), example.nominal_rate)


def _rank(n: int) -> int:
    return min(n - 1, math.ceil((n + 1) * (1 - ALPHA)) - 1)


def _quantile_pair(kind: str):
    if kind == "linear":
        return (
            QuantileRegressor(quantile=ALPHA / 2, alpha=1e-4, solver="highs"),
            QuantileRegressor(quantile=1 - ALPHA / 2, alpha=1e-4, solver="highs"),
        )
    return (
        GradientBoostingRegressor(loss="quantile", alpha=ALPHA / 2, n_estimators=60, max_depth=2, learning_rate=0.08, random_state=0),
        GradientBoostingRegressor(loss="quantile", alpha=1 - ALPHA / 2, n_estimators=60, max_depth=2, learning_rate=0.08, random_state=1),
    )


@dataclass
class RawModel:
    kind: str
    keys: tuple[str, ...]
    lo: object
    hi: object
    median: object | None = None
    buckets: dict = field(default_factory=dict)
    fallback: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @classmethod
    def fit(cls, examples: list[UCR2Example], target: str, kind: str) -> "RawModel":
        keys = S_FEATURES if target == "S" else C_FEATURES
        y = values_of(examples, target)
        if kind == "bucket":
            groups: dict[tuple, list[float]] = {}
            for example, value in zip(examples, y, strict=True):
                generated = example.features["generated_tokens"]
                token_bin = 0 if generated < 15 else 1 if generated < 40 else 2
                key = (example.features["tool_type"], example.features["tool_param_fields"], time_bin(example.features["predicate_time"]), token_bin)
                groups.setdefault(key, []).append(float(value))
            buckets = {key: np.quantile(vals, [ALPHA / 2, 0.5, 1 - ALPHA / 2]) for key, vals in groups.items()}
            fallback = tuple(float(x) for x in np.quantile(y, [ALPHA / 2, 0.5, 1 - ALPHA / 2]))
            return cls(kind=kind, keys=keys, lo=None, hi=None, buckets=buckets, fallback=fallback)
        features = matrix(examples, keys)
        if kind == "hybrid":
            median = QuantileRegressor(quantile=0.5, alpha=1e-4, solver="highs")
            median.fit(features, y)
            residual = y - median.predict(features)
            lo, hi = _quantile_pair("tree")
            lo.fit(features, residual)
            hi.fit(features, residual)
            return cls(kind=kind, keys=keys, lo=lo, hi=hi, median=median)
        lo, hi = _quantile_pair("linear" if kind == "linear" else "tree")
        lo.fit(features, y)
        hi.fit(features, y)
        return cls(kind=kind, keys=keys, lo=lo, hi=hi)

    def raw(self, examples: list[UCR2Example]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if self.kind == "bucket":
            lower, mid, upper = [], [], []
            for example in examples:
                generated = example.features["generated_tokens"]
                token_bin = 0 if generated < 15 else 1 if generated < 40 else 2
                key = (example.features["tool_type"], example.features["tool_param_fields"], time_bin(example.features["predicate_time"]), token_bin)
                lo, center, hi = self.buckets.get(key, self.fallback)
                lower.append(lo)
                mid.append(center)
                upper.append(hi)
            return np.asarray(lower), np.asarray(upper), np.asarray(mid)
        features = matrix(examples, self.keys)
        if self.kind == "hybrid":
            center = self.median.predict(features)
            lower = center + self.lo.predict(features)
            upper = center + self.hi.predict(features)
        else:
            lower = self.lo.predict(features)
            upper = self.hi.predict(features)
            center = 0.5 * (lower + upper)
        lower, upper = np.minimum(lower, upper), np.maximum(lower, upper)
        return np.maximum(0.0, lower), np.maximum(0.0, upper), np.maximum(0.0, center)


@dataclass
class ConformalModel:
    raw_model: RawModel
    grouped: bool
    pooled: float = 0.0
    groups: dict[tuple, float] = field(default_factory=dict)

    def calibrate(self, examples: list[UCR2Example], target: str) -> None:
        lower, upper, _ = self.raw_model.raw(examples)
        y = values_of(examples, target)
        scores = np.maximum(lower - y, y - upper)
        self.pooled = float(np.sort(scores)[max(0, _rank(len(scores)))])
        if not self.grouped:
            return
        buckets: dict[tuple, list[float]] = {}
        for example, score in zip(examples, scores, strict=True):
            buckets.setdefault(group_key(example), []).append(float(score))
        self.groups = {key: float(np.sort(vals)[max(0, _rank(len(vals)))]) for key, vals in buckets.items()}

    def _expansion(self, example: UCR2Example) -> float:
        if self.grouped:
            return self.groups.get(group_key(example), self.pooled)
        return self.pooled

    def interval(self, example: UCR2Example) -> tuple[float, float]:
        lower, upper, _ = self.raw_model.raw([example])
        mid = 0.5 * (float(lower[0]) + float(upper[0]))
        half = 0.5 * (float(upper[0]) - float(lower[0])) + self._expansion(example)
        return max(0.0, mid - half), mid + half

    def point(self, example: UCR2Example) -> float:
        _, _, center = self.raw_model.raw([example])
        return float(center[0])


def fit_pair(examples: list[UCR2Example], kind: str, grouped: bool, calibration: list[UCR2Example]) -> tuple[ConformalModel, ConformalModel]:
    remaining = ConformalModel(RawModel.fit(examples, "S", kind), grouped)
    restart = ConformalModel(RawModel.fit(examples, "C", kind), grouped)
    remaining.calibrate(calibration, "S")
    restart.calibrate(calibration, "C")
    return remaining, restart
