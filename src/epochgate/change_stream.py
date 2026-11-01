"""变化流故障。和模型 HTTP 故障分开，包在环境的 ``changes`` 外面。"""

from __future__ import annotations

import random
from typing import Any

from epochgate.models import ChangeBatch


class ChangeStream:
    """把 ``drop``、``reorder``、``disconnect`` 施加到环境变化上。"""

    def __init__(self, inner: Any, *, mode: str | None = None, seed: int = 0) -> None:
        if mode not in {None, "drop", "reorder", "disconnect"}:
            raise ValueError("change stream mode must be drop, reorder, disconnect, or empty")
        self._inner = inner
        self.mode = mode
        self._rng = random.Random(seed)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def changes(self, cursor: str) -> ChangeBatch:
        """读取一页变化，再按模式丢掉、打乱或断开。

        输入：游标。
        输出：可能被改过的 ``ChangeBatch``。断开或整页丢弃时 ``stream_open`` 为假。
        副作用：推进自己的随机序列；内部环境的副作用保持不变。
        失败：模式无法识别时在构造阶段抛出 ``ValueError``。
        """
        batch = await self._inner.changes(cursor)
        if self.mode is None:
            return batch
        if self.mode == "disconnect":
            return ChangeBatch(events=[], next_cursor=batch.next_cursor, stream_open=False)
        if self.mode == "reorder":
            events = list(batch.events)
            self._rng.shuffle(events)
            return ChangeBatch(events=events, next_cursor=batch.next_cursor, stream_open=True)
        if batch.events:
            return ChangeBatch(events=[], next_cursor=batch.next_cursor, stream_open=False)
        return batch
