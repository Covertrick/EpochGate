"""v5.0 DeepSeek D1–D3：正式外部有效性实验（非冒烟）。

用法：
  python -m uv run python scripts/run_v50_ds.py --phase dev
  python -m uv run python scripts/run_v50_ds.py --phase confirm
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
from pathlib import Path

from epochgate.cli import load_project_env
from epochgate.v50_ds import run_ds_generation_trace

PROTOCOL = json.loads(Path("results/experiments/v5_0/protocol/v50_protocol.json").read_text(encoding="utf-8"))
OUT_DIR = Path("results/experiments/v5_0/ds")


async def _run_pair(seed: int, load: str, phase: str, sem: asyncio.Semaphore) -> None:
    async with sem:
        order = ["G0", "G2-local"]
        rng = random.Random(seed ^ 0x5A50)
        rng.shuffle(order)
        t_change = float(PROTOCOL["ds"]["t_change_s"])
        out = OUT_DIR / phase / "traces.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        for arm in order:
            trace = await run_ds_generation_trace(
                seed=seed,
                arm=arm,  # type: ignore[arg-type]
                api_key=os.environ["EPOCHGATE_API_KEY"],
                base_url=os.environ.get("EPOCHGATE_BASE_URL", "https://api.deepseek.com/v1"),
                model=os.environ.get("EPOCHGATE_MODEL", "deepseek-flash"),
                load=load,
                cell_id=f"ds-{load}-harmful-early",
                pair_id=f"ds-{load}-s{seed}",
                batch_id=f"ds-{load}-s{seed}-{arm}",
                slots_s=int(PROTOCOL["ds"]["slots_S"]),
                bystanders_b=int(PROTOCOL["ds"]["bystanders_B"]),
                t_change_s=t_change,
                max_tokens=int(PROTOCOL["ds"]["max_tokens"]),
                tau_s=float(PROTOCOL["thresholds"]["provisional_tau_s"]),
                arm_order="".join(order),
            )
            # D3 assertions: never residual=0 from aclose
            for row in trace["rows"]:
                assert row["backend_stop_status"] == "unknown"
                assert row.get("residual_lower_s") is None
            rec = {
                "phase": phase,
                "seed": seed,
                "load": load,
                "arm": arm,
                "model": os.environ.get("EPOCHGATE_MODEL", "deepseek-flash"),
                "thinking": "disabled",
                "arm_order": "".join(order),
                "failed": trace["failed"],
                "error": trace["error"],
                "summary": trace["summary"],
                "focal": trace["focal"],
                "rows": trace["rows"],
                "claim_ceiling": trace["claim_ceiling"],
            }
            with out.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"ds {phase} {load} seed={seed} arm={arm} failed={trace['failed']}", flush=True)


def _apply_repo_env() -> None:
    """仓库 .env 覆盖进程里过期的 EPOCHGATE_*，避免 deepseek-chat 盖掉 flash。"""
    load_project_env()
    env_path = Path(".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name in {"EPOCHGATE_BASE_URL", "EPOCHGATE_API_KEY", "EPOCHGATE_MODEL"}:
            os.environ[name] = value.strip().strip('"').strip("'")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["dev", "confirm"], required=True)
    parser.add_argument("--loads", default="medium", help="comma list: medium,high")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--max-seeds", type=int, default=0, help="0=all for phase")
    args = parser.parse_args()
    _apply_repo_env()
    if not os.environ.get("EPOCHGATE_API_KEY"):
        raise SystemExit("EPOCHGATE_API_KEY missing; DS phase skipped")
    model = os.environ.get("EPOCHGATE_MODEL", "")
    if model != "deepseek-flash":
        raise SystemExit(f"EPOCHGATE_MODEL must be deepseek-flash (V4.1-Flash); got {model!r}")

    if args.phase == "dev":
        seeds = list(PROTOCOL["ds"]["dev_seeds"])
    else:
        start = int(PROTOCOL["ds"]["confirm_seeds"]["start"])
        n = int(PROTOCOL["ds"]["confirm_seeds"]["n"])
        seeds = list(range(start, start + n))
    if args.max_seeds > 0:
        seeds = seeds[: args.max_seeds]

    loads = [x.strip() for x in args.loads.split(",") if x.strip()]
    sem = asyncio.Semaphore(args.concurrency)
    jobs = [_run_pair(seed, load, args.phase, sem) for load in loads for seed in seeds]
    await asyncio.gather(*jobs)


if __name__ == "__main__":
    asyncio.run(main())
