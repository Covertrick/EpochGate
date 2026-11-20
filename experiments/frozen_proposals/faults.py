"""Shared fault schedule. The same schedule object is installed on every path."""

from __future__ import annotations

import copy
from typing import Any, Callable

class FaultCut(Exception):
    """In-process fault injection. This is not process.kill and not power loss."""

    def __init__(self, point: str) -> None:
        super().__init__(point)
        self.point = point


def apply_op(world: dict, auth: dict, op: str) -> bool:
    """Mutate world and authority. Return True when authority changed."""
    if op == "revoke":
        auth["token"] += 1
        auth["epoch"] += 1
        auth["eligible"] = False
        world["effect_forbidden"] = True
        return True
    if op == "supersede":
        auth["token"] += 1
        auth["epoch"] += 1
        auth["eligible"] = True
        world["qty"] = world["successor_qty"]
        world["generation"] = int(world["generation"]) + 1
        world["effect_forbidden"] = False
        return True
    if op == "drift":
        world["qty"] = world["drift_qty"]
        return False
    if op == "stale_epoch":
        auth["token"] += 1
        auth["epoch"] += 1
        auth["eligible"] = True
        return True
    if op == "concurrent_edit":
        auth["token"] += 1
        auth["epoch"] += 1
        auth["eligible"] = True
        world["qty"] = world["drift_qty"]
        world["generation"] = int(world["generation"]) + 1
        return True
    if op in {"crash", "duplicate_delivery", "conflict_delivery", "receiver_fail"}:
        return False
    raise ValueError(f"unknown fault op {op}")


def simulate(world: dict, auth: dict, schedule: dict) -> tuple[dict, dict]:
    world_after = copy.deepcopy(world)
    auth_after = copy.deepcopy(auth)
    for step in schedule["points"]:
        for action in step["actions"]:
            apply_op(world_after, auth_after, action["op"])
    return world_after, auth_after


class FaultController:
    def __init__(self, schedule: dict, world: dict, auth: dict, write_auth: Callable[[dict, str], None], receiver: Any) -> None:
        self.schedule = schedule
        self.world = world
        self.auth = auth
        self.write_auth = write_auth
        self.receiver = receiver
        self.fired: list[str] = []
        self._applied: set[str] = set()

    def actions_for(self, point: str) -> list[dict]:
        for step in self.schedule["points"]:
            if step["point"] == point:
                return list(step["actions"])
        return []

    def hit(self, point: str) -> None:
        if point in self._applied:
            return
        self._applied.add(point)
        actions = self.actions_for(point)
        if not actions:
            return
        self.fired.append(point)
        for action in actions:
            changed = apply_op(self.world, self.auth, action["op"])
            if changed:
                self.write_auth(self.auth, action["op"])
            op = action["op"]
            if op == "duplicate_delivery":
                self.receiver.duplicate_next = True
            elif op == "conflict_delivery":
                self.receiver.conflict_next = True
                self.receiver.conflict_payload = bytes.fromhex(action["payload_hex"])
            elif op == "receiver_fail":
                self.receiver.fail_next = True
            elif op == "crash":
                raise FaultCut(point)
