"""vLLM 三级观测兼容性测试。失败则不得进入机制确认性。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from epochgate.ids import IdFactory
from epochgate.vllm_backend import VllmObservingBackend

OUT = Path("results/experiments/v5_0/vllm/compat.json")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000/v1"))
    parser.add_argument("--admin-base-url", default=os.environ.get("VLLM_ADMIN_URL"))
    parser.add_argument("--metrics-url", default=os.environ.get("VLLM_METRICS_URL"))
    parser.add_argument("--model", default=os.environ.get("VLLM_MODEL", "dummy"))
    args = parser.parse_args()

    backend = VllmObservingBackend(
        base_url=args.base_url,
        api_key=os.environ.get("VLLM_API_KEY", "EMPTY"),
        model=args.model,
        admin_base_url=args.admin_base_url,
        metrics_url=args.metrics_url,
        ids=IdFactory(42),
        max_inflight=1,
        timeout_s=30.0,
    )
    # Minimal: only probe observation endpoints without requiring a live generation
    handle_id = "compat-probe"
    backend._request_ids[handle_id] = "compat-1"  # noqa: SLF001 — compat harness
    obs = await backend.poll_observation(handle_id)
    result = {
        "base_url": args.base_url,
        "admin_base_url": args.admin_base_url,
        "metrics_url": args.metrics_url,
        "observation": obs,
        "L2-a_ok": obs.get("L2-a") not in {None, "unavailable"},
        "L2-b_ok": obs.get("L2-b") not in {None, "unavailable"},
        "L2-c_ok": obs.get("L2-c") not in {None, "unavailable"},
        "pass_min_for_mechanism": obs.get("L2-a") not in {None, "unavailable"}
        or obs.get("L2-b") not in {None, "unavailable"},
        "rule": "never_upgrade_ab_to_c",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["pass_min_for_mechanism"]:
        raise SystemExit("vLLM compat: neither L2-a nor L2-b available; do not enter mechanism confirm")


if __name__ == "__main__":
    asyncio.run(main())
