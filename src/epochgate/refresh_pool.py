"""刷新池：按 merge_key 去重，并限制并发和每任务次数。"""

from __future__ import annotations

import asyncio

from epochgate.environment import EnvironmentAdapter
from epochgate.models import ObservationBatch, RefreshRecipe


class RefreshNotAllowed(RuntimeError):
    """读工具不在环境目录的 ``role=read`` 名单里。不调用 ``observe``。"""


class RefreshPool:
    """只调用环境的 observe。不做业务写入。"""

    def __init__(self, *, max_concurrent: int = 4, per_task_budget: int = 8) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        if per_task_budget < 1:
            raise ValueError("per_task_budget must be >= 1")
        self._sem = asyncio.Semaphore(max_concurrent)
        self._budget = per_task_budget
        self._used: dict[str, int] = {}
        self._inflight: dict[str, asyncio.Task[ObservationBatch]] = {}

    def budget(self) -> int:
        """每任务刷新上限。

        输入：无。
        输出：上限整数。
        副作用：无。
        失败：无。
        """
        return self._budget

    def used(self, task_id: str) -> int:
        """该任务已经消耗的刷新次数。

        输入：任务 id。
        输出：次数。
        副作用：无。
        失败：无。
        """
        return self._used.get(task_id, 0)

    async def observe(
        self,
        task_id: str,
        recipe: RefreshRecipe,
        environment: EnvironmentAdapter,
    ) -> ObservationBatch:
        """执行或复用一次只读刷新。

        输入：任务 id、配方、环境。
        输出：观测批次。相同 merge_key 的在途刷新会被等待方复用。
        副作用：只有真正调用 ``environment.observe`` 的那次增加计数。
        失败：读工具不在目录里时抛出 ``RefreshNotAllowed``。超出每任务上限时抛出 ``RuntimeError``。
        """
        key = recipe.merge_key or recipe.read_tool
        existing = self._inflight.get(key)
        if existing is not None:
            return await existing
        if not _read_allowed(environment, recipe):
            raise RefreshNotAllowed("read tool not allowed")
        if self.used(task_id) >= self._budget:
            raise RuntimeError(f"refresh budget exhausted for {task_id}")
        self._used[task_id] = self.used(task_id) + 1

        async def _run() -> ObservationBatch:
            async with self._sem:
                return await environment.observe(recipe)

        task: asyncio.Task[ObservationBatch] = asyncio.create_task(_run())
        self._inflight[key] = task
        try:
            return await task
        finally:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)


def _read_allowed(environment: EnvironmentAdapter, recipe: RefreshRecipe) -> bool:
    return any(
        spec.role == "read" and spec.name == recipe.read_tool for spec in environment.tool_catalog()
    )
