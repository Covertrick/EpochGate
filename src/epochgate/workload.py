"""Open-loop 工作负载。政策不参与生成，到达在回放前哈希。"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any

from pydantic import BaseModel

SERVICE_S = {"short": 2.0, "mid": 6.0, "long": 16.0}
MIX = (("long", 0.40), ("short", 0.40), ("mid", 0.20))
FOCAL_SERVICE_S = SERVICE_S["long"]
N_BYSTANDERS = 36
DRAIN_S = 4.0 * FOCAL_SERVICE_S


class Arrival(BaseModel):
    """一条预先写死的到达。"""

    workflow_id: str
    length_class: str
    service_s: float
    arrival_time: float
    role_seed: str


class Workload(BaseModel):
    """一份与撤销政策无关的 trace。"""

    seed: int
    trigger_class: str
    timing: str
    rho: float
    load: str
    slots: int
    interarrival_s: float
    focal_service_s: float
    event_time: float
    remaining_at_event_s: float
    horizon_s: float
    arrivals: list[Arrival]
    arrival_hash: str
    p_b: float = 0.0


def _class_for(rng: random.Random) -> str:
    draw = rng.random()
    cursor = 0.0
    for label, weight in MIX:
        cursor += weight
        if draw <= cursor:
            return label
    return "mid"


def _hash_arrivals(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def build_workload(
    *,
    seed: int,
    trigger_class: str,
    timing: str,
    rho: float,
    load: str,
    interarrival_s: float,
    slots: int = 4,
    n_bystanders: int = N_BYSTANDERS,
) -> Workload:
    """生成一条 open-loop trace。

    输入：种子、有害或无害、早或中时机、ρ、负载名、旁观者到达间隔、槽位数。
    输出：焦点任务固定为 16 秒长任务，旁观者按 40/40/20 混合。
    副作用：无。
    失败：时机或触发类非法时抛出 ``ValueError``。
    """
    if trigger_class not in {"harmful", "harmless"}:
        raise ValueError("trigger_class must be harmful or harmless")
    if timing not in {"early", "mid"}:
        raise ValueError("timing must be early or mid")
    fraction = 0.20 if timing == "early" else 0.50
    event_time = fraction * FOCAL_SERVICE_S
    remaining = FOCAL_SERVICE_S - event_time
    rng = random.Random(seed)
    arrivals = [
        Arrival(
            workflow_id="wf-focal",
            length_class="long",
            service_s=FOCAL_SERVICE_S,
            arrival_time=0.0,
            role_seed="focal",
        )
    ]
    for index in range(n_bystanders):
        label = _class_for(rng)
        arrivals.append(
            Arrival(
                workflow_id=f"wf-b{index:02d}",
                length_class=label,
                service_s=SERVICE_S[label],
                arrival_time=(index + 1) * interarrival_s,
                role_seed="bystander",
            )
        )
    last_arrival = arrivals[-1].arrival_time
    body = {
        "arrivals": [item.model_dump() for item in arrivals],
        "event_time": event_time,
        "remaining_at_event_s": remaining,
        "trigger_class": trigger_class,
        "rho": rho,
    }
    return Workload(
        seed=seed,
        trigger_class=trigger_class,
        timing=timing,
        rho=rho,
        load=load,
        slots=slots,
        interarrival_s=interarrival_s,
        focal_service_s=FOCAL_SERVICE_S,
        event_time=event_time,
        remaining_at_event_s=remaining,
        horizon_s=last_arrival + DRAIN_S,
        arrivals=arrivals,
        arrival_hash=_hash_arrivals(body),
        p_b=0.0 if trigger_class == "harmful" else 1.0,
    )
