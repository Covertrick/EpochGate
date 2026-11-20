"""Path adapters.

Direct is B0. Admission is B1: the eligibility read is latched, and the window
until send stays open. Claim is B3 byte replay, not a semantic claim. Outbox
is a new same-database relay. B2 is not relabeled as any of these.
"""

from __future__ import annotations

from typing import Any

from frozen_proposals.baselines.byte_claim import ByteClaim
from frozen_proposals.baselines.outbox import Outbox
from frozen_proposals.db import read_authority


class DirectPath:
    name = "Direct"
    supports_recovery = False

    def authorize(self, ctx: Any) -> bool:
        return True

    def blob(self, ctx: Any) -> bytes:
        return ctx.request.payload

    def has_durable(self, ctx: Any) -> bool:
        return False

    def mark_sending(self, ctx: Any) -> None:
        return None

    def acknowledge(self, ctx: Any, receipt: dict | None) -> None:
        ctx.local_ack = True


class AdmissionPath:
    name = "Admission"
    supports_recovery = False

    def authorize(self, ctx: Any) -> bool:
        current = read_authority(ctx.conn)
        request = ctx.request
        self._latched = bool(
            current["eligible"]
            and current["token"] == request.authority_token
            and current["epoch"] == request.authority_epoch
        )
        return self._latched

    def blob(self, ctx: Any) -> bytes:
        return ctx.request.payload

    def has_durable(self, ctx: Any) -> bool:
        return False

    def mark_sending(self, ctx: Any) -> None:
        return None

    def acknowledge(self, ctx: Any, receipt: dict | None) -> None:
        ctx.local_ack = True


class ClaimPath:
    name = "Claim"
    supports_recovery = True

    def __init__(self) -> None:
        self.store = ByteClaim()

    def authorize(self, ctx: Any) -> bool:
        return self.store.commit(ctx.conn, ctx.request, ctx.faults)

    def blob(self, ctx: Any) -> bytes:
        return self.store.payload(ctx.conn, ctx.request.idempotency_key)

    def has_durable(self, ctx: Any) -> bool:
        return self.store.has_row(ctx.conn, ctx.request.idempotency_key)

    def mark_sending(self, ctx: Any) -> None:
        if self.has_durable(ctx):
            self.store.mark_sending(ctx.conn, ctx.request.idempotency_key)

    def acknowledge(self, ctx: Any, receipt: dict | None) -> None:
        ctx.local_ack = True
        if self.has_durable(ctx):
            receipt_id = None if receipt is None else receipt.get("receipt")
            self.store.acknowledge(ctx.conn, ctx.request.idempotency_key, receipt_id)


class OutboxPath:
    name = "Outbox"
    supports_recovery = True

    def __init__(self) -> None:
        self.store = Outbox()

    def authorize(self, ctx: Any) -> bool:
        return self.store.commit(ctx.conn, ctx.request, ctx.faults)

    def blob(self, ctx: Any) -> bytes:
        return self.store.payload(ctx.conn, ctx.request.outbox_event_id)

    def has_durable(self, ctx: Any) -> bool:
        return self.store.has_row(ctx.conn, ctx.request.outbox_event_id)

    def mark_sending(self, ctx: Any) -> None:
        if self.has_durable(ctx):
            self.store.mark_sending(ctx.conn, ctx.request.outbox_event_id)

    def acknowledge(self, ctx: Any, receipt: dict | None) -> None:
        ctx.local_ack = True
        if self.has_durable(ctx):
            receipt_id = None if receipt is None else receipt.get("receipt")
            self.store.acknowledge(ctx.conn, ctx.request.outbox_event_id, receipt_id)


def path_factories() -> dict[str, type]:
    return {
        "Direct": DirectPath,
        "Admission": AdmissionPath,
        "Claim": ClaimPath,
        "Outbox": OutboxPath,
    }
