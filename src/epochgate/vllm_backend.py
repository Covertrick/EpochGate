"""vLLM OpenAI 兼容适配器 + 三级观测轮询。

L2-a：请求状态 aborted/finished
L2-b：调度队列移除（若管理面提供）
L2-c：GPU/KV 指标（仅当端点可用；默认不可用则不声称）

兼容性未通过前不得用于机制确认性主表。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from epochgate.http_streaming import HttpStreamingBackend
from epochgate.ids import IdFactory
from epochgate.models import BackendCapabilities, BackendHandle, CancelReceipt, InferenceRequest


class VllmObservingBackend(HttpStreamingBackend):
    """在 HttpStreamingBackend 上增加可选管理面轮询。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "EMPTY",
        model: str,
        system_prompt: str = "",
        stop_on_cancel: bool = True,
        admin_base_url: str | None = None,
        metrics_url: str | None = None,
        poll_timeout_s: float = 5.0,
        poll_interval_s: float = 0.1,
        max_inflight: int = 2,
        timeout_s: float = 120.0,
        ids: IdFactory | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            tool_choice="none",
            stop_on_cancel=stop_on_cancel,
            ids=ids,
            timeout_s=timeout_s,
            max_inflight=max_inflight,
        )
        self._admin_base = (admin_base_url or base_url).rstrip("/")
        self._metrics_url = metrics_url
        self._poll_timeout_s = poll_timeout_s
        self._poll_interval_s = poll_interval_s
        self._request_ids: dict[str, str] = {}
        self._last_observation: dict[str, Any] = {}
        # Override caps: generation observability possible via admin poll
        self._caps = BackendCapabilities(
            supports_cancel=True,
            supports_streaming=True,
            resource_reclaim_observable=False,
            max_inflight=max_inflight,
            streaming_capability=True,
            local_read_abort=stop_on_cancel,
            client_stream_close_issued=stop_on_cancel,
            transport_disconnect_observed=False,
            provider_cancel_api_invoked=False,
            provider_cancel_ack_received=False,
            provider_cancel_request=False,
            provider_cancel_ack_semantics="UNKNOWN",
            server_generation_observability=True,
            server_generation_stopped_observed=False,
            server_resource_reclaim_observability=bool(metrics_url),
            server_resource_reclaim_observed=False,
        )

    def capabilities(self) -> BackendCapabilities:
        return self._caps

    async def submit(self, request: InferenceRequest) -> BackendHandle:
        handle = await super().submit(request)
        self._request_ids[handle.handle_id] = request.attempt_id
        return handle

    async def cancel(self, handle: BackendHandle) -> CancelReceipt:
        receipt = await super().cancel(handle)
        obs = await self.poll_observation(handle.handle_id)
        self._last_observation[handle.handle_id] = obs
        return receipt

    async def poll_observation(self, handle_id: str) -> dict[str, Any]:
        """轮询三级观测。缺端点时对应级别记 unavailable，不升级解读。"""
        started = time.monotonic()
        level_a: str = "unavailable"
        level_b: str = "unavailable"
        level_c: str = "unavailable"
        details: dict[str, Any] = {}
        attempt = self._request_ids.get(handle_id, handle_id)

        async with httpx.AsyncClient(timeout=self._poll_timeout_s) as client:
            # L2-a: try common is_done / status paths; tolerate 404
            while time.monotonic() - started < self._poll_timeout_s:
                for path in (
                    f"/v1/requests/{attempt}",
                    f"/requests/{attempt}",
                    "/is_done",
                ):
                    try:
                        resp = await client.get(f"{self._admin_base}{path}")
                        if resp.status_code == 200:
                            payload = resp.json()
                            details["status_payload"] = payload
                            status = str(
                                payload.get("status")
                                or payload.get("state")
                                or payload.get("finished_reason")
                                or "ok"
                            ).lower()
                            if any(x in status for x in ("abort", "cancel", "stop")):
                                level_a = "aborted"
                            elif any(x in status for x in ("finish", "done", "complete")):
                                level_a = "finished"
                            else:
                                level_a = f"observed:{status}"
                            break
                    except Exception as exc:  # noqa: BLE001 — compat probe
                        details.setdefault("a_errors", []).append(str(exc)[:120])
                if level_a != "unavailable":
                    break
                await asyncio.sleep(self._poll_interval_s)

            # L2-b: queue length / running list if present
            for path in ("/metrics", "/queue", "/v1/queue"):
                try:
                    resp = await client.get(f"{self._admin_base}{path}")
                    if resp.status_code == 200:
                        details["queue_payload"] = resp.text[:500]
                        level_b = "queue_snapshot"
                        break
                except Exception as exc:  # noqa: BLE001
                    details.setdefault("b_errors", []).append(str(exc)[:120])

            # L2-c: explicit metrics URL only
            if self._metrics_url:
                try:
                    resp = await client.get(self._metrics_url)
                    if resp.status_code == 200:
                        text = resp.text
                        details["metrics_excerpt"] = text[:500]
                        level_c = "metrics_snapshot"
                        if "vllm:num_requests_running" in text or "kv_cache" in text.lower():
                            level_c = "metrics_with_kv_or_running"
                except Exception as exc:  # noqa: BLE001
                    details["c_error"] = str(exc)[:120]
                    level_c = "unavailable"

        backend_stop = "unknown"
        if level_a in {"aborted", "finished"}:
            backend_stop = "stopped"
        elif level_a.startswith("observed:"):
            backend_stop = "delayed"

        return {
            "L2-a": level_a,
            "L2-b": level_b,
            "L2-c": level_c,
            "backend_stop_status": backend_stop,
            "details": details,
            "rule": "never_upgrade_ab_to_c",
        }

    def last_observation(self, handle_id: str) -> dict[str, Any]:
        return self._last_observation.get(handle_id, {})
