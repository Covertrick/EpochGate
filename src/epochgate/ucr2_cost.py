"""UCR-2.1 的代价。完成期限来自样本上的可见环境参数。"""

from __future__ import annotations

from epochgate.ucr2_protocol import LAMBDA_B, LAMBDA_S
from epochgate.ucr2_workload import UCR2Example, accept_service


def wait_per_service(queue_length: float, slot_occupancy: float, arrival_factor: float) -> float:
    return 0.2 * queue_length * arrival_factor / max(1.0 - 0.5 * slot_occupancy, 0.25)


def multiplier(example: UCR2Example, lambda_hat: float | None = None) -> float:
    rate = wait_per_service(example.queue_length, example.slot_occupancy, example.arrival_factor)
    if lambda_hat is not None:
        rate = lambda_hat
    return LAMBDA_S + LAMBDA_B * rate


def action_cost(service_s: float, example: UCR2Example, lambda_hat: float | None = None) -> float:
    return max(0.0, service_s) * multiplier(example, lambda_hat)


def service_of(action: str, example: UCR2Example) -> float:
    if action == "continue":
        return example.true_remaining_s
    if action == "abort-and-replan":
        return example.true_restart_s
    if action == "accept-if-valid":
        return accept_service(example)
    raise ValueError(action)


def fails(action: str, example: UCR2Example) -> bool:
    if action == "continue":
        if example.predicate_now == "invalid":
            return True
        return example.true_remaining_s > example.completion_horizon
    if action == "abort-and-replan":
        return example.true_restart_s > example.completion_horizon
    if action == "accept-if-valid":
        return example.predicate_now == "invalid"
    raise ValueError(action)


def terminal_cost(action: str, example: UCR2Example, lambda_hat: float | None = None) -> tuple[float, bool]:
    return action_cost(service_of(action, example), example, lambda_hat), fails(action, example)


def cost_oracle(example: UCR2Example) -> str:
    """当前真实 C、S、当前谓词和当前候选。不读 probe 之后才会出现的状态。"""
    if example.predicate_now == "invalid":
        return "abort-and-replan"
    actions = ["continue", "abort-and-replan"]
    if example.candidate_now:
        actions.append("accept-if-valid")
    return min(actions, key=lambda action: terminal_cost(action, example, None)[0])
