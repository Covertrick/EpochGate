"""DeepSeek V4.1-Flash 客户端可观察实验。

固定 prompt、关闭 thinking、不用工具循环。G0 撤权后继续读完并占槽；
G2-local 撤权后 aclose 并立刻放槽。backend_stop_status 固定 unknown。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

import httpx

from epochgate.sse import parse_sse_data
from epochgate.v50_metrics import summarize_bystanders
from epochgate.v50_runner import build_task_plan

ArmName = Literal["G0", "G2-local"]


def _completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


async def _stream_until(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    stop: asyncio.Event,
    on_token: Any,
) -> dict[str, Any]:
    """读到自然结束，或 stop 被置位后 aclose。返回状态，不把断流写成服务端停止。"""
    ttft = None
    last = None
    n_tok = 0
    usage: dict[str, Any] = {}
    error = ""
    closed_early = False
    try:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.status_code != 200:
                body = (await response.aread())[:240].decode(errors="replace")
                return {
                    "ok": False,
                    "error": f"http_{response.status_code}:{body}",
                    "ttft": None,
                    "last": None,
                    "n_tok": 0,
                    "usage": {},
                    "closed_early": False,
                }
            async for line in response.aiter_lines():
                if stop.is_set():
                    closed_early = True
                    await response.aclose()
                    break
                parsed = parse_sse_data(line)
                if parsed is None:
                    continue
                raw_usage = parsed.get("usage")
                if isinstance(raw_usage, dict):
                    usage = raw_usage
                choices = parsed.get("choices") or []
                delta = choices[0].get("delta") if choices else None
                if not isinstance(delta, dict):
                    continue
                piece = delta.get("content") or ""
                if not piece:
                    continue
                now = time.monotonic()
                if ttft is None:
                    ttft = now
                last = now
                n_tok += 1
                on_token(piece)
    except Exception as exc:  # noqa: BLE001 — transport recorded
        if stop.is_set():
            closed_early = True
        else:
            error = f"{type(exc).__name__}: {exc}"[:240]
    return {
        "ok": not error,
        "error": error,
        "ttft": ttft,
        "last": last,
        "n_tok": n_tok,
        "usage": usage,
        "closed_early": closed_early,
    }


async def run_ds_generation_trace(
    *,
    seed: int,
    arm: ArmName,
    api_key: str,
    base_url: str,
    model: str,
    load: str,
    cell_id: str,
    pair_id: str,
    batch_id: str,
    slots_s: int = 2,
    bystanders_b: int = 8,
    t_change_s: float = 2.0,
    max_tokens: int = 512,
    tau_s: float = 0.05,
    arm_order: str = "",
    code_version: str = "5.0.0",
) -> dict[str, Any]:
    """一条 DS trace。同 seed 的到达偏移与 G0/G2 共享。"""
    if arm not in {"G0", "G2-local"}:
        raise ValueError("DS allows only G0 and G2-local")
    if model != "deepseek-flash":
        raise ValueError(f"DS model must be deepseek-flash, got {model}")

    span_s = 6.4 if load == "high" else 12.0
    plan = build_task_plan(seed, bystanders_b=bystanders_b, span_s=span_s)
    # successor is scheduled after the target releases, not at a hashed offset
    plan = [item for item in plan if item["workflow_role"] != "successor"]
    t0 = time.monotonic()
    horizon = t0 + 300.0
    sem = asyncio.Semaphore(slots_s)
    rows: list[dict[str, Any]] = []
    target_released = asyncio.Event()
    errors: list[str] = []

    url = _completions_url(base_url)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _payload(words: int) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Write a detailed inventory restocking plan in about {words} English words. "
                        "Do not use tools."
                    ),
                }
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": True,
            "stream_options": {"include_usage": True},
            "thinking": {"type": "disabled"},
        }

    async def _occupy(item: dict[str, Any], *, arrival: float, is_target: bool) -> None:
        wait_deadline = horizon - time.monotonic()
        if wait_deadline <= 0:
            rows.append(_censored(item, arrival, t0))
            return
        try:
            await asyncio.wait_for(sem.acquire(), timeout=wait_deadline)
        except TimeoutError:
            rows.append(_censored(item, arrival, t0))
            return
        slot_at = time.monotonic()
        stop = asyncio.Event()
        revoke_at: float | None = None
        pre_revoke = False

        async def _revoke_later() -> None:
            nonlocal revoke_at, pre_revoke
            await asyncio.sleep(t_change_s)
            if stop.is_set():
                return
            revoke_at = time.monotonic()
            if arm == "G2-local":
                stop.set()

        revoker = asyncio.create_task(_revoke_later()) if is_target else None
        result: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=120.0, trust_env=True) as client:
            result = await _stream_until(
                client=client,
                url=url,
                headers=headers,
                payload=_payload(int(item["words"])),
                stop=stop,
                on_token=lambda _p: None,
            )
        if revoker is not None:
            if not revoker.done():
                pre_revoke = result.get("ok", False) and not stop.is_set()
                revoker.cancel()
            await asyncio.gather(revoker, return_exceptions=True)
        done_at = time.monotonic()
        sem.release()
        if is_target:
            target_released.set()
        if result.get("error"):
            errors.append(str(result["error"]))
        prompt_tokens = (result.get("usage") or {}).get("prompt_tokens")
        completion_tokens = (result.get("usage") or {}).get("completion_tokens")
        rows.append(
            {
                "task_id": item["task_id"],
                "workflow_role": item["workflow_role"],
                "event_id": f"{pair_id}-{arm}",
                "arrival_mono": arrival,
                "slot_acquired_mono": slot_at,
                "ttft_mono": result.get("ttft"),
                "last_token_mono": result.get("last"),
                "completed_mono": done_at if result.get("ok") or result.get("closed_early") else None,
                "slot_released_mono": done_at,
                "eligibility_revoked_mono": revoke_at,
                "cancel_requested_mono": revoke_at if arm == "G2-local" and revoke_at else None,
                "connection_closed_mono": done_at if result.get("closed_early") else None,
                "local_read_stopped_mono": done_at if result.get("closed_early") else None,
                "provider_cancel_ack": False,
                "provider_abort_not_issued": True,
                "backend_stop_status": "unknown",
                "residual_lower_s": None,
                "residual_upper_s": None,
                "W_slot_s": max(0.0, slot_at - arrival),
                "W_ttft_s": None
                if result.get("ttft") is None
                else max(0.0, float(result["ttft"]) - arrival),
                "T_e2e_s": None
                if not (result.get("ok") or result.get("closed_early"))
                else max(0.0, done_at - arrival),
                "pre_revoke_completed": pre_revoke,
                "input_tokens": prompt_tokens,
                "output_tokens": completion_tokens,
                "local_deltas": result.get("n_tok"),
                "closed_early": bool(result.get("closed_early")),
                "task_status": "ok" if result.get("ok") or result.get("closed_early") else "failed",
                "error": result.get("error") or "",
                "seed": seed,
                "arm": arm,
                "load": load,
                "model": model,
                "thinking": "disabled",
            }
        )

    def _censored(item: dict[str, Any], arrival: float, origin: float) -> dict[str, Any]:
        return {
            "task_id": item["task_id"],
            "workflow_role": item["workflow_role"],
            "arrival_mono": arrival,
            "W_slot_s": None,
            "T_e2e_s": None,
            "backend_stop_status": "unknown",
            "residual_lower_s": None,
            "task_status": "censored",
            "censored": True,
            "seed": seed,
            "arm": arm,
            "load": load,
            "model": model,
        }

    async def _job(item: dict[str, Any]) -> None:
        arrival = t0 + float(item["arrival_offset_s"])
        delay = arrival - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        await _occupy(item, arrival=arrival, is_target=item["workflow_role"] == "target")

    async def _successor() -> None:
        await target_released.wait()
        arrival = time.monotonic()
        item = {
            "task_id": "successor",
            "workflow_role": "successor",
            "words": 200,
        }
        await _occupy(item, arrival=arrival, is_target=False)

    started = time.monotonic()
    await asyncio.gather(*[_job(item) for item in plan], _successor())
    wall = time.monotonic() - started
    for row in rows:
        assert row.get("backend_stop_status") == "unknown"
        assert row.get("residual_lower_s") is None
    summary = summarize_bystanders(rows, tau=tau_s)
    focal = next((row for row in rows if row.get("workflow_role") == "target"), None)
    return {
        "seed": seed,
        "arm": arm,
        "load": load,
        "arrival_span_s": span_s,
        "cell_id": cell_id,
        "pair_id": pair_id,
        "batch_id": batch_id,
        "backend_kind": "ds",
        "model": model,
        "thinking": "disabled",
        "arm_order": arm_order,
        "failed": bool(errors) or any(row.get("task_status") == "failed" for row in rows),
        "error": "; ".join(errors)[:500],
        "wall_s": wall,
        "rows": rows,
        "summary": summary,
        "focal": focal,
        "code_version": code_version,
        "claim_ceiling": "client-observable only; deepseek-flash; backend residual unknown",
    }
