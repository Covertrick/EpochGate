"""v5.1 独立时间窗口复现。不写入、不读取后混合 7000–7049。"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from epochgate.v50_ds import run_ds_generation_trace
from epochgate.v51_analysis import analysis_source_sha256, sha256_file

ROOT = Path("results/experiments/v5_1")
PROTOCOL_PATH = ROOT / "protocol" / "v51_protocol.json"
OUT = ROOT / "ds" / "replicate" / "traces.jsonl"
SEALED = Path("results/experiments/v5_0/ds/confirm/traces.jsonl")


def _load_env() -> None:
    env_path = Path(".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in {"EPOCHGATE_BASE_URL", "EPOCHGATE_API_KEY", "EPOCHGATE_MODEL"}:
            os.environ[name.strip()] = value.strip().strip('"').strip("'")


def _done(path: Path) -> set[tuple[int, str, str]]:
    found: set[tuple[int, str, str]] = set()
    if not path.exists():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        found.add((int(row["seed"]), str(row["load"]), str(row["arm"])))
    return found


async def _probe(api_key: str, base_url: str, model: str) -> None:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word ok."}],
        "max_tokens": 8,
        "temperature": 0,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60.0, trust_env=True) as client:
        response = await client.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        raise SystemExit(f"DeepSeek probe failed: http_{response.status_code}")


async def _run() -> None:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    if analysis_source_sha256() != protocol["analysis_sha256"]:
        raise SystemExit("analysis source changed after the protocol was frozen")
    if OUT.resolve() == SEALED.resolve():
        raise SystemExit("refusing to write the sealed confirm file")
    forbidden = set(range(6000, 6015)) | set(range(7000, 7050))
    seeds = [int(s) for s in protocol["seeds"]]
    if forbidden.intersection(seeds):
        raise SystemExit("protocol seeds overlap a sealed window")
    _load_env()
    api_key = os.environ.get("EPOCHGATE_API_KEY", "")
    model = os.environ.get("EPOCHGATE_MODEL", "")
    base_url = os.environ.get("EPOCHGATE_BASE_URL", "https://api.deepseek.com/v1")
    if not api_key or model != protocol["model"]:
        raise SystemExit("EPOCHGATE_API_KEY missing or EPOCHGATE_MODEL is not deepseek-flash")
    await _probe(api_key, base_url, model)

    sealed_before = sha256_file(SEALED)
    if sealed_before != protocol["sealed_confirm_sha256"]:
        raise SystemExit("sealed confirm file hash does not match the frozen protocol")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = _done(OUT)
    for load in protocol["loads"]:
        for seed in seeds:
            order = ["G0", "G2-local"]
            rng = random.Random(seed ^ 0x51A1)
            rng.shuffle(order)
            for arm in order:
                if (seed, load, arm) in done:
                    continue
                started = datetime.now(timezone.utc).isoformat()
                trace = await run_ds_generation_trace(
                    seed=seed,
                    arm=arm,  # type: ignore[arg-type]
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    load=load,
                    cell_id=f"v51-{load}-harmful-early",
                    pair_id=f"v51-{load}-s{seed}",
                    batch_id=f"v51-{load}-s{seed}-{arm}",
                    slots_s=int(protocol["slots_S"]),
                    bystanders_b=int(protocol["bystanders_B"]),
                    t_change_s=float(protocol["t_change_s"]),
                    max_tokens=int(protocol["max_tokens"]),
                    tau_s=0.05,
                    arm_order="".join(order),
                    code_version="5.1.0",
                )
                record = {
                    "phase": "v51-replicate",
                    "seed": seed,
                    "load": load,
                    "arm": arm,
                    "model": model,
                    "thinking": "disabled",
                    "temperature": protocol["temperature"],
                    "max_tokens": protocol["max_tokens"],
                    "arm_order": "".join(order),
                    "started_at": started,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "failed": trace["failed"],
                    "error": trace["error"],
                    "summary": trace["summary"],
                    "focal": trace["focal"],
                    "rows": trace["rows"],
                    "wall_s": trace["wall_s"],
                    "claim_ceiling": trace["claim_ceiling"],
                    "mix_with_7000_7049": False,
                }
                with OUT.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"v51 {load} seed={seed} arm={arm} failed={trace['failed']} wall={trace['wall_s']:.1f}s", flush=True)
                if sha256_file(SEALED) != sealed_before:
                    raise SystemExit("sealed confirm file changed during replication")
    print("v51 replication write complete", flush=True)


if __name__ == "__main__":
    started = time.monotonic()
    asyncio.run(_run())
    print(f"elapsed_s={time.monotonic() - started:.1f}", flush=True)
