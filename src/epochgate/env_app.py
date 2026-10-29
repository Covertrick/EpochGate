"""FastAPI 库存服务。提交走 SQLite 条件更新，默认不打开检查-写入窗口。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from epochgate.models import CandidateAction
from epochgate.sqlite_inventory import InventoryClosed, SqliteInventory


class ObserveBody(BaseModel):
    read_tool: str = "read_inventory"


class CheckBody(BaseModel):
    action_id: str
    task_id: str
    attempt_id: str
    plan_epoch: int = 0
    obs_revision: int = 0
    action_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    observation_ids: list[str] = Field(default_factory=list)


class CommitBody(CheckBody):
    conditions: dict[str, Any] = Field(default_factory=dict)
    window_s: float = 0.0


class ChangesBody(BaseModel):
    cursor: str = "0"


def create_app(store: SqliteInventory) -> FastAPI:
    """挂上观察、检查、提交、变化和关闭。

    输入：已经 ``setup`` 过的库存。
    输出：FastAPI 应用。
    副作用：路由会读写该库存。
    失败：无。
    """
    app = FastAPI()

    @app.post("/observe")
    async def observe(body: ObserveBody) -> dict[str, Any]:
        if body.read_tool != "read_inventory":
            raise HTTPException(status_code=400, detail=f"unknown read tool: {body.read_tool}")
        try:
            qty, version = await store.read_stock()
        except InventoryClosed as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "observations": [
                {
                    "observation_id": "",
                    "source_id": "inventory",
                    "source_version": version,
                    "payload": {"stock": qty},
                }
            ]
        }

    @app.post("/check")
    async def check(body: CheckBody) -> dict[str, Any]:
        try:
            result = await store.check(_candidate(body))
        except InventoryClosed as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": result.ok, "reasons": list(result.reasons)}

    @app.post("/commit")
    async def commit(body: CommitBody) -> dict[str, Any]:
        try:
            result = await store.commit_action(
                _candidate(body),
                body.conditions,
                window_s=body.window_s,
            )
        except InventoryClosed as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "accepted": result.accepted,
            "commit_id": result.commit_id,
            "reasons": list(result.reasons),
        }

    @app.post("/changes")
    async def changes(body: ChangesBody) -> dict[str, Any]:
        return {"events": [], "next_cursor": body.cursor or "0"}

    @app.post("/close")
    async def close() -> dict[str, bool]:
        await store.close()
        return {"closed": True}

    return app


def _candidate(body: CheckBody) -> CandidateAction:
    return CandidateAction(
        action_id=body.action_id,
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        plan_epoch=body.plan_epoch,
        obs_revision=body.obs_revision,
        action_type=body.action_type,
        parameters=dict(body.parameters),
        observation_ids=list(body.observation_ids),
    )
