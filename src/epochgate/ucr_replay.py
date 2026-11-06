"""留出评估用的单次决策回放。Cost Oracle 不读 probe 之后的谓词。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from epochgate.ucr_cost import action_cost, fails, multiplier, terminal_cost
from epochgate.ucr_estimate import QuantileModel, ShadowTable
from epochgate.ucr_policy import bounds_overlap, decide, overlap_width, probe_budget_from_features
from epochgate.ucr_workload import UCRExample, after_probe, probe_service_s


@dataclass(frozen=True)
class ReplayScore:
    action: str
    probed: bool
    action_changed: bool
    cost: float
    failed: bool
    separated: bool
    covered: bool
    overlap: bool


def cost_oracle(example: UCRExample) -> str:
    """当前真实 C、S 和当前谓词。参数列表里没有 probe 之后的谓词。"""
    if example.predicate_now == "invalid":
        return "abort-and-replan"
    continue_cost = action_cost(example.true_remaining_s, example, None)
    abort_cost = action_cost(example.true_restart_s, example, None)
    if abort_cost <= continue_cost:
        return "abort-and-replan"
    return "continue"


class ValueTable:
    def __init__(self) -> None:
        self._sums: dict[tuple[int, int], float] = defaultdict(float)
        self._counts: dict[tuple[int, int], int] = defaultdict(int)

    @staticmethod
    def key(width: float, queue_length: float) -> tuple[int, int]:
        width_bin = 0 if width <= 0 else 1 if width < 1.0 else 2
        queue_bin = 0 if queue_length <= 2 else 1 if queue_length <= 5 else 2
        return width_bin, queue_bin

    def add(self, width: float, queue_length: float, value: float) -> None:
        slot = self.key(width, queue_length)
        self._sums[slot] += value
        self._counts[slot] += 1

    def lookup(self, width: float, queue_length: float) -> float:
        slot = self.key(width, queue_length)
        count = self._counts.get(slot, 0)
        if count == 0:
            return 0.0
        return self._sums[slot] / count


def _intervals(example: UCRExample, remaining: QuantileModel, restart: QuantileModel, width_scale: float):
    return remaining.interval(example, width_scale), restart.interval(example, width_scale)


def _covered(example: UCRExample, s_interval: tuple[float, float], c_interval: tuple[float, float]) -> bool:
    s_lo, s_hi = s_interval
    c_lo, c_hi = c_interval
    return s_lo <= example.true_remaining_s <= s_hi and c_lo <= example.true_restart_s <= c_hi


def fit_value_table(
    examples: list[UCRExample],
    remaining: QuantileModel,
    restart: QuantileModel,
    shadow: ShadowTable,
    slack_s: float,
) -> ValueTable:
    """校准集上的信息价值。用的是校准 trace 的实现代价，测试集只查表。"""
    table = ValueTable()
    for example in examples:
        if example.predicate_now == "invalid":
            continue
        s_interval, c_interval = _intervals(example, remaining, restart, 1.0)
        width = overlap_width(*s_interval, *c_interval)
        if width <= 0:
            continue
        before = decide(
            example.features, s_interval, c_interval,
            information_value=0.0, probe_cost=1.0, allow_probe=False, already_probed=False,
        )
        probed = after_probe(example)
        s2, c2 = _intervals(probed, remaining, restart, 1.0)
        after = decide(
            probed.features, s2, c2,
            information_value=0.0, probe_cost=1.0, allow_probe=False, already_probed=True,
        )
        before_cost, _ = terminal_cost(before, example, slack_s, None)
        after_cost, _ = terminal_cost(after, probed, slack_s, None)
        table.add(width, example.queue_length, before_cost - after_cost)
    return table


def play(
    example: UCRExample,
    *,
    policy: str,
    remaining: QuantileModel | None,
    restart: QuantileModel | None,
    shadow: ShadowTable | None,
    value_table: ValueTable | None,
    slack_s: float,
    width_scale: float = 1.0,
) -> ReplayScore:
    probed = False
    changed = False
    if policy == "always-continue":
        action = "continue"
    elif policy == "always-abort":
        action = "abort-and-replan"
    elif policy == "predicate-only":
        action = "abort-and-replan" if example.predicate_now == "invalid" else "continue"
    elif policy == "cost-oracle":
        action = cost_oracle(example)
    else:
        if remaining is None or restart is None or shadow is None or value_table is None:
            raise ValueError("learned policies need the fitted models")
        s_interval, c_interval = _intervals(example, remaining, restart, width_scale)
        if policy == "point":
            s_mid = 0.5 * sum(s_interval)
            c_mid = 0.5 * sum(c_interval)
            s_interval = (s_mid, s_mid)
            c_interval = (c_mid, c_mid)
        hat = shadow.lookup(example)
        budget = probe_budget_from_features(example.features)
        probe_cost = action_cost(budget, example, hat)
        value = value_table.lookup(overlap_width(*s_interval, *c_interval), example.queue_length)
        allow = policy == "full"
        action = decide(
            example.features, s_interval, c_interval,
            information_value=value, probe_cost=probe_cost,
            allow_probe=allow, already_probed=False,
        )
        if action == "probe":
            probed = True
            nxt = after_probe(example)
            s2, c2 = _intervals(nxt, remaining, restart, width_scale)
            if policy == "point":
                s2 = (0.5 * sum(s2),) * 2
                c2 = (0.5 * sum(c2),) * 2
            second = decide(
                nxt.features, s2, c2,
                information_value=0.0, probe_cost=probe_cost,
                allow_probe=False, already_probed=True,
            )
            changed = second != decide(
                example.features, s_interval, c_interval,
                information_value=0.0, probe_cost=1.0, allow_probe=False, already_probed=False,
            )
            fee = action_cost(probe_service_s(example), example, None)
            rest, failed = terminal_cost(second, nxt, slack_s, None)
            covered = _covered(example, *_intervals(example, remaining, restart, width_scale))
            separated = not bounds_overlap(*_intervals(example, remaining, restart, width_scale))
            return ReplayScore(second, True, changed, fee + rest, failed, separated, covered, not separated)
        covered = _covered(example, s_interval, c_interval) if policy != "point" else False
        separated = not bounds_overlap(*s_interval, *c_interval)
        cost, failed = terminal_cost(action, example, slack_s, None)
        return ReplayScore(action, False, False, cost, failed, separated, covered, not separated)

    cost, failed = terminal_cost(action, example, slack_s, None)
    return ReplayScore(action, probed, changed, cost, failed, False, False, False)
