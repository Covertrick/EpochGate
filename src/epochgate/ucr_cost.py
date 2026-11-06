"""统一代价。服务秒和旁观者等待用同一套乘数。"""

from __future__ import annotations

from epochgate.ucr_protocol import LAMBDA_B, LAMBDA_S
from epochgate.ucr_workload import UCRExample


def wait_per_service(queue_length: float, slot_occupancy: float, arrival_factor: float) -> float:
    """每占用 1 秒服务，旁观者多等多少秒。"""
    return 0.2 * queue_length * arrival_factor / max(1.0 - 0.5 * slot_occupancy, 0.25)


def multiplier(example: UCRExample, lambda_hat: float | None = None) -> float:
    rate = wait_per_service(example.queue_length, example.slot_occupancy, example.arrival_factor)
    if lambda_hat is not None:
        rate = lambda_hat
    return LAMBDA_S + LAMBDA_B * rate


def action_cost(service_s: float, example: UCRExample, lambda_hat: float | None = None) -> float:
    return max(0.0, service_s) * multiplier(example, lambda_hat)


def fails(action: str, example: UCRExample, slack_s: float) -> bool:
    if action == "continue":
        if example.predicate_now == "invalid":
            return True
        return example.true_remaining_s > slack_s
    if action == "abort-and-replan":
        return example.true_restart_s > slack_s
    raise ValueError(f"failure is only defined for terminal actions, got {action}")


def terminal_cost(action: str, example: UCRExample, slack_s: float, lambda_hat: float | None = None) -> tuple[float, bool]:
    if action == "continue":
        cost = action_cost(example.true_remaining_s, example, lambda_hat)
    elif action == "abort-and-replan":
        cost = action_cost(example.true_restart_s, example, lambda_hat)
    else:
        raise ValueError(action)
    return cost, fails(action, example, slack_s)
