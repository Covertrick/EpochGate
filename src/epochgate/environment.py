"""环境适配协议，以及演示用的进程内假库存。"""

from __future__ import annotations

import time
from typing import Any, Mapping, Protocol, runtime_checkable

from epochgate.ids import IdFactory
from epochgate.models import (
    CandidateAction,
    ChangeBatch,
    CommitResult,
    Observation,
    ObservationBatch,
    RefreshRecipe,
    ToolSpec,
    ValidationResult,
)


@runtime_checkable
class EnvironmentAdapter(Protocol):
    """动态环境边界。只有 ActionGateway 可以调用 ``commit``。"""

    def tool_catalog(self) -> list[ToolSpec]:
        """返回工具目录。

        输入：无。
        输出：读工具与写工具的 ``ToolSpec`` 列表。
        副作用：无。
        失败：无。
        """
        ...

    def default_refresh(self) -> RefreshRecipe:
        """返回默认只读配方。

        输入：无。
        输出：``RefreshRecipe``。
        副作用：无。
        失败：无。
        """
        ...

    async def observe(self, recipe: RefreshRecipe) -> ObservationBatch:
        """只读观察。

        输入：必须是读语义的 ``RefreshRecipe``。
        输出：带版本和采样时刻的 ``ObservationBatch``。
        副作用：不改业务状态。
        失败：未知工具抛出 ``ValueError``。
        """
        ...

    async def changes(self, cursor: str) -> ChangeBatch:
        """拉取自上次游标以来的变化。

        输入：上次返回的游标；空串表示从头。
        输出：``ChangeBatch``。
        副作用：消费已到期的外生事件，可能推进环境版本。
        失败：无。
        """
        ...

    async def check(self, candidate: CandidateAction) -> ValidationResult:
        """提交前检查。相对 commit 可以不是原子的。

        输入：``CandidateAction``。
        输出：``ValidationResult``。
        副作用：无。
        失败：无；不满足时 ``ok=False``。
        """
        ...

    async def commit(
        self,
        candidate: CandidateAction,
        conditions: Mapping[str, Any],
    ) -> CommitResult:
        """条件提交。只能由 ActionGateway 调用。

        输入：候选动作，以及网关组装的条件。
        输出：``CommitResult``。
        副作用：接受时修改环境。
        失败：冲突返回拒绝结果；内部错误向上抛。
        """
        ...


class FakeEnvironment:
    """演示用的可变库存。没有外生事件，``changes`` 恒为空。"""

    def __init__(self, *, stock: int = 3, source_id: str = "inventory", ids: IdFactory | None = None) -> None:
        self.source_id = source_id
        self._stock = stock
        self._version = 1
        self._commits: list[str] = []
        self._ids = ids or IdFactory(None)

    @property
    def stock(self) -> int:
        """当前库存。"""
        return self._stock

    @property
    def version(self) -> str:
        """当前来源版本。"""
        return f"v{self._version}"

    def tool_catalog(self) -> list[ToolSpec]:
        """库存读与预约写。副作用：无。"""
        return [
            ToolSpec(
                name="read_inventory",
                role="read",
                description="Read current stock",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="reserve",
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
        """默认读取库存。副作用：无。"""
        return RefreshRecipe(
            read_tool="read_inventory",
            sources=[self.source_id],
            merge_key=self.source_id,
        )

    async def observe(self, recipe: RefreshRecipe) -> ObservationBatch:
        """返回当前库存快照。

        输入：配方。
        输出：一条观测。
        副作用：无。
        失败：``read_tool`` 不是目录中的读工具时抛出 ``ValueError``。
        """
        read_names = {spec.name for spec in self.tool_catalog() if spec.role == "read"}
        if recipe.read_tool not in read_names:
            raise ValueError(f"unknown read_tool: {recipe.read_tool}")
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
        """假环境没有外生变化。

        输入：游标。
        输出：空批次，游标原样返回。
        副作用：无。
        失败：无。
        """
        return ChangeBatch(events=[], next_cursor=cursor or "0")

    async def check(self, candidate: CandidateAction) -> ValidationResult:
        """库存足够时接受目录中的写工具。

        输入：候选动作。
        输出：检查结果。
        副作用：无。
        失败：无。
        """
        effect_names = {spec.name for spec in self.tool_catalog() if spec.role == "effect"}
        if candidate.action_type not in effect_names:
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
        """版本仍匹配且库存足够时扣减。

        输入：候选与条件。
        输出：提交结果。
        副作用：接受时减少库存并推进版本。
        失败：版本冲突或库存不足时返回拒绝，不抛异常。
        """
        qty = int(candidate.parameters.get("qty", 1))
        expected_version = conditions.get("source_version")
        if expected_version is not None and expected_version != self.version:
            return CommitResult(accepted=False, reasons=["version conflict"])
        effect_names = {spec.name for spec in self.tool_catalog() if spec.role == "effect"}
        if candidate.action_type not in effect_names:
            return CommitResult(accepted=False, reasons=[f"unsupported action: {candidate.action_type}"])
        if self._stock < qty:
            return CommitResult(accepted=False, reasons=["insufficient stock"])
        self._stock -= qty
        self._version += 1
        commit_id = self._ids("cmt")
        self._commits.append(commit_id)
        return CommitResult(accepted=True, commit_id=commit_id)
