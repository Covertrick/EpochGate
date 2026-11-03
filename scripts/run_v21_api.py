"""第 8.3 节的小规模事件链。不估计旁观者 P95。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from epochgate.cli import load_project_env
from epochgate.v22_runner import run_v22_case

OUT = Path("results/experiments/v2_1/api/events.jsonl")
SEEDS = list(range(9100, 9110))
POLICIES = {"G0": "A", "G1": "B", "G2": "C"}


def _done() -> set[tuple]:
    found = set()
    if not OUT.exists():
        return found
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("task_id") == "t0":
            found.add((row.get("trigger_class"), row.get("policy"), row.get("seed")))
    return found


async def _one(seed: int, policy: str, trigger: str, sem: asyncio.Semaphore) -> None:
    async with sem:
        rows = await run_v22_case(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            seed=seed,
            arm=POLICIES[policy],
            task_kind="invalidate" if trigger == "harmful" else "harmless",
            cell_id=f"api_{trigger}_{policy}",
            pair_id=f"api-{trigger}-s{seed}",
            batch_id=f"api-{trigger}-s{seed}-{policy}",
            length_tier="long",
            timing_tier="early",
            target_words=450,
            t_change_s=0.25,
            scheduler_order="fifo",
            task_plan=[
                {"task_id": "t0", "words": 450, "length_tier": "long", "arrival_offset_s": 0.0},
                {"task_id": "t1", "words": 50, "length_tier": "short", "arrival_offset_s": 0.0},
            ],
            code_version="3.0.0",
            experiment_block="v21-api",
            max_inflight=2,
        )
        with OUT.open("a", encoding="utf-8") as handle:
            for row in rows:
                row["policy"] = policy
                row["trigger_class"] = trigger
                row["phase"] = "api-mechanism"
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"done {trigger} {policy} seed={seed}", flush=True)


async def main() -> None:
    """跑长任务早时机的事件链。

    输入：无。密钥来自进程环境。
    输出：追加 JSONL。
    副作用：调用 DeepSeek。不把等待差写进主结论。
    失败：缺密钥时退出。单次传输错误记在行里。
    """
    load_project_env()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY missing; skip")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = _done()
    sem = asyncio.Semaphore(4)
    jobs = []
    for trigger in ("harmful", "harmless"):
        for policy in POLICIES:
            for seed in SEEDS:
                if (trigger, policy, seed) in done:
                    continue
                jobs.append(_one(seed, policy, trigger, sem))
    await asyncio.gather(*jobs)
    print(json.dumps({"ran": len(jobs)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
