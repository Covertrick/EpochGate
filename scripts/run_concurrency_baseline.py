"""阶段 D：单任务与三任务传输基线。失败行写入 results/real/phase_d/。"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from epochgate.cli import http_settings  # noqa: E402
from epochgate.matrix_run import run_matrix_arm  # noqa: E402

OUT = ROOT / "results" / "real" / "phase_d"


def code_version() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def classify_failure(row: dict) -> str:
    blob = " ".join(str(item) for item in (row.get("backend_failures") or []))
    text = blob.lower()
    if row.get("pair_status") not in {"model_error", "request_timeout", "cancel_error", "protocol_error"}:
        if not blob:
            return "ok"
    if "timeout=true" in text or "timeoutexception" in text or "request_timeout" == row.get("pair_status"):
        return "timeout"
    if "status_code=429" in text or "rate" in text:
        return "rate_limit"
    if "closed_by_cancel=true" in text:
        return "closed_by_cancel"
    if "stream_closed" in text or "remoteprotocol" in text or "readerror" in text:
        return "connection_closed_early"
    if "status_code=" in text and "status_code=none" not in text and "status_code=200" not in text:
        return "http_status"
    if blob:
        return "other_transport"
    return "ok"


async def main_async(*, singles: int, triples: int) -> None:
    settings = http_settings()
    if settings is None:
        raise SystemExit("缺少 EPOCHGATE_BASE_URL、EPOCHGATE_API_KEY 或 EPOCHGATE_MODEL")
    base, key, model = settings
    version = code_version()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "baseline.jsonl"
    counts: dict[str, int] = {}

    for index in range(singles):
        seed = 801 + index
        row = await run_matrix_arm(
            base_url=base,
            api_key=key,
            model=model,
            seed=seed,
            arm="B",
            words=120,
            delay_s=0.5,
            concurrency=1,
            code_version=version,
        )
        row["probe"] = "single"
        row["failure_class"] = classify_failure(row)
        counts[row["failure_class"]] = counts.get(row["failure_class"], 0) + 1
        append_row(path, row)
        print(
            f"single seed={seed} status={row['pair_status']} class={row['failure_class']} "
            f"failures={row.get('backend_failures')}"
        )

    for index in range(triples):
        seed = 901 + index
        row = await run_matrix_arm(
            base_url=base,
            api_key=key,
            model=model,
            seed=seed,
            arm="B",
            words=120,
            delay_s=0.5,
            concurrency=3,
            code_version=version,
        )
        row["probe"] = "triple"
        row["failure_class"] = classify_failure(row)
        counts[row["failure_class"]] = counts.get(row["failure_class"], 0) + 1
        append_row(path, row)
        print(
            f"triple seed={seed} status={row['pair_status']} class={row['failure_class']} "
            f"failures={row.get('backend_failures')}"
        )

    summary = {
        "code_version": version,
        "singles": singles,
        "triples": triples,
        "counts": counts,
        "note": "max_inflight=1；每次请求新建 AsyncClient，不是连接池打满。",
    }
    (OUT / "baseline_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--singles", type=int, default=5)
    parser.add_argument("--triples", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main_async(singles=args.singles, triples=args.triples))


if __name__ == "__main__":
    main()
