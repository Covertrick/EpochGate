"""多连接 SQLite：写入不丢、关闭后拒绝、并发扣减只发生一次。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from epochgate.env_app import create_app
from epochgate.http_environment import HttpInventoryEnvironment
from epochgate.models import CandidateAction
from epochgate.sqlite_inventory import InventoryClosed, SqliteInventory


def _action(action_id: str, *, qty: int = 1) -> CandidateAction:
    return CandidateAction(
        action_id=action_id,
        task_id="t",
        attempt_id="a",
        plan_epoch=0,
        obs_revision=0,
        action_type="reserve",
        parameters={"qty": qty},
    )


@pytest.mark.asyncio
async def test_concurrent_inserts_lose_nothing(tmp_path) -> None:
    store = SqliteInventory(tmp_path / "records.sqlite")
    await store.setup()

    async def write_batch(worker: int) -> None:
        for index in range(25):
            await store.append_record(f"w{worker}-{index}", f"w{worker}")

    await asyncio.gather(*(write_batch(worker) for worker in range(8)))
    count, distinct = await store.record_stats()
    assert count == 200
    assert distinct == 200


@pytest.mark.asyncio
async def test_close_is_idempotent_and_later_writes_fail(tmp_path) -> None:
    store = SqliteInventory(tmp_path / "closed.sqlite", stock=3)
    await store.setup()
    await store.close()
    await store.close()
    with pytest.raises(InventoryClosed):
        await store.append_record("after", "w")
    with pytest.raises(InventoryClosed):
        await store.commit_action(_action("a1"), {})


@pytest.mark.asyncio
async def test_concurrent_different_actions_do_not_oversell(tmp_path) -> None:
    store = SqliteInventory(tmp_path / "stock.sqlite", stock=1)
    await store.setup()
    _qty, version = await store.read_stock()
    results = await asyncio.gather(
        *(
            store.commit_action(_action(f"act-{index}"), {"source_version": version})
            for index in range(8)
        )
    )
    accepted = [result for result in results if result.accepted]
    assert len(accepted) == 1
    qty, _version = await store.read_stock()
    assert qty == 0


@pytest.mark.asyncio
async def test_concurrent_retries_of_one_action_debit_once(tmp_path) -> None:
    store = SqliteInventory(tmp_path / "idem.sqlite", stock=4)
    await store.setup()
    _qty, version = await store.read_stock()
    results = await asyncio.gather(
        *(store.commit_action(_action("same"), {"source_version": version}) for _ in range(20))
    )
    assert all(result.accepted for result in results)
    assert len({result.commit_id for result in results}) == 1
    qty, _version = await store.read_stock()
    assert qty == 3


@pytest.mark.asyncio
async def test_explicit_window_can_conflict(tmp_path) -> None:
    store = SqliteInventory(tmp_path / "window.sqlite", stock=2)
    await store.setup()
    _qty, version = await store.read_stock()
    results = await asyncio.gather(
        store.commit_action(_action("left"), {"source_version": version}, window_s=0.05),
        store.commit_action(_action("right"), {"source_version": version}, window_s=0.05),
    )
    assert sum(result.accepted for result in results) == 1
    assert any("version conflict" in result.reasons for result in results if not result.accepted)


@pytest.mark.asyncio
async def test_http_commit_and_close(tmp_path) -> None:
    store = SqliteInventory(tmp_path / "http.sqlite", stock=2)
    await store.setup()
    app = create_app(store)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://inventory") as client:
        env = HttpInventoryEnvironment(client)
        batch = await env.observe(env.default_refresh())
        version = batch.observations[0].source_version
        result = await env.commit(_action("http-1"), {"source_id": "inventory", "source_version": version})
        assert result.accepted is True
        again = await env.commit(_action("http-1"), {"source_id": "inventory", "source_version": version})
        assert again.commit_id == result.commit_id
        response = await client.post("/close")
        assert response.status_code == 200
        qty_body = await client.post("/observe", json={"read_tool": "read_inventory"})
        assert qty_body.status_code == 409
        with pytest.raises(RuntimeError):
            await env.commit(_action("http-2"), {"source_version": version})
