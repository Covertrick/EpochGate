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


def _class_for(rng: random.Random, mix: tuple[tuple[str, float], ...]) -> str:
    draw = rng.random()
    total = sum(weight for _label, weight in mix) or 1.0
    cursor = 0.0
    for label, weight in mix:
        cursor += weight / total
        if draw <= cursor:
            return label
    return mix[-1][0]


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
    mix: tuple[tuple[str, float], ...] | None = None,
    service_noise: float = 0.0,
    arrival_jitter: float = 0.0,
) -> Workload:
    """生成一条 open-loop trace。

    输入：种子、有害或无害、早或中时机、ρ、负载名、旁观者到达间隔、槽位数。
    可选的混合比例、服务时间相对扰动和到达间隔抖动只用于敏感性分析。默认值与主实验相同。
    输出：焦点任务默认 16 秒。扰动打开时，事件仍发生在该焦点任务服务的同一比例处。
    副作用：无。
    失败：时机、触发类或扰动幅度非法时抛出 ``ValueError``。
    """
    if trigger_class not in {"harmful", "harmless"}:
        raise ValueError("trigger_class must be harmful or harmless")
    if timing not in {"early", "mid", "late"}:
        raise ValueError("timing must be early, mid, or late")
    if not 0.0 <= service_noise < 1.0:
        raise ValueError("service_noise must be in [0, 1)")
    if not 0.0 <= arrival_jitter < 1.0:
        raise ValueError("arrival_jitter must be in [0, 1)")
    mix_used = mix or MIX
    fraction = {"early": 0.20, "mid": 0.50, "late": 0.80}[timing]
    rng = random.Random(seed)
    noise_rng = random.Random(seed + 10007)
    jitter_rng = random.Random(seed + 20011)
    focal_service = FOCAL_SERVICE_S
    if service_noise:
        focal_service *= noise_rng.uniform(1.0 - service_noise, 1.0 + service_noise)
    event_time = fraction * focal_service
    remaining = focal_service - event_time
    arrivals = [
        Arrival(
            workflow_id="wf-focal",
            length_class="long",
            service_s=focal_service,
            arrival_time=0.0,
            role_seed="focal",
        )
    ]
    previous = 0.0
    for index in range(n_bystanders):
        label = _class_for(rng, mix_used)
        service = SERVICE_S[label]
        if service_noise:
            service *= noise_rng.uniform(1.0 - service_noise, 1.0 + service_noise)
        arrival = (index + 1) * interarrival_s
        if arrival_jitter:
            arrival += jitter_rng.uniform(-arrival_jitter * interarrival_s, arrival_jitter * interarrival_s)
            arrival = max(previous + 1e-3, arrival)
        previous = arrival
        arrivals.append(
            Arrival(
                workflow_id=f"wf-b{index:02d}",
                length_class=label,
                service_s=service,
                arrival_time=arrival,
                role_seed="bystander",
            )
        )
    last_arrival = arrivals[-1].arrival_time
    longest = max(item.service_s for item in arrivals)
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
        focal_service_s=focal_service,
        event_time=event_time,
        remaining_at_event_s=remaining,
        horizon_s=last_arrival + max(DRAIN_S, 4.0 * longest),
        arrivals=arrivals,
        arrival_hash=_hash_arrivals(body),
        p_b=0.0 if trigger_class == "harmful" else 1.0,
    )
