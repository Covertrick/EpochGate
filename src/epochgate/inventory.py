"""带种子的外生库存。推理期间世界继续按时间表变化。"""

from __future__ import annotations

import random
import time
from typing import Any, Mapping

from epochgate.ids import IdFactory
from epochgate.models import (
    CandidateAction,
    ChangeBatch,
    ChangeEvent,
    CommitResult,
    Observation,
    ObservationBatch,
    RefreshRecipe,
    ToolSpec,
    ValidationResult,
)


def plan_exogenous(seed: int, n: int = 3) -> list[tuple[float, int]]:
    """由种子生成外生库存变化。

    输入：种子和条数。
    输出：``(相对起点的秒数, 库存增量)`` 列表。同一种子结果相同。
    副作用：无。
    失败：n 小于 0 时抛出 ``ValueError``。
    """
    if n < 0:
        raise ValueError("n must be >= 0")
    rng = random.Random(seed)
    planned: list[tuple[float, int]] = []
    for _ in range(n):
        offset = round(rng.uniform(0.05, 1.0), 5)
        delta = rng.choice([-1, 1])
        planned.append((offset, delta))
    return planned


class InventoryEnvironment:
    """进程内库存。observe 只读；commit 按来源版本条件扣减。"""

    def __init__(
        self,
        *,
        stock: int = 5,
        seed: int | None = None,
        schedule: list[tuple[float, int]] | None = None,
        source_id: str = "inventory",
        read_tool: str = "read_inventory",
        effect_tool: str = "reserve",
        ids: IdFactory | None = None,
        start_mono: float | None = None,
        pace: str = "time",
    ) -> None:
        self.source_id = source_id
        self._read_tool = read_tool
        self._effect_tool = effect_tool
        self._stock = stock
        self._version = 1
        self._ids = ids or IdFactory(None)
        self._t0 = time.monotonic() if start_mono is None else start_mono
        if schedule is None:
            schedule = plan_exogenous(0 if seed is None else seed)
        self._plan = list(schedule)
        self._schedule = list(schedule)
        if pace not in {"time", "step"}:
            raise ValueError("pace must be time or step")
        self._pace = pace
        self._step_polls = 0
        self.observe_calls = 0
        self._queued: list[ChangeEvent] = []
        self._emitted = 0
        self._accepted: dict[str, CommitResult] = {}

    @property
    def stock(self) -> int:
        """当前库存。"""
        return self._stock

    @property
    def version(self) -> str:
        """当前来源版本。"""
        return f"v{self._version}"

    def planned_offsets(self) -> list[tuple[float, int]]:
        """返回构造时的外生计划，不受已经发生的事件影响。

        输入：无。
        输出：计划副本。
        副作用：无。
        失败：无。
        """
        return list(self._plan)

    def tool_catalog(self) -> list[ToolSpec]:
        """读工具与写工具。名字来自构造参数。副作用：无。"""
        return [
            ToolSpec(
                name=self._read_tool,
                role="read",
                description="Read current stock",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name=self._effect_tool,
                role="effect",
                description="Reserve units of stock",
                parameters={
                    "type": "object",
                    "properties": {"qty": {"type": "integer"}},
                    "required": ["qty"],
                },
            ),
        ]

    def default_refresh(self) -> RefreshRecipe:
        """默认只读配方。副作用：无。"""
        return RefreshRecipe(
            read_tool=self._read_tool,
            sources=[self.source_id],
            merge_key=self.source_id,
        )

    def apply_exogenous(self, delta: int) -> ChangeEvent:
        """立刻改库存并排队一条变化，供下一次 ``changes`` 取走。

        输入：库存增量，可以为负。
        输出：已入队的 ``ChangeEvent``。
        副作用：改库存和版本。
        失败：无。库存不会低于 0。
        """
        event = self._mutate(delta, kind="exogenous")
        self._queued.append(event)
        return event

    def notify(self, source_id: str, source_version: str | None = None) -> ChangeEvent:
        """排队一条不改库存、也不进入默认刷新来源的通知。

        输入：来源 id 和可选版本。版本为空时只把来源标脏。
        输出：已入队的变化。
        副作用：下一次 ``changes`` 会交出这条事件。
        失败：无。
        """
        self._emitted_seq = getattr(self, "_emitted_seq", 0) + 1
        event = ChangeEvent(
            cursor=str(self._emitted_seq),
            source_id=source_id,
            source_version=source_version,
            mono_time=time.monotonic(),
            kind="external",
        )
        self._queued.append(event)
        return event

    async def observe(self, recipe: RefreshRecipe) -> ObservationBatch:
        """只读当前库存。

        输入：配方。
        输出：一条观测。
        副作用：不改库存。
        失败：读工具名不匹配时抛出 ``ValueError``。
        """
        if recipe.read_tool != self._read_tool:
            raise ValueError(f"unknown read_tool: {recipe.read_tool}")
        self.observe_calls += 1
        now = time.monotonic()
        obs = Observation(
            observation_id=self._ids("obs"),
            source_id=self.source_id,
            source_version=self.version,
            sample_time_mono=now,
            received_time_mono=now,
            payload={"stock": self._stock},
        )
        return ObservationBatch(observations=[obs])

    async def changes(self, cursor: str) -> ChangeBatch:
        """应用已到期的计划事件，并返回排队中的变化。

        输入：游标。
        输出：本页事件和下一个游标。
        副作用：到期事件会改库存和版本。
        失败：无。
        """
        now = time.monotonic()
        if self._pace == "step":
            self._step_polls += 1
            if self._step_polls > 1 and self._schedule:
                _offset, delta = self._schedule.pop(0)
                self._queued.append(self._mutate(delta, kind="scheduled"))
        else:
            pending: list[tuple[float, int]] = []
            for offset, delta in self._schedule:
                if self._t0 + offset <= now:
                    self._queued.append(self._mutate(delta, kind="scheduled"))
                else:
                    pending.append((offset, delta))
            self._schedule = pending
        events = list(self._queued)
        self._queued.clear()
        self._emitted += len(events)
        return ChangeBatch(events=events, next_cursor=str(self._emitted))

    async def check(self, candidate: CandidateAction) -> ValidationResult:
        """写工具且库存足够时通过。

        输入：候选动作。
        输出：检查结果。
        副作用：无。
        失败：无。
        """
        if candidate.action_type != self._effect_tool:
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
        """版本匹配时扣减库存。

        输入：候选与网关条件。
        输出：提交结果。
        副作用：接受时减少库存并推进版本。同一 action_id 再次提交返回原结果，不再扣库存。
        失败：版本冲突或库存不足时拒绝。
        """
        cached = self._accepted.get(candidate.action_id)
        if cached is not None:
            return cached
        expected_version = conditions.get("source_version")
        if expected_version is not None and expected_version != self.version:
            return CommitResult(accepted=False, reasons=["version conflict"])
        checked = await self.check(candidate)
        if not checked.ok:
            return CommitResult(accepted=False, reasons=list(checked.reasons))
        qty = int(candidate.parameters.get("qty", 1))
        self._stock -= qty
        self._version += 1
        result = CommitResult(accepted=True, commit_id=self._ids("cmt"))
        self._accepted[candidate.action_id] = result
        return result

    def _mutate(self, delta: int, *, kind: str) -> ChangeEvent:
        self._stock = max(0, self._stock + delta)
        self._version += 1
        self._emitted_seq = getattr(self, "_emitted_seq", 0) + 1
        return ChangeEvent(
            cursor=str(self._emitted_seq),
            source_id=self.source_id,
            source_version=self.version,
            mono_time=time.monotonic(),
            kind=kind,
        )
