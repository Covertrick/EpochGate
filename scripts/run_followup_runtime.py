"""跑 4 类场景 × 3 批 × 20 个 attempt 的运行时闭环。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from epochgate.followup_runtime import ScriptedGeneration, VllmGeneration, run_matrix, summarize

OUT = Path("results/experiments/followup")


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("vllm", "scripted"), default="vllm")
    parser.add_argument("--batches", type=int, default=3)
    parser.add_argument("--tasks", type=int, default=20)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if args.engine == "vllm":
        generation = VllmGeneration(base_url=args.base_url)
    else:
        generation = ScriptedGeneration()
    try:
        rows = await run_matrix(generation, batches=args.batches, tasks=args.tasks)
    finally:
        close = getattr(generation, "aclose", None)
        if close is not None:
            await close()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "runtime.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    summary = summarize(rows, engine=args.engine)
    summary["batches"] = args.batches
    summary["tasks_per_batch"] = args.tasks
    (OUT / "runtime_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_main())
