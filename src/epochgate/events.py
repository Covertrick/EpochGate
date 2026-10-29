"""只追加的事件日志。可选 SQLite 建表双写，并可以导出 JSONL。"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

from epochgate.models import TraceEvent


class EventLog:
    """内存日志。给出路径时，每次追加同步写入 SQLite。"""

    def __init__(self, *, sqlite_path: str | Path | None = None) -> None:
        self._events: list[TraceEvent] = []
        self._seq = 0
        self._conn: sqlite3.Connection | None = None
        if sqlite_path is not None:
            self._conn = sqlite3.connect(str(sqlite_path))
            self._init_schema(self._conn)

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        """建最小追加表。没有二级索引，也不在这里查询。"""
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
        """追加一条事件。

        输入：``TraceEvent``。seq 为 0 时由日志分配。
        输出：带单调序号的已存事件。
        副作用：内存列表变长；可选 SQLite INSERT。
        失败：SQLite 错误向上抛。
        """
        self._seq += 1
        stored = event.model_copy(update={"seq": self._seq, "mono_time": event.mono_time or time.monotonic()})
        if stored.mono_time <= 0:
            stored = stored.model_copy(update={"mono_time": time.monotonic()})
        self._events.append(stored)
        if self._conn is not None:
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
        """用当前 monotonic 时间构造并追加一条事件。

        输入：运行 id、类型、身份、原因、用量。
        输出：已存事件。
        副作用：同 ``append``。
        失败：同 ``append``。
        """
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
        """按写入顺序遍历。

        输入：可选 run_id。
        输出：事件迭代器。不匹配时为空。
        副作用：无。
        失败：无。
        """
        for event in self._events:
            if run_id is None or event.run_id == run_id:
                yield event

    def export_jsonl(self, path: str | Path, *, run_id: str | None = None) -> int:
        """把事件写成 JSONL，一行一条。

        输入：输出路径；可选 run_id 过滤。
        输出：写入的行数。
        副作用：覆盖该文件。
        失败：文件系统错误向上抛。
        """
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with destination.open("w", encoding="utf-8") as handle:
            for event in self.iter_events(run_id=run_id):
                handle.write(event.model_dump_json() + "\n")
                count += 1
        return count

    def close(self) -> None:
        """关闭可选的 SQLite 连接。

        输入：无。
        输出：无。
        副作用：关闭数据库句柄。
        失败：无。
        """
        if self._conn is not None:
            self._conn.close()
            self._conn = None
