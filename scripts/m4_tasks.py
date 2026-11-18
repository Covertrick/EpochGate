"""未见过的履约与配置任务。实例清单在模型调用之前写定。"""

from __future__ import annotations

import json
from typing import Any

ARMS = ("B0", "B1", "B2", "B3")
FAULTS = ("S1", "S2", "S3")
PROMPT = (
    "你是业务代理。先调用至少两个读取工具，其中后一个读取要使用前一个结果里的编号。"
    "然后只根据已经读到的内容调用 submit_effect 一次，提交持久效果。"
    "如果工具返回拒绝，并且给出了新的观察，可以再提交一次合法后继。"
    "不要声称已经完成，完成与否由外部持久状态决定。"
)


def _order(sku: str, qty: int, customer: str, stock: int, price_version: int, config_version: int) -> dict[str, Any]:
    return {
        "form": "order",
        "relative": "shipment.json",
        "initial": {
            "order": {"sku": sku, "qty": qty, "customer": customer},
            "inventory": {sku: stock},
            "permission": {customer: "ALLOW"},
            "price": {"version": price_version, sku: 10},
            "config": {"id": "ship-rules", "version": config_version},
        },
        "event": {"set": {"price.version": price_version + 1}},
        "constraints": ["inventory", "permission"],
    }


def _config(doc: str, version: int, approver: str, price_version: int) -> dict[str, Any]:
    return {
        "form": "config",
        "relative": "release.json",
        "initial": {
            "order": {"sku": "none", "qty": 0, "customer": approver},
            "inventory": {"none": 1},
            "permission": {approver: "ALLOW"},
            "price": {"version": price_version, "none": 1},
            "config": {"id": doc, "version": version},
            "approver": approver,
            "required_price_version": price_version,
        },
        "event": {"set": {"config.version": version + 1}},
        "constraints": ["permission", "price"],
    }


def _bind(spec: dict[str, Any], task_id: str, phase: str) -> dict[str, Any]:
    spec = json.loads(json.dumps(spec))
    spec["task_id"] = task_id
    spec["family"] = "fulfillment"
    spec["phase_pool"] = phase
    spec["successor_applicable"] = True
    spec["s2_mode"] = "missing" if int(task_id[-1], 36) % 2 == 0 else "delayed"
    return spec


def legal_payload(task: dict[str, Any], world: dict[str, Any]) -> str:
    if task["form"] == "order":
        order = world["order"]
        if world["permission"].get(order["customer"]) != "ALLOW":
            return ""
        if world["inventory"].get(order["sku"], 0) < order["qty"]:
            return ""
        body = {
            "config_version": world["config"]["version"],
            "customer": order["customer"],
            "price_version": world["price"]["version"],
            "qty": order["qty"],
            "sku": order["sku"],
        }
        return json.dumps(body, sort_keys=True)
    approver = world.get("approver")
    if world["permission"].get(approver) != "ALLOW":
        return ""
    if world["price"]["version"] != world["required_price_version"]:
        return ""
    body = {
        "id": world["config"]["id"],
        "price_version": world["price"]["version"],
        "version": world["config"]["version"],
    }
    return json.dumps(body, sort_keys=True)


def observed_payload(task: dict[str, Any]) -> bytes:
    """冻结尝试使用变化前的世界。这是模型被要求根据已有观察提交的载荷。"""
    return legal_payload(task, task["initial"]).encode("utf-8")


def attach_legal(task: dict[str, Any]) -> dict[str, Any]:
    task["legal_payload"] = lambda world: legal_payload(task, world)
    return task


PILOT = [
    attach_legal(_bind(_order("pen", 2, "c1", 5, 1, 3), "ful-order-p1", "pilot")),
    attach_legal(_bind(_config("rules-a", 3, "a1", 1), "ful-config-p2", "pilot")),
]
CONFIRM = [
    attach_legal(_bind(_order("ink", 1, "c3", 4, 2, 5), "ful-order-c1", "confirm")),
    attach_legal(_bind(_order("pad", 3, "c4", 9, 4, 6), "ful-order-c2", "confirm")),
    attach_legal(_bind(_order("box", 2, "c5", 6, 1, 2), "ful-order-c3", "confirm")),
    attach_legal(_bind(_config("rules-b", 5, "a2", 2), "ful-config-c4", "confirm")),
    attach_legal(_bind(_config("rules-c", 7, "a3", 3), "ful-config-c5", "confirm")),
    attach_legal(_bind(_config("rules-d", 2, "a4", 4), "ful-config-c6", "confirm")),
]
M5_TASKS = [
    attach_legal(_bind(_order("clip", 2, "m1", 8, 3, 4), "ful-order-m1", "m5")),
    attach_legal(_bind(_order("tape", 1, "m2", 3, 5, 8), "ful-order-m2", "m5")),
    attach_legal(_bind(_config("rules-m", 9, "a9", 6), "ful-config-m3", "m5")),
]


def pool(name: str) -> list[dict[str, Any]]:
    return {"pilot": PILOT, "confirm": CONFIRM, "m5": M5_TASKS}[name]
