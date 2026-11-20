"""Immutable scoring predicate. Paths must not call this to rebuild a proposal."""

from __future__ import annotations

PREDICATE_VERSION = "predicate.fulfillment.v1"


def legal_payload(world: dict) -> bytes:
    if world.get("effect_forbidden"):
        return b""
    return (
        f"SHIP|{world['order_id']}|{world['sku']}|{world['qty']}|{world['dest']}|g{world['generation']}"
    ).encode("utf-8")
