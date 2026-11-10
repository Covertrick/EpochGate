"""并发 2/4/8 的干净重跑。每个槽使用自己的 SQLite 连接。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from epochgate_runtime.sandbox import lock_workspace, start_container, stop_container

from runtime_online_arms import OUT, _batch, _summarize

ROOT = OUT
LEDGER_DIR = OUT / "online_scale_ledgers"


def _annotate_first_attempt() -> None:
    path = OUT / "online_b0b1b2.json"
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["concurrency_2_first_attempt"] = {
        "valid_measurement": False,
        "reason": "Two worker threads shared one sqlite connection. B1 repeat 1 and B2 repeat 1 each kept only 1 of 2 samples after sqlite3.InterfaceError. Do not cite this concurrency-2 block.",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _level(concurrency: int) -> dict[str, Any]:
    containers: dict[str, list[str]] = {
        arm: [f"eg-scale-{arm.lower()}-{concurrency}-{index}" for index in range(concurrency)]
        for arm in ("B0", "B1", "B2")
    }
    started: list[str] = []
    level: dict[str, Any] = {"concurrency": concurrency, "batches": []}
    try:
        for arm, names in containers.items():
            for name in names:
                start_container(name)
                started.append(name)
                if arm == "B2":
                    lock_workspace(name)
            for repeat in range(3):
                level["batches"].append(_batch(arm, names, LEDGER_DIR, repeat))
            for name in names:
                stop_container(name)
                started.remove(name)
    finally:
        for name in started:
            try:
                stop_container(name)
            except (OSError, RuntimeError, TimeoutError):
                pass
    flat = [sample for batch in level["batches"] for sample in batch["samples"]]
    tokens = sum((sample.get("usage") or {}).get("total_tokens") or 0 for sample in flat)
    complete = all(len(batch["samples"]) == concurrency and batch["completed"] == concurrency for batch in level["batches"])
    return {
        "concurrency": concurrency,
        "summary": _summarize(level),
        "token_total": tokens,
        "complete": complete,
        "batches": level["batches"],
    }


def main() -> None:
    _annotate_first_attempt()
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    levels = []
    for concurrency in (2, 4, 8):
        started = time.perf_counter()
        level = _level(concurrency)
        level["wall_seconds"] = time.perf_counter() - started
        levels.append(level)
        print(
            f"c{concurrency} complete={level['complete']} tokens={level['token_total']} batches={len(level['batches'])}"
        )
        if not level["complete"]:
            break
        if level["token_total"] > 8000:
            break
    report = {
        "requested_model": "deepseek-flash",
        "task": "bug.py value() == 1",
        "replaces": "concurrency 2 block inside online_b0b1b2.json, which is marked invalid",
        "price": None,
        "price_reason": "API usage has token counts and no price field. No price was invented.",
        "levels": [{key: value for key, value in level.items() if key != "batches"} | {"sample_counts": [len(batch["samples"]) for batch in level["batches"]]} for level in levels],
        "detail": levels,
    }
    path = OUT / "online_scale.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
