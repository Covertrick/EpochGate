"""P1.1：独立、定时、可重放的外生事件驱动器（ScenarioDriver）。

只负责按固定相对时间推进环境状态（如 t_change: 1->0，t_restore: 0->1），
不判断 A/B 臂，不依赖某个 attempt 是否已完成或被取消。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from epochgate.inventory import InventoryEnvironment


@dataclass(frozen=True)
class ExogenousCue:
    """相对于锚点（first_delta 或 run_start）的外生库存变化事件。"""

    offset_s: float
    delta: int
    label: str = "exogenous"


@dataclass(frozen=True)
class ScenarioSpec:
    """可重放场景定义：A/B 配对运行各自构造独立环境副本，重放同一 cues。"""

    scenario_id: str
    task_kind: str  # "static" | "invalidate" | "recoverable"
    initial_stock: int = 1
    cues: tuple[ExogenousCue, ...] = ()
    anchor: str = "first_delta"  # "first_delta" | "run_start"


@dataclass
class CueRecord:
    """单条外生事件的实际触发记录。"""

    label: str
    offset_s: float
    delta: int
    scheduled_at_mono: float
    applied_at_mono: float
    source_version: str
    stock_after: int


@dataclass
class ScenarioDriver:
    """驱动一个独立 ``InventoryEnvironment`` 按 ``ScenarioSpec`` 的相对时间触发外生变化。"""

    spec: ScenarioSpec
    env: InventoryEnvironment
    anchor_mono: float | None = None
    history: list[CueRecord] = field(default_factory=list)
    _started: bool = False
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    def start_anchor(self) -> None:
        """在锚点时刻启动所有定时外生事件（幂等，只启动一次）。"""
        if self._started:
            return
        self._started = True
        self.anchor_mono = time.monotonic()
        anchor = self.anchor_mono
        for cue in sorted(self.spec.cues, key=lambda item: item.offset_s):
            self._tasks.append(asyncio.create_task(self._fire_after(anchor, cue)))

    async def _fire_after(self, anchor: float, cue: ExogenousCue) -> None:
        target = anchor + max(0.0, cue.offset_s)
        now = time.monotonic()
        if target > now:
            await asyncio.sleep(target - now)
        if cue.label == "harmless":
            event = self.env.notify("annotation")
        else:
            event = self.env.apply_exogenous(cue.delta)
        self.history.append(
            CueRecord(
                label=cue.label,
                offset_s=cue.offset_s,
                delta=cue.delta,
                scheduled_at_mono=anchor,
                applied_at_mono=event.mono_time,
                source_version=event.source_version or "",
                stock_after=self.env.stock,
            )
        )

    async def wait_all(self, timeout_s: float = 10.0) -> None:
        """等待所有已排期的外生事件执行完毕。"""
        if not self._tasks:
            return
        await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=timeout_s)

    def cancel_pending(self) -> None:
        """清理尚未触发的后台任务。"""
        for task in self._tasks:
            if not task.done():
                task.cancel()

    def first_change_applied_at(self) -> float | None:
        for rec in self.history:
            if rec.delta < 0 or rec.label in {"invalidate", "t_change", "harmless"}:
                return rec.applied_at_mono
        return self.history[0].applied_at_mono if self.history else None

    def restore_applied_at(self) -> float | None:
        for rec in self.history:
            if rec.delta > 0 or rec.label in {"restore", "t_restore"}:
                return rec.applied_at_mono
        return None

    def summary(self) -> list[dict[str, Any]]:
        return [
            {
                "label": rec.label,
                "offset_s": rec.offset_s,
                "delta": rec.delta,
                "scheduled_at_mono": rec.scheduled_at_mono,
                "applied_at_mono": rec.applied_at_mono,
                "actual_offset_s": rec.applied_at_mono - rec.scheduled_at_mono,
                "source_version": rec.source_version,
                "stock_after": rec.stock_after,
            }
            for rec in self.history
        ]


def make_scenario_spec(
    *,
    scenario_id: str,
    task_kind: str,
    t_change_s: float = 0.5,
    t_restore_s: float | None = None,
    initial_stock: int = 1,
    restore_delta: int = 1,
) -> ScenarioSpec:
    """构造标准三类场景（static / invalidate / recoverable）。"""
    if task_kind == "static":
        return ScenarioSpec(
            scenario_id=scenario_id,
            task_kind="static",
            initial_stock=initial_stock,
            cues=(),
        )
    if task_kind == "harmless":
        return ScenarioSpec(
            scenario_id=scenario_id,
            task_kind="harmless",
            initial_stock=initial_stock,
            cues=(ExogenousCue(offset_s=t_change_s, delta=0, label="harmless"),),
        )
    if task_kind == "invalidate":
        return ScenarioSpec(
            scenario_id=scenario_id,
            task_kind="invalidate",
            initial_stock=initial_stock,
            cues=(ExogenousCue(offset_s=t_change_s, delta=-initial_stock, label="t_change"),),
        )
    if task_kind == "recoverable":
        restore_at = t_restore_s if t_restore_s is not None else (t_change_s + 1.0)
        if restore_at <= t_change_s:
            raise ValueError("t_restore_s must be > t_change_s")
        return ScenarioSpec(
            scenario_id=scenario_id,
            task_kind="recoverable",
            initial_stock=initial_stock,
            cues=(
                ExogenousCue(offset_s=t_change_s, delta=-initial_stock, label="t_change"),
                ExogenousCue(offset_s=restore_at, delta=restore_delta, label="t_restore"),
            ),
        )
    raise ValueError(f"unknown task_kind: {task_kind}")
