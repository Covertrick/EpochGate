"""Clients for the local receiver process.

PathReceiverClient can submit an effect and query one key. It cannot open the
receiver database and it has no audit or world-mutation method.
"""

from __future__ import annotations

import json
import socket
from typing import Any


class PathReceiverClient:
    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def submit(
        self,
        *,
        effect_key: str,
        payload: bytes,
        attempt_id: str,
        fence: bool = False,
        expected_world_version: int | None = None,
        expected_payload_hash: str | None = None,
        expected_authority: dict[str, Any] | None = None,
        authority_token: int = 0,
        authority_epoch: int = 0,
    ) -> dict[str, Any]:
        import base64

        body = {
            "op": "submit",
            "effect_key": effect_key,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
            "attempt_id": attempt_id,
            "fence": bool(fence),
            "expected_world_version": expected_world_version,
            "expected_payload_hash": expected_payload_hash,
            "expected_authority": expected_authority,
            "authority_token": authority_token,
            "authority_epoch": authority_epoch,
        }
        return self._rpc(body)

    def query(self, effect_key: str) -> dict[str, Any]:
        return self._rpc({"op": "query", "effect_key": effect_key})

    def submit_discard_response(
        self,
        *,
        effect_key: str,
        payload: bytes,
        attempt_id: str,
        fence: bool = False,
        expected_world_version: int | None = None,
        expected_payload_hash: str | None = None,
        expected_authority: dict[str, Any] | None = None,
        authority_token: int = 0,
        authority_epoch: int = 0,
    ) -> dict[str, Any]:
        """Send a submit and discard the response bytes. This is a lost acknowledgement, not a crash."""
        import base64

        body = {
            "op": "submit",
            "effect_key": effect_key,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
            "attempt_id": attempt_id,
            "fence": bool(fence),
            "expected_world_version": expected_world_version,
            "expected_payload_hash": expected_payload_hash,
            "expected_authority": expected_authority,
            "authority_token": authority_token,
            "authority_epoch": authority_epoch,
        }
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall((json.dumps(body) + "\n").encode("utf-8"))
                data = b""
                while b"\n" not in data:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
        except (OSError, TimeoutError):
            return {"status": "UNKNOWN", "applied": False, "outcome": "UNKNOWN", "reason": "transport"}
        if not data:
            return {"status": "UNKNOWN", "applied": False, "outcome": "UNKNOWN", "reason": "no_response"}
        return {"status": "ACK_NOT_OBSERVED", "outcome": "ACK_NOT_OBSERVED", "applied": False}

    def _rpc(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall((json.dumps(body) + "\n").encode("utf-8"))
                data = b""
                while b"\n" not in data:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
        except (OSError, TimeoutError):
            return {"status": "UNKNOWN", "applied": False, "outcome": "UNKNOWN", "reason": "transport"}
        if not data:
            return {"status": "UNKNOWN", "applied": False, "outcome": "UNKNOWN", "reason": "no_response"}
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            return {"status": "UNKNOWN", "applied": False, "outcome": "UNKNOWN", "reason": "bad_response"}


class ControlReceiverClient(PathReceiverClient):
    """Experiment controller. Not used by Claim or Outbox."""

    def set_world_version(self, version: int) -> dict[str, Any]:
        return self._rpc({"op": "set_world_version", "version": int(version)})

    def set_authority(self, *, token: int, epoch: int, eligible: bool) -> dict[str, Any]:
        return self._rpc(
            {
                "op": "set_authority",
                "token": int(token),
                "epoch": int(epoch),
                "eligible": bool(eligible),
            }
        )

    def audit(self) -> dict[str, Any]:
        return self._rpc({"op": "audit"})

    def reset_effects(self) -> dict[str, Any]:
        return self._rpc({"op": "reset_effects"})

    def shutdown(self) -> dict[str, Any]:
        return self._rpc({"op": "shutdown"})
