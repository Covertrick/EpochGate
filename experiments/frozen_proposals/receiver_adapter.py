"""Same SharedReceiver for Direct, Admission, Claim, and Outbox.

The class is scripts/reliability_contract.py::SharedReceiver. This wrapper only
adds the harness clock, the presented authority token, and fault-injected
duplicate or conflicting deliveries. It does not replace the accept rule.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from frozen_proposals.hashing import sha256_bytes

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from reliability_contract import SharedReceiver  # noqa: E402

RECEIVER_IMPL = "reliability_contract.SharedReceiver"
RECEIVER_VERSION = "m4-shared-receiver-1"


class RecordingReceiver:
    def __init__(self, root: Path, clock: Any, proposal_sha256: str) -> None:
        self.inner = SharedReceiver(root, "effect.bin")
        self.clock = clock
        self.proposal_sha256 = proposal_sha256
        self.log: list[dict[str, Any]] = []
        self.duplicate_next = False
        self.conflict_next = False
        self.conflict_payload = b""
        self.fail_next = False
        self.foreign_payload = False
        self.elapsed_ms = 0.0
        self.last_result: dict[str, Any] | None = None

    def note_failure(self, *, effect_key: str, attempt_id: str, payload: bytes, authority_token: int, authority_epoch: int, run_id: str) -> None:
        digest = sha256_bytes(payload)
        if digest != self.proposal_sha256:
            self.foreign_payload = True
        self.log.append(
            {
                "run_id": run_id,
                "seq": self.clock.tick("receiver_fail"),
                "effect_key": effect_key,
                "attempt_id": attempt_id,
                "accepted": False,
                "detail": "receiver_fail",
                "digest": digest,
                "token": authority_token,
                "epoch": authority_epoch,
                "receipt": None,
            }
        )

    def accept(
        self,
        *,
        effect_key: str,
        attempt_id: str,
        payload: bytes,
        authority_token: int,
        authority_epoch: int,
        run_id: str,
    ) -> dict[str, Any]:
        first = self._one(
            effect_key=effect_key,
            attempt_id=attempt_id,
            payload=payload,
            authority_token=authority_token,
            authority_epoch=authority_epoch,
            run_id=run_id,
            conflict_ok=False,
        )
        if self.duplicate_next:
            self.duplicate_next = False
            self._one(
                effect_key=effect_key,
                attempt_id=attempt_id + ":dup",
                payload=payload,
                authority_token=authority_token,
                authority_epoch=authority_epoch,
                run_id=run_id,
                conflict_ok=False,
            )
        elif self.conflict_next:
            self.conflict_next = False
            self._one(
                effect_key=effect_key,
                attempt_id=attempt_id + ":conflict",
                payload=self.conflict_payload,
                authority_token=authority_token,
                authority_epoch=authority_epoch,
                run_id=run_id,
                conflict_ok=True,
            )
        self.last_result = first
        return first

    def _one(
        self,
        *,
        effect_key: str,
        attempt_id: str,
        payload: bytes,
        authority_token: int,
        authority_epoch: int,
        run_id: str,
        conflict_ok: bool,
    ) -> dict[str, Any]:
        digest = sha256_bytes(payload)
        if digest != self.proposal_sha256 and not conflict_ok:
            self.foreign_payload = True
        seq = self.clock.tick("receiver_accept")
        started = time.perf_counter()
        result = self.inner.accept(effect_key=effect_key, attempt_id=attempt_id, payload=payload)
        self.elapsed_ms += (time.perf_counter() - started) * 1000.0
        self.log.append(
            {
                "run_id": run_id,
                "seq": seq,
                "effect_key": effect_key,
                "attempt_id": attempt_id,
                "accepted": bool(result.get("applied")),
                "detail": result.get("status"),
                "digest": digest,
                "token": authority_token,
                "epoch": authority_epoch,
                "receipt": result.get("receipt"),
            }
        )
        return result

    def physical(self) -> bytes | None:
        return self.inner.physical()

    def accepted_count(self, effect_key: str) -> int:
        return self.inner.accepted_count(effect_key)
