"""四臂脚本校准。事件来自注入的本地令牌，不是 vLLM。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

Arm = Literal["G0", "G2-local", "G2-residual", "G2-hold-slot"]


async def run_scripted_group(
    arm: Arm,
    *,
    seed: int = 9100,
    slots: int = 2,
    bystanders: int = 4,
    token_interval_s: float = 0.0,
    target_tokens: int = 6,
    other_tokens: int = 3,
    t_change_s: float = 0.0,
    residual_s: float = 0.0,
    hold_slot_s: float = 0.0,
) -> dict[str, Any]:
    """记录本地停读、放槽和脚本令牌释放。KV/GPU 标不可观测。"""
    if arm not in {"G0", "G2-local", "G2-residual", "G2-hold-slot"}:
        raise ValueError(arm)
    origin = time.monotonic()
    events: list[dict[str, Any]] = []

    def mark(event: str, role: str, **extra: Any) -> None:
        events.append({"t_s": time.monotonic() - origin, "event": event, "role": role, **extra})

    sem = asyncio.Semaphore(slots)
    released = asyncio.Event()

    async def occupy(role: str, arrival_s: float, tokens: int, *, is_target: bool) -> None:
        mark("enqueued", role)
        if arrival_s > 0:
            await asyncio.sleep(arrival_s)
        mark("arrival", role)
        await sem.acquire()
        mark("slot_acquired", role)
        mark("engine_enter", role, level="scripted")
        mark("execution_start", role, level="scripted")
        produced = 0
        stopped = is_target and arm != "G0"
        if stopped:
            if t_change_s > 0:
                await asyncio.sleep(t_change_s)
            mark("eligibility_revoked", role)
            mark("abort_issued", role, level="scripted-local")
            mark("local_read_stopped", role, level="scripted-local")
            mark("abort_confirmed", role, level="scripted-local", provider_abort_ack=False)
            mark("left_running", role, level="scripted")
            if arm == "G2-hold-slot":
                mark("left_scheduler_queue", role, level="scripted", residual_source="none")
                if hold_slot_s > 0:
                    await asyncio.sleep(hold_slot_s)
                mark("slot_released", role)
            elif arm == "G2-residual":
                mark("slot_released", role)
                sem.release()
                if is_target:
                    released.set()
                if residual_s > 0:
                    mark("backend_residual_start", role, residual_source="injected-script", residual_s=residual_s)
                    await asyncio.sleep(residual_s)
                mark("left_scheduler_queue", role, level="scripted", residual_source="injected-script")
                mark("completed", role, produced_tokens=produced)
                return
            else:
                mark("left_scheduler_queue", role, level="scripted", residual_source="none")
                mark("slot_released", role)
        else:
            for _ in range(tokens):
                if token_interval_s > 0:
                    await asyncio.sleep(token_interval_s)
                produced += 1
            mark("left_running", role, level="scripted", reason="natural_end")
            mark("left_scheduler_queue", role, level="scripted", reason="natural_end")
            mark("slot_released", role)
        sem.release()
        if is_target:
            released.set()
        mark("completed", role, produced_tokens=produced)

    async def successor() -> None:
        mark("successor_waiting_for_slot_release", "successor")
        await released.wait()
        await occupy("successor", 0.0, other_tokens, is_target=False)

    jobs = [occupy("target", 0.0, target_tokens, is_target=True)]
    for index in range(bystanders):
        jobs.append(occupy(f"bystander-{index}", 0.0, other_tokens, is_target=False))
    await asyncio.gather(*jobs, successor())
    return {
        "arm": arm,
        "seed": seed,
        "source": "scripted-calibration",
        "not_vllm": True,
        "kv_gpu": "unavailable",
        "observation_levels_claimed": [],
        "events": events,
    }


def event_time(report: dict[str, Any], event: str, role: str = "target") -> float | None:
    for item in report["events"]:
        if item["event"] == event and item["role"] == role:
            return float(item["t_s"])
    return None
