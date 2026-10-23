"""Append-only event log with an optional SQLite sink (schema only, no queries)."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

from epochgate.models import TraceEvent


class EventLog:
    """In-memory append-only log. Optional SQLite sink mirrors each append."""

    def __init__(self, *, sqlite_path: str | Path | None = None) -> None:
        self._events: list[TraceEvent] = []
        self._seq = 0
        self._conn: sqlite3.Connection | None = None
        if sqlite_path is not None:
            self._conn = sqlite3.connect(str(sqlite_path))
            self._init_schema(self._conn)

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        """Create the minimal append table. No secondary indexes or queries."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_events (
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                mono_time REAL NOT NULL,
                event_type TEXT NOT NULL,
                identity_json TEXT NOT NULL,
                reason TEXT,
                usage_json TEXT NOT NULL,
                PRIMARY KEY (run_id, seq)
            )
            """
        )
        conn.commit()

    def append(self, event: TraceEvent) -> TraceEvent:
        """Append one event.

        Inputs: TraceEvent (seq may be 0; assigned if so).
        Outputs: stored TraceEvent with monotonic seq.
        Side effects: memory list grows; optional SQLite INSERT.
        Failures: sqlite errors propagate.
        """
        self._seq += 1
        stored = event.model_copy(update={"seq": self._seq, "mono_time": event.mono_time or time.monotonic()})
        if stored.mono_time <= 0:
            stored = stored.model_copy(update={"mono_time": time.monotonic()})
        self._events.append(stored)
        if self._conn is not None:
            import json

            self._conn.execute(
                """
                INSERT INTO trace_events
                (run_id, seq, mono_time, event_type, identity_json, reason, usage_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.run_id,
                    stored.seq,
                    stored.mono_time,
                    stored.event_type,
                    json.dumps(dict(stored.identity)),
                    stored.reason,
                    json.dumps(dict(stored.usage)),
                ),
            )
            self._conn.commit()
        return stored

    def emit(
        self,
        *,
        run_id: str,
        event_type: str,
        identity: dict[str, str] | None = None,
        reason: str | None = None,
        usage: dict | None = None,
    ) -> TraceEvent:
        """Convenience constructor using ``time.monotonic()``."""
        return self.append(
            TraceEvent(
                run_id=run_id,
                seq=0,
                mono_time=time.monotonic(),
                event_type=event_type,
                identity=identity or {},
                reason=reason,
                usage=usage or {},
            )
        )

    def iter_events(self, *, run_id: str | None = None) -> Iterator[TraceEvent]:
        """Iterate stored events. Side effects: none. Missing run_id → empty."""
        for event in self._events:
            if run_id is None or event.run_id == run_id:
                yield event

    def close(self) -> None:
        """Close optional SQLite connection. Side effects: closes DB handle."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
