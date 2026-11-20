"""Decoy. Neither Claim nor Outbox may call this."""

from __future__ import annotations


def private_transition(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("shared private transition was called")
