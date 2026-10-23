"""Environment adapter protocol and an in-process fake for demos/tests."""

from __future__ import annotations

import time
import uuid
from typing import Any, Mapping, Protocol, runtime_checkable

from epochgate.models import (
    CandidateAction,
    CommitResult,
    Observation,
    ObservationBatch,
    RefreshRecipe,
    ValidationResult,
)


@runtime_checkable
class EnvironmentAdapter(Protocol):
    """Boundary to a dynamic environment. Only ActionGateway may call ``commit``."""

    async def observe(self, recipe: RefreshRecipe) -> ObservationBatch:
        """Read-only observation.

        Inputs: RefreshRecipe (must be read semantics).
        Outputs: ObservationBatch with versions and sample times.
        Side effects: none on business state.
        Failures: unknown tool → ``ValueError``.
        """
        ...

    async def check(self, candidate: CandidateAction) -> ValidationResult:
        """Pre-commit predicate check (may be non-atomic w.r.t. commit).

        Inputs: CandidateAction.
        Outputs: ValidationResult (ok / reasons). Side effects: none.
        """
        ...

    async def commit(
        self,
        candidate: CandidateAction,
        conditions: Mapping[str, Any],
    ) -> CommitResult:
        """Conditional commit. Must only be invoked by ActionGateway.

        Inputs: candidate + gateway-assembled conditions.
        Outputs: CommitResult.
        Side effects: mutates environment if accepted.
        Failures: conflicts return rejected result; internal errors propagate.
        """
        ...


class FakeEnvironment:
    """Minimal mutable inventory used by the happy-path demo."""

    def __init__(self, *, stock: int = 3, source_id: str = "inventory") -> None:
        self.source_id = source_id
        self._stock = stock
        self._version = 1
        self._commits: list[str] = []

    @property
    def stock(self) -> int:
        return self._stock

    @property
    def version(self) -> str:
        return f"v{self._version}"

    async def observe(self, recipe: RefreshRecipe) -> ObservationBatch:
        """Return current stock snapshot. Failures: unknown ``read_tool``."""
        if recipe.read_tool != "read_inventory":
            raise ValueError(f"unknown read_tool: {recipe.read_tool}")
        now = time.monotonic()
        obs = Observation(
            observation_id=f"obs-{uuid.uuid4().hex[:12]}",
            source_id=self.source_id,
            source_version=self.version,
            sample_time_mono=now,
            received_time_mono=now,
            payload={"stock": self._stock},
        )
        return ObservationBatch(observations=[obs])

    async def check(self, candidate: CandidateAction) -> ValidationResult:
        """Accept reserve actions when stock is positive."""
        if candidate.action_type != "reserve":
            return ValidationResult(ok=False, reasons=[f"unsupported action: {candidate.action_type}"])
        qty = int(candidate.parameters.get("qty", 1))
        if self._stock < qty:
            return ValidationResult(ok=False, reasons=["insufficient stock"])
        return ValidationResult(ok=True)

    async def commit(
        self,
        candidate: CandidateAction,
        conditions: Mapping[str, Any],
    ) -> CommitResult:
        """Decrement stock if check-equivalent conditions still hold."""
        qty = int(candidate.parameters.get("qty", 1))
        expected_version = conditions.get("source_version")
        if expected_version is not None and expected_version != self.version:
            return CommitResult(accepted=False, reasons=["version conflict"])
        if self._stock < qty:
            return CommitResult(accepted=False, reasons=["insufficient stock"])
        self._stock -= qty
        self._version += 1
        commit_id = f"cmt-{uuid.uuid4().hex[:12]}"
        self._commits.append(commit_id)
        return CommitResult(accepted=True, commit_id=commit_id)
