"""SQLite 库存。默认提交是一条条件更新；窗口参数只用于显式的检查-写入竞态。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping

import aiosqlite

from epochgate.ids import IdFactory
from epochgate.models import CandidateAction, CommitResult, ValidationResult


def write_records_sync(path: str, worker: int, count: int = 25) -> None:
    """在一个新进程里追加记录。供多进程测试调用。

    输入：数据库路径、写入者编号、条数。
    输出：无。
    副作用：向已建好的表插入 ``count`` 行。
    失败：数据库不可写时向上抛。
    """

    async def _write() -> None:
        store = SqliteInventory(path)
        for index in range(count):
            await store.append_record(f"p{worker}-{index}", f"p{worker}")

    asyncio.run(_write())


class InventoryClosed(RuntimeError):
    """库存已经关闭。再次写入必须失败，不能静默提交。"""


class SqliteInventory:
    """WAL 下的多连接库存。同一 action_id 只扣一次。"""

    def __init__(self, path: str | Path, *, stock: int = 5, ids: IdFactory | None = None) -> None:
        self.path = str(path)
        self._initial_stock = stock
        self._ids = ids or IdFactory(None)
        self._closed = False

    async def setup(self) -> None:
        """建表并写入初始库存。

        输入：无。
        输出：无。
        副作用：创建或打开 SQLite 文件，开启 WAL。
        失败：数据库无法打开时向上抛。
        """
        async with self._open() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS stock (
                    source_id TEXT PRIMARY KEY,
                    qty INTEGER NOT NULL,
                    version INTEGER NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS commits (
                    action_id TEXT PRIMARY KEY,
                    commit_id TEXT NOT NULL,
                    qty INTEGER NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    record_id TEXT PRIMARY KEY,
                    writer TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "INSERT OR IGNORE INTO stock (source_id, qty, version) VALUES ('inventory', ?, 1)",
                (self._initial_stock,),
            )
            await db.commit()

    async def close(self) -> None:
        """关闭库存。重复调用不报错。

        输入：无。
        输出：无。
        副作用：之后的写入都会被拒绝。
        失败：无。
        """
        self._closed = True

    @property
    def closed(self) -> bool:
        """是否已关闭。"""
        return self._closed

    async def read_stock(self) -> tuple[int, str]:
        """读取当前数量和版本。

        输入：无。
        输出：数量，以及 ``vN`` 形式的版本。
        副作用：无。
        失败：已关闭时抛出 ``InventoryClosed``。
        """
        self._ensure_open()
        async with self._open() as db:
            cursor = await db.execute("SELECT qty, version FROM stock WHERE source_id = 'inventory'")
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("stock row missing")
        return int(row[0]), f"v{int(row[1])}"

    async def append_record(self, record_id: str, writer: str) -> None:
        """追加一条不可重复的记录。

        输入：记录 id 和写入者。
        输出：无。
        副作用：插入一行。
        失败：已关闭时抛出 ``InventoryClosed``；主键冲突时向上抛。
        """
        self._ensure_open()
        async with self._open() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "INSERT INTO records (record_id, writer) VALUES (?, ?)",
                (record_id, writer),
            )
            await db.commit()

    async def record_stats(self) -> tuple[int, int]:
        """返回记录条数和去重条数。

        输入：无。
        输出：``(count, distinct count)``。
        副作用：无。
        失败：数据库不可读时向上抛。
        """
        async with self._open() as db:
            cursor = await db.execute("SELECT COUNT(*), COUNT(DISTINCT record_id) FROM records")
            row = await cursor.fetchone()
        if row is None:
            return 0, 0
        return int(row[0]), int(row[1])

    async def check(self, candidate: CandidateAction) -> ValidationResult:
        """只读检查库存是否够。不持有写入锁。

        输入：候选动作。
        输出：检查结果。
        副作用：无。
        失败：已关闭时抛出 ``InventoryClosed``。
        """
        self._ensure_open()
        if candidate.action_type != "reserve":
            return ValidationResult(ok=False, reasons=[f"unsupported action: {candidate.action_type}"])
        qty, _version = await self.read_stock()
        need = int(candidate.parameters.get("qty", 1))
        if qty < need:
            return ValidationResult(ok=False, reasons=["insufficient stock"])
        return ValidationResult(ok=True)

    async def commit_action(
        self,
        candidate: CandidateAction,
        conditions: Mapping[str, Any],
        *,
        window_s: float = 0.0,
    ) -> CommitResult:
        """条件扣减。``window_s > 0`` 时先读版本，放开锁，再写。

        输入：候选、网关条件、可选的显式竞态窗口。
        输出：提交结果。同一 ``action_id`` 返回原来的 ``commit_id``。
        副作用：接受时减少库存并推进版本。
        失败：已关闭时抛出 ``InventoryClosed``。版本冲突或库存不足时拒绝。
        """
        self._ensure_open()
        expected = conditions.get("source_version")
        if window_s > 0:
            _qty, current = await self.read_stock()
            if expected is not None and expected != current:
                return CommitResult(accepted=False, reasons=["version conflict"])
            await asyncio.sleep(window_s)
            expected = current if expected is None else expected
        return await self._commit_locked(candidate, expected)

    async def _commit_locked(self, candidate: CandidateAction, expected: object) -> CommitResult:
        self._ensure_open()
        need = int(candidate.parameters.get("qty", 1))
        async with self._open() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                known = await db.execute(
                    "SELECT commit_id FROM commits WHERE action_id = ?",
                    (candidate.action_id,),
                )
                existing = await known.fetchone()
                if existing is not None:
                    await db.commit()
                    return CommitResult(accepted=True, commit_id=str(existing[0]))
                if candidate.action_type != "reserve":
                    await db.rollback()
                    return CommitResult(accepted=False, reasons=[f"unsupported action: {candidate.action_type}"])
                stock = await db.execute("SELECT qty, version FROM stock WHERE source_id = 'inventory'")
                row = await stock.fetchone()
                if row is None:
                    await db.rollback()
                    return CommitResult(accepted=False, reasons=["stock missing"])
                qty, version = int(row[0]), int(row[1])
                if expected is not None and str(expected) != f"v{version}":
                    await db.rollback()
                    return CommitResult(accepted=False, reasons=["version conflict"])
                if qty < need:
                    await db.rollback()
                    return CommitResult(accepted=False, reasons=["insufficient stock"])
                cursor = await db.execute(
                    """
                    UPDATE stock
                    SET qty = qty - ?, version = version + 1
                    WHERE source_id = 'inventory' AND version = ? AND qty >= ?
                    """,
                    (need, version, need),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    return CommitResult(accepted=False, reasons=["version conflict"])
                commit_id = self._ids("cmt")
                await db.execute(
                    "INSERT INTO commits (action_id, commit_id, qty) VALUES (?, ?, ?)",
                    (candidate.action_id, commit_id, need),
                )
                await db.commit()
                return CommitResult(accepted=True, commit_id=commit_id)
            except InventoryClosed:
                await db.rollback()
                raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise InventoryClosed("inventory closed")

    @asynccontextmanager
    async def _open(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self.path, timeout=5)
        try:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")
            yield db
        finally:
            await db.close()
