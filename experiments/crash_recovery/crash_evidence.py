"""Predicates for the durable recovery surface.

Inputs are digests and a receipt flag read from SQLite and the filesystem.
The functions do not publish, and they do not read process memory.
"""

from __future__ import annotations


def classify_durable(
    *,
    sealed_digest: str,
    target_digest: str,
    receipt_present: bool,
    receipt_digest: str,
) -> tuple[str, str]:
    """Return ``(evidence_state, recovery_action)`` from durable facts.

    Order is the state machine:

    1. CONFLICT — both digests exist and differ (same effect key, other bytes).
    2. REPLAY_RECEIPT — receipt, seal, and target all name the same digest.
    3. CONFIRM_TARGET — target matches the seal and the receipt is absent.
    4. PUBLISH_SEALED — seal exists, target and receipt do not.
    5. UNKNOWN — no seal, no target, no receipt.
    """
    sealed = sealed_digest or ""
    target = target_digest or ""
    receipt = receipt_digest or ""
    if sealed and target and sealed != target:
        return "CONFLICT", "HOLD_CONFLICT"
    if receipt_present and sealed and receipt == sealed and target == sealed:
        return "REPLAY_RECEIPT", "REPLAY_STORED_RECEIPT"
    if target and sealed and target == sealed and not receipt_present:
        return "CONFIRM_TARGET", "CONFIRM_EXISTING_TARGET"
    if sealed and not target and not receipt_present:
        return "PUBLISH_SEALED", "PUBLISH_SEALED_BYTES"
    return "UNKNOWN", "WITHHOLD_UNSEALED"


def rsr_gate(precheck: str) -> tuple[str, str, bool]:
    """Decide whether a sealed restore may publish.

    VALID publishes. INVALID returns SEMANTICALLY_SUPERSEDED and does not
    publish. Any other oracle result, including UNKNOWN, returns UNKNOWN and
    does not publish. This does not close the window after the check.
    """
    if precheck == "VALID":
        return "PUBLISH_SEALED", "PUBLISH_SEALED_BYTES", True
    if precheck == "INVALID":
        return "SEMANTICALLY_SUPERSEDED", "WITHHOLD_SUPERSEDED", False
    return "UNKNOWN", "WITHHOLD_UNKNOWN_SEMANTICS", False


def receiver_post_kill(evidence_state: str) -> str:
    return {
        "UNKNOWN": "RECEIVER_NOT_ACCEPTED",
        "CONFIRM_TARGET": "ACCEPTED_RECEIPT_LOST",
        "CONFLICT": "EFFECT_KEY_CONFLICT",
        "PUBLISH_SEALED": "SEALED_NOT_ACCEPTED",
        "REPLAY_RECEIPT": "RECEIPT_DURABLE",
        "SEMANTICALLY_SUPERSEDED": "SEALED_SUPERSEDED",
    }[evidence_state]
