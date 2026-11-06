"""校准集上的信息价值，以及插值留出上的策略回放。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from epochgate.ucr2_cost import action_cost, cost_oracle, fails, terminal_cost
from epochgate.ucr2_estimate import ConformalModel
from epochgate.ucr2_policy import bounds_overlap, decide, overlap_width
from epochgate.ucr2_protocol import RISK_THRESHOLD
from epochgate.ucr2_workload import UCR2Example, after_probe, probe_service_s, reveal, time_bin
from epochgate.ucr2_audit import information_value


@dataclass
class ValueTable:
    sums: dict[tuple, float]
    counts: dict[tuple, int]

    @staticmethod
    def key(example: UCR2Example, width: float) -> tuple:
        width_bin = 0 if width <= 0 else 1 if width < 1 else 2
        queue_bin = 0 if example.queue_length <= 2 else 1 if example.queue_length <= 5 else 2
        occupy_bin = 0 if example.slot_occupancy < 0.5 else 1
        budget_bin = 0 if example.features["probe_budget"] < 1 else 1
        return (width_bin, queue_bin, occupy_bin, int(example.features["predicate_valid"]), budget_bin)

    @classmethod
    def fit(cls, examples: list[UCR2Example], remaining: ConformalModel, restart: ConformalModel) -> "ValueTable":
        sums: dict[tuple, float] = defaultdict(float)
        counts: dict[tuple, int] = defaultdict(int)
        for example in examples:
            if example.predicate_now == "invalid":
                continue
            s_interval = remaining.interval(example)
            c_interval = restart.interval(example)
            width = overlap_width(*s_interval, *c_interval)
            if width <= 0:
                continue
            value, _ = information_value(example)
            slot = cls.key(example, width)
            sums[slot] += value
            counts[slot] += 1
        return cls(sums=dict(sums), counts=dict(counts))

    def lookup(self, example: UCR2Example, width: float) -> float:
        slot = self.key(example, width)
        count = self.counts.get(slot, 0)
        if count == 0:
            return 0.0
        return self.sums[slot] / count


@dataclass
class FailureTable:
    rates: dict[tuple[int, int], float]
    default: float

    @classmethod
    def fit(cls, examples: list[UCR2Example]) -> "FailureTable":
        groups: dict[tuple[int, int], list[float]] = {}
        for example in examples:
            key = (int(example.features["predicate_valid"]), time_bin(example.features["predicate_time"]))
            groups.setdefault(key, []).append(float(fails("continue", example)))
        rates = {key: sum(vals) / len(vals) for key, vals in groups.items()}
        default = sum(rates.values()) / len(rates) if rates else 0.0
        return cls(rates=rates, default=default)

    def lookup(self, example: UCR2Example) -> float:
        key = (int(example.features["predicate_valid"]), time_bin(example.features["predicate_time"]))
        return self.rates.get(key, self.default)


@dataclass(frozen=True)
class ReplayScore:
    action: str
    probed: bool
    action_changed: bool
    cost: float
    failed: bool
    overlap: bool
    covered_s: bool
    covered_c: bool
    selective_violation: bool
    probe_bound_violation: bool
    s_width: float
    c_width: float


def _covered(value: float, interval: tuple[float, float]) -> bool:
    return interval[0] <= value <= interval[1]


def play(
    example: UCR2Example,
    *,
    policy: str,
    remaining: ConformalModel | None,
    restart: ConformalModel | None,
    value_table: ValueTable | None,
    failure_table: FailureTable | None,
) -> ReplayScore:
    blank = ReplayScore("continue", False, False, 0.0, False, False, False, False, False, False, 0.0, 0.0)
    if policy == "always-continue":
        action = "continue"
    elif policy == "always-abort":
        action = "abort-and-replan"
    elif policy == "predicate-only":
        action = "abort-and-replan" if example.predicate_now == "invalid" else "continue"
    elif policy == "cost-oracle":
        action = cost_oracle(example)
    elif policy == "expected-value":
        if remaining is None or restart is None or failure_table is None:
            raise ValueError(policy)
        if example.predicate_now == "invalid" or failure_table.lookup(example) > RISK_THRESHOLD:
            action = "abort-and-replan"
        elif restart.point(example) < remaining.point(example):
            action = "abort-and-replan"
        else:
            action = "continue"
    else:
        if remaining is None or restart is None or value_table is None:
            raise ValueError(policy)
        s_interval = remaining.interval(example)
        c_interval = restart.interval(example)
        s_width = s_interval[1] - s_interval[0]
        c_width = c_interval[1] - c_interval[0]
        if policy == "point":
            s_point, c_point = remaining.point(example), restart.point(example)
            s_interval = (s_point, s_point)
            c_interval = (c_point, c_point)
        width = overlap_width(*s_interval, *c_interval)
        overlap = width > 0
        budget = float(example.features["probe_budget"])
        probe_cost = action_cost(budget, example, None)
        allow = policy == "full"
        action = decide(
            example.features, s_interval, c_interval,
            information_value=value_table.lookup(example, width),
            probe_cost=probe_cost, allow_probe=allow, already_probed=False,
            candidate_formed=example.candidate_now,
        )
        covered_s = _covered(example.true_remaining_s, remaining.interval(example))
        covered_c = _covered(example.true_restart_s, restart.interval(example))
        no_probe_action = decide(
            example.features, s_interval, c_interval,
            information_value=0.0, probe_cost=probe_cost, allow_probe=False, already_probed=False,
            candidate_formed=example.candidate_now,
        )
        selective = False
        if covered_s and covered_c and not overlap:
            continue_cost, _ = terminal_cost("continue", example, None)
            abort_cost, _ = terminal_cost("abort-and-replan", example, None)
            if c_interval[1] < s_interval[0] and abort_cost > continue_cost:
                selective = True
            if s_interval[1] < c_interval[0] and continue_cost > abort_cost:
                selective = True
        if action == "probe":
            nxt = after_probe(example)
            s2, c2 = remaining.interval(nxt), restart.interval(nxt)
            if policy == "point":
                s2 = (remaining.point(nxt), remaining.point(nxt))
                c2 = (restart.point(nxt), restart.point(nxt))
            second = decide(
                nxt.features, s2, c2, information_value=0.0, probe_cost=probe_cost,
                allow_probe=False, already_probed=True, candidate_formed=nxt.candidate_now,
            )
            fee = action_cost(probe_service_s(example), example, None)
            rest, failed = terminal_cost(second, nxt, None)
            without, _ = terminal_cost(no_probe_action, example, None)
            bound = fee + s_width + c_width
            return ReplayScore(
                second, True, second != no_probe_action, fee + rest, failed, overlap,
                covered_s, covered_c, selective, fee + rest - without > bound + 1e-8, s_width, c_width,
            )
        cost, failed = terminal_cost(action, example, None)
        return ReplayScore(action, False, False, cost, failed, overlap, covered_s, covered_c, selective, False, s_width, c_width)

    cost, failed = terminal_cost(action, example, None)
    return ReplayScore(action, False, False, cost, failed, False, False, False, False, False, blank.s_width, blank.c_width)
