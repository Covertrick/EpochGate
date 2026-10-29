"""短 id 生成。传入 seed 时序列可重复，便于同种子重放。"""

from __future__ import annotations

import uuid


class IdFactory:
    """按前缀发号。

    输入：可选 seed。seed 为 None 时每次取随机十六进制。
    输出：可调用对象，``ids("att")`` 得到 id 字符串。
    副作用：有 seed 时内部计数加一。
    失败：无。
    """

    def __init__(self, seed: int | None = None) -> None:
        self._n = 0
        self._seeded = seed is not None

    def __call__(self, prefix: str) -> str:
        """返回 ``prefix-序号`` 或 ``prefix-随机串``。

        输入：前缀。
        输出：id 字符串。
        副作用：有 seed 时计数加一。
        失败：无。
        """
        if not self._seeded:
            return f"{prefix}-{uuid.uuid4().hex[:12]}"
        self._n += 1
        return f"{prefix}-{self._n:04d}"
