"""In-process fault schedule. FaultCut is not process termination and not power loss."""

from __future__ import annotations

from typing import Any


class FaultCut(Exception):
    def __init__(self, point: str) -> None:
        super().__init__(point)
        self.point = point


class FaultState:
    def __init__(self, schedule: dict[str, Any] | None) -> None:
        self.schedule = schedule or {"points": []}
        self.fired: list[str] = []
        self._applied: set[str] = set()
        self.receiver_fail = False
        self.duplicate = False
        self.conflict = False
        self.restart = False

    def actions_for(self, point: str) -> list[dict[str, Any]]:
        found = []
        for step in self.schedule.get("points", []):
            if step.get("point") == point:
                found.extend(step.get("actions", []))
        return found

    def hit(self, point: str, world: dict[str, Any], auth: dict[str, Any]) -> None:
        if point in self._applied:
            return
        self._applied.add(point)
        actions = self.actions_for(point)
        if not actions:
            return
        self.fired.append(point)
        for action in actions:
            self._apply(action.get("op", ""), world, auth)

    def _apply(self, op: str, world: dict[str, Any], auth: dict[str, Any]) -> None:
        if op == "revoke":
            auth["token"] = int(auth["token"]) + 1
            auth["epoch"] = int(auth["epoch"]) + 1
            auth["eligible"] = False
            world["effect_forbidden"] = True
            return
        if op == "supersede":
            auth["token"] = int(auth["token"]) + 1
            auth["epoch"] = int(auth["epoch"]) + 1
            auth["eligible"] = True
            world["qty"] = world["successor_qty"]
            world["generation"] = int(world["generation"]) + 1
            world["effect_forbidden"] = False
            return
        if op == "drift":
            world["qty"] = world["drift_qty"]
            return
        if op == "stale_epoch":
            auth["token"] = int(auth["token"]) + 1
            auth["epoch"] = int(auth["epoch"]) + 1
            auth["eligible"] = True
            return
        if op == "receiver_fail":
            self.receiver_fail = True
            return
        if op == "duplicate_delivery":
            self.duplicate = True
            return
        if op == "conflict_delivery":
            self.conflict = True
            return
        if op == "restart_after_commit":
            self.restart = True
            return
        raise ValueError(f"unknown fault op {op}")
