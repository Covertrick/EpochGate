"""按 source_id、source_version 和采样时间保存观测。"""

from __future__ import annotations

import time

from epochgate.ids import IdFactory
from epochgate.models import Observation


class ObservationStore:
    """只追加的观测记忆，可以绑定到任务。"""

    def __init__(self, *, ids: IdFactory | None = None) -> None:
        self._by_id: dict[str, Observation] = {}
        self._by_source: dict[str, list[str]] = {}
        self._task_bindings: dict[str, list[str]] = {}
        self._ids = ids or IdFactory(None)

    def record(self, observation: Observation, *, task_id: str | None = None) -> Observation:
        """保存一条观测。

        输入：观测；可选的任务绑定。
        输出：存入的观测。缺 id 或接收时间时补上。
        副作用：只追加，不改已有 payload。
        失败：``source_id`` 为空时抛出 ``ValueError``。
        """
        if not observation.source_id:
            raise ValueError("source_id is required")
        obs_id = observation.observation_id or self._ids("obs")
        received = observation.received_time_mono or time.monotonic()
        stored = observation.model_copy(
            update={
                "observation_id": obs_id,
                "received_time_mono": received,
            }
        )
        self._by_id[obs_id] = stored
        self._by_source.setdefault(stored.source_id, []).append(obs_id)
        if task_id is not None:
            self._task_bindings.setdefault(task_id, []).append(obs_id)
        return stored

    def get_by_source(
        self,
        source_id: str,
        *,
        source_version: str | None = None,
    ) -> list[Observation]:
        """列出某来源的观测，可按版本过滤。

        输入：来源 id，可选版本。
        输出：按采样时间升序的列表；没有则为空。
        副作用：无。
        失败：无。
        """
        ids = self._by_source.get(source_id, [])
        items = [self._by_id[obs_id] for obs_id in ids]
        if source_version is not None:
            items = [item for item in items if item.source_version == source_version]
        return sorted(items, key=lambda item: item.sample_time_mono)

    def latest_for_task(self, task_id: str) -> list[Observation]:
        """每个来源取该任务最新的一条观测。

        输入：任务 id。
        输出：每个 source_id 至多一条；未绑定时为空。
        副作用：无。
        失败：无。
        """
        ids = self._task_bindings.get(task_id, [])
        if not ids:
            return []
        latest: dict[str, Observation] = {}
        for obs_id in ids:
            obs = self._by_id[obs_id]
            prev = latest.get(obs.source_id)
            if prev is None or obs.sample_time_mono >= prev.sample_time_mono:
                latest[obs.source_id] = obs
        return list(latest.values())

    def get(self, observation_id: str) -> Observation:
        """按 id 取观测。

        输入：观测 id。
        输出：观测。
        副作用：无。
        失败：未知 id 抛出 ``KeyError``。
        """
        if observation_id not in self._by_id:
            raise KeyError(f"unknown observation: {observation_id}")
        return self._by_id[observation_id]

    def bind_to_task(self, task_id: str, observation_id: str) -> None:
        """把已有观测绑到任务上。

        输入：任务 id 和观测 id。
        输出：无。
        副作用：增加绑定。
        失败：未知观测抛出 ``KeyError``。
        """
        if observation_id not in self._by_id:
            raise KeyError(f"unknown observation: {observation_id}")
        self._task_bindings.setdefault(task_id, []).append(observation_id)
