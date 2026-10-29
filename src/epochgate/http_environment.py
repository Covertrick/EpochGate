"""通过 HTTP 调用 FastAPI 库存。runtime 仍只看见 EnvironmentAdapter。"""

from __future__ import annotations

import time
from typing import Any, Mapping

import httpx

from epochgate.ids import IdFactory
from epochgate.models import (
    CandidateAction,
    ChangeBatch,
    CommitResult,
    Observation,
    ObservationBatch,
    RefreshRecipe,
    ToolSpec,
    ValidationResult,
)


class HttpInventoryEnvironment:
    """把观察和提交发到环境服务。关闭后的写入会变成异常，而不是空成功。"""

    def __init__(self, client: httpx.AsyncClient, *, ids: IdFactory | None = None) -> None:
        self._client = client
        self._ids = ids or IdFactory(None)

    def tool_catalog(self) -> list[ToolSpec]:
        """返回读库存和预约两个工具。

        输入：无。输出：工具列表。副作用：无。失败：无。
        """
        return [
            ToolSpec(
                name="read_inventory",
                role="read",
                description="Read stock",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="reserve",
                role="effect",
                description="Reserve units",
                parameters={
                    "type": "object",
                    "properties": {"qty": {"type": "integer"}},
                    "required": ["qty"],
                },
            ),
        ]

    def default_refresh(self) -> RefreshRecipe:
        """默认只读库存。输入：无。输出：配方。副作用：无。失败：无。"""
        return RefreshRecipe(read_tool="read_inventory", sources=["inventory"], merge_key="inventory")

    async def observe(self, recipe: RefreshRecipe) -> ObservationBatch:
        """请求当前库存快照。

        输入：只读配方。
        输出：一条带版本的观测。
        副作用：服务端只读。
        失败：HTTP 错误或服务已关闭时抛出 ``RuntimeError``。
        """
        response = await self._client.post("/observe", json={"read_tool": recipe.read_tool})
        _raise_closed(response)
        response.raise_for_status()
        body = response.json()
        now = time.monotonic()
        observations: list[Observation] = []
        for item in body.get("observations") or []:
            observations.append(
                Observation(
                    observation_id=str(item.get("observation_id") or self._ids("obs")),
                    source_id=str(item.get("source_id") or "inventory"),
                    source_version=item.get("source_version"),
                    sample_time_mono=now,
                    received_time_mono=now,
                    payload=dict(item.get("payload") or {}),
                )
            )
        return ObservationBatch(observations=observations)

    async def changes(self, cursor: str) -> ChangeBatch:
        """拉取变化。当前服务不产生外生事件。

        输入：游标。输出：空批次。副作用：无。失败：HTTP 错误时向上抛。
        """
        response = await self._client.post("/changes", json={"cursor": cursor})
        response.raise_for_status()
        body = response.json()
        return ChangeBatch(events=[], next_cursor=str(body.get("next_cursor") or cursor))

    async def check(self, candidate: CandidateAction) -> ValidationResult:
        """提交前检查。

        输入：候选。输出：检查结果。副作用：无。失败：服务已关闭时抛出 ``RuntimeError``。
        """
        response = await self._client.post("/check", json=_action_body(candidate))
        _raise_closed(response)
        response.raise_for_status()
        body = response.json()
        return ValidationResult(ok=bool(body.get("ok")), reasons=list(body.get("reasons") or []))

    async def commit(self, candidate: CandidateAction, conditions: Mapping[str, Any]) -> CommitResult:
        """条件提交。

        输入：候选和网关条件。
        输出：提交结果。
        副作用：服务端可能扣减库存。
        失败：服务已关闭时抛出 ``RuntimeError``。
        """
        payload = _action_body(candidate)
        payload["conditions"] = dict(conditions)
        payload["window_s"] = float(conditions.get("window_s") or 0)
        response = await self._client.post("/commit", json=payload)
        _raise_closed(response)
        response.raise_for_status()
        body = response.json()
        return CommitResult(
            accepted=bool(body.get("accepted")),
            commit_id=body.get("commit_id"),
            reasons=list(body.get("reasons") or []),
        )


def _action_body(candidate: CandidateAction) -> dict[str, Any]:
    return {
        "action_id": candidate.action_id,
        "task_id": candidate.task_id,
        "attempt_id": candidate.attempt_id,
        "plan_epoch": candidate.plan_epoch,
        "obs_revision": candidate.obs_revision,
        "action_type": candidate.action_type,
        "parameters": dict(candidate.parameters),
        "observation_ids": list(candidate.observation_ids),
    }


def _raise_closed(response: httpx.Response) -> None:
    if response.status_code == 409:
        raise RuntimeError(response.text or "inventory closed")
