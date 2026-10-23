"""Observation store keyed by source_id / source_version / sample_time."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict

from epochgate.models import Observation


class ObservationStore:
    """Append-only observation memory with optional task bindings."""

    def __init__(self) -> None:
        self._by_id: dict[str, Observation] = {}
        self._by_source: dict[str, list[str]] = defaultdict(list)
        self._task_bindings: dict[str, list[str]] = defaultdict(list)

    def record(self, observation: Observation, *, task_id: str | None = None) -> Observation:
        """Store one observation.

        Inputs: Observation; optional ``task_id`` binding.
        Outputs: stored Observation (fills id / received_time if missing).
        Side effects: append-only write; never mutates prior payloads.
        Failures: empty ``source_id`` → ``ValueError``.
        """
        if not observation.source_id:
            raise ValueError("source_id is required")
        obs_id = observation.observation_id or f"obs-{uuid.uuid4().hex[:12]}"
        received = observation.received_time_mono or time.monotonic()
        stored = observation.model_copy(
            update={
                "observation_id": obs_id,
                "received_time_mono": received,
            }
        )
        self._by_id[obs_id] = stored
        self._by_source[stored.source_id].append(obs_id)
        if task_id is not None:
            self._task_bindings[task_id].append(obs_id)
        return stored

    def get_by_source(
        self,
        source_id: str,
        *,
        source_version: str | None = None,
    ) -> list[Observation]:
        """List observations for a source, optionally filtered by version.

        Inputs: ``source_id``, optional ``source_version``.
        Outputs: list sorted by ``sample_time_mono`` ascending (empty if none).
        Side effects: none.
        """
        ids = self._by_source.get(source_id, [])
        items = [self._by_id[i] for i in ids]
        if source_version is not None:
            items = [o for o in items if o.source_version == source_version]
        return sorted(items, key=lambda o: o.sample_time_mono)

    def latest_for_task(self, task_id: str) -> list[Observation]:
        """Latest observation per ``source_id`` bound to a task.

        Inputs: ``task_id``.
        Outputs: at most one Observation per source (empty if unbound).
        Side effects: none.
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
        """Lookup by id. Failures: ``KeyError``."""
        if observation_id not in self._by_id:
            raise KeyError(f"unknown observation: {observation_id}")
        return self._by_id[observation_id]

    def bind_to_task(self, task_id: str, observation_id: str) -> None:
        """Attach an existing observation to a task. Failures: ``KeyError``."""
        if observation_id not in self._by_id:
            raise KeyError(f"unknown observation: {observation_id}")
        self._task_bindings[task_id].append(observation_id)
