"""分开的剩余服务、重规划服务分位数模型，以及校准集上的影子价格。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor

from epochgate.ucr_cost import wait_per_service
from epochgate.ucr_protocol import ALPHA, C_FEATURES, S_FEATURES
from epochgate.ucr_workload import UCRExample


def matrix(examples: list[UCRExample], keys: tuple[str, ...]) -> np.ndarray:
    return np.asarray([[example.features[key] for key in keys] for example in examples], dtype=float)


@dataclass
class QuantileModel:
    keys: tuple[str, ...]
    lo: GradientBoostingRegressor
    hi: GradientBoostingRegressor
    expansion: float = 0.0

    @classmethod
    def fit(cls, examples: list[UCRExample], keys: tuple[str, ...], target: str) -> "QuantileModel":
        features = matrix(examples, keys)
        values = np.asarray(
            [example.true_remaining_s if target == "S" else example.true_restart_s for example in examples],
            dtype=float,
        )
        lo = GradientBoostingRegressor(loss="quantile", alpha=ALPHA / 2, n_estimators=60, max_depth=2, learning_rate=0.08, random_state=0)
        hi = GradientBoostingRegressor(loss="quantile", alpha=1 - ALPHA / 2, n_estimators=60, max_depth=2, learning_rate=0.08, random_state=1)
        lo.fit(features, values)
        hi.fit(features, values)
        return cls(keys=keys, lo=lo, hi=hi)

    def raw(self, examples: list[UCRExample]) -> tuple[np.ndarray, np.ndarray]:
        features = matrix(examples, self.keys)
        lower = np.minimum(self.lo.predict(features), self.hi.predict(features))
        upper = np.maximum(self.lo.predict(features), self.hi.predict(features))
        return np.maximum(0.0, lower), np.maximum(0.0, upper)

    def calibrate(self, examples: list[UCRExample], target: str) -> float:
        lower, upper = self.raw(examples)
        values = np.asarray(
            [example.true_remaining_s if target == "S" else example.true_restart_s for example in examples],
            dtype=float,
        )
        scores = np.maximum(lower - values, values - upper)
        n = len(scores)
        rank = min(n - 1, math.ceil((n + 1) * (1 - ALPHA)) - 1)
        self.expansion = float(np.sort(scores)[max(0, rank)])
        return self.expansion

    def interval(self, example: UCRExample, width_scale: float = 1.0) -> tuple[float, float]:
        lower, upper = self.raw([example])
        mid = 0.5 * (float(lower[0]) + float(upper[0]))
        half = (0.5 * (float(upper[0]) - float(lower[0])) + self.expansion) * width_scale
        return max(0.0, mid - half), mid + half


@dataclass
class ShadowTable:
    buckets: dict[tuple[int, int, int], float] = field(default_factory=dict)
    default: float = 0.0

    @staticmethod
    def _key(example: UCRExample) -> tuple[int, int, int]:
        queue_bin = 0 if example.queue_length <= 2 else 1 if example.queue_length <= 5 else 2
        occupy_bin = 0 if example.slot_occupancy < 0.5 else 1
        arrival_bin = 0 if example.arrival_factor < 0.8 else 1 if example.arrival_factor < 1.5 else 2
        return queue_bin, occupy_bin, arrival_bin

    @classmethod
    def fit(cls, examples: list[UCRExample]) -> "ShadowTable":
        groups: dict[tuple[int, int, int], list[float]] = {}
        for example in examples:
            groups.setdefault(cls._key(example), []).append(
                wait_per_service(example.queue_length, example.slot_occupancy, example.arrival_factor)
            )
        buckets = {key: sum(values) / len(values) for key, values in groups.items()}
        default = sum(buckets.values()) / len(buckets) if buckets else 0.0
        return cls(buckets=buckets, default=default)

    def lookup(self, example: UCRExample) -> float:
        return self.buckets.get(self._key(example), self.default)


def fit_models(dev: list[UCRExample], cal: list[UCRExample]) -> tuple[QuantileModel, QuantileModel, ShadowTable]:
    remaining = QuantileModel.fit(dev, S_FEATURES, "S")
    restart = QuantileModel.fit(dev, C_FEATURES, "C")
    remaining.calibrate(cal, "S")
    restart.calibrate(cal, "C")
    return remaining, restart, ShadowTable.fit(cal)


def coverage(model: QuantileModel, examples: list[UCRExample], target: str, width_scale: float = 1.0) -> float:
    if not examples:
        return 0.0
    hits = 0
    for example in examples:
        lower, upper = model.interval(example, width_scale)
        value = example.true_remaining_s if target == "S" else example.true_restart_s
        hits += int(lower <= value <= upper)
    return hits / len(examples)
