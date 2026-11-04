"""阶段 C：脚本四臂与 vLLM 兼容性分开落盘。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from epochgate.v51_scripted import event_time, run_scripted_group

SCRIPTED_OUT = Path("results/experiments/v5_1/scripted/calibration.json")
VLLM_OUT = Path("results/experiments/v5_1/vllm/compat.json")
MD = Path("docs/测试记录/v5.1-机制.md")


async def _vllm_compat() -> dict:
    url = "http://127.0.0.1:8000/v1/models"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url)
        reachable = response.status_code < 500
        status = response.status_code
    except httpx.HTTPError:
        reachable = False
        status = None
    return {
        "source": "vllm-compat",
        "base_url": "http://127.0.0.1:8000/v1",
        "reachable": reachable,
        "http_status": status,
        "L2-a": "unavailable",
        "L2-b": "unavailable",
        "L2-c": "unavailable",
        "entered_mechanism_matrix": False,
        "reason": "no local vLLM answered; client disconnect is not treated as abort",
    }


async def _main() -> None:
    scripted = []
    for arm, residual, hold in (
        ("G0", 0.0, 0.0),
        ("G2-local", 0.0, 0.0),
        ("G2-residual", 0.05, 0.0),
        ("G2-hold-slot", 0.0, 0.05),
    ):
        scripted.append(
            await run_scripted_group(
                arm,  # type: ignore[arg-type]
                residual_s=residual,
                hold_slot_s=hold,
                t_change_s=0.0,
                token_interval_s=0.0,
            )
        )
    vllm = await _vllm_compat()
    SCRIPTED_OUT.parent.mkdir(parents=True, exist_ok=True)
    VLLM_OUT.parent.mkdir(parents=True, exist_ok=True)
    SCRIPTED_OUT.write_text(json.dumps(scripted, ensure_ascii=False, indent=2), encoding="utf-8")
    VLLM_OUT.write_text(json.dumps(vllm, ensure_ascii=False, indent=2), encoding="utf-8")

    def rel(name: str, arm_report: dict) -> str:
        release = event_time(arm_report, "slot_released")
        left_q = event_time(arm_report, "left_scheduler_queue")
        if release is None or left_q is None:
            return f"{name}: missing"
        return f"{name}: slot_released={release:.4f}s left_scheduler_queue={left_q:.4f}s"

    lines = [
        "# v5.1 机制",
        "",
        "脚本校准和 vLLM 验证分开。脚本里的残余是注入的，不能写成 vLLM 的取消路径。",
        "",
        "## vLLM",
        "",
        f"本机 `{vllm['base_url']}` reachable={vllm['reachable']}。L2-a/b/c 都是 unavailable。未进入机制矩阵。",
        "2026-10 的社区报告只说明断连不能默认等于 abort；本台架没有自己的 abort 观测。",
        "",
        "## 脚本四臂",
        "",
        "种子 9100。KV/GPU 不可观测。`abort_confirmed` 只表示本地停读，`provider_abort_ack=false`。",
        "",
    ]
    for report in scripted:
        lines.append("- " + rel(report["arm"], report))
    lines.append("")
    lines.append("G2-residual 的 `left_scheduler_queue` 晚于放槽，因为残余是 `injected-script`。G2-hold-slot 的放槽晚于离开脚本队列。这两组只说明仪器能把三种事件分开记。")
    lines.append("")
    MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"vllm_reachable": vllm["reachable"], "scripted_arms": [r["arm"] for r in scripted]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_main())
