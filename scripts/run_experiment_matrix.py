"""阶段一矩阵。每对内部随机决定 A/B 顺序，原始行追加写入。"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from epochgate.cli import http_settings  # noqa: E402
from epochgate.matrix_run import DEADLINE_S, PROMPT_VERSION, TIMEOUT_S, run_matrix_arm  # noqa: E402
from epochgate.matrix_summary import choose_branch, summarize_cell  # noqa: E402

CELLS = [
    ("A", 300, 0.1, 1, 101),
    ("A", 300, 0.5, 1, 106),
    ("A", 300, 1.5, 1, 111),
    ("B", 50, 0.5, 1, 201),
    ("B", 800, 0.5, 1, 301),
    ("C", 300, 0.5, 3, 401),
]


def code_version() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def load_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def already(rows: list[dict], *, seed: int, arm: str, version: str) -> bool:
    return any(
        row.get("seed") == seed and row.get("arm") == arm and row.get("code_version") == version
        for row in rows
    )


async def run_preflight(settings: tuple[str, str, str], version: str, path: Path) -> list[dict]:
    base, key, model = settings
    rows = []
    for words in (50, 300, 800):
        row = await run_matrix_arm(
            base_url=base,
            api_key=key,
            model=model,
            seed=words,
            arm="A",
            words=words,
            delay_s=-1,
            concurrency=1,
            code_version=version,
        )
        row["preflight"] = True
        row["near_deadline"] = (row.get("end_to_end_latency_s") or 0) >= DEADLINE_S * 0.9
        row["include_in_matrix"] = row["pair_status"] != "request_timeout" and not row["near_deadline"]
        if words == 800 and (row["near_deadline"] or row["pair_status"] == "request_timeout"):
            row["include_in_matrix"] = False
        rows.append(row)
        append_row(path, row)
        print(
            f"preflight words={words} status={row['pair_status']} "
            f"e2e={row['end_to_end_latency_s']} actual_words={row['actual_words_before_tool']} "
            f"include={row['include_in_matrix']}"
        )
    return rows


async def run_cells(settings: tuple[str, str, str], version: str, path: Path, names: set[str]) -> None:
    base, key, model = settings
    rows = load_rows(path)
    foreign = [row for row in rows if row.get("code_version") not in {None, version}]
    if foreign:
        raise SystemExit(f"{path} 含有其他代码版本的行，请换一个输出文件")
    for name, words, delay, concurrency, seed0 in CELLS:
        if names and name not in names:
            continue
        if name == "B" and words == 800 and not _include_800():
            print("skip B 800: preflight said it crowds the deadline")
            continue
        for offset in range(5):
            seed = seed0 + offset
            order = ["A", "B"]
            if random.Random(seed).random() < 0.5:
                order = ["B", "A"]
            for arm in order:
                if already(rows, seed=seed, arm=arm, version=version):
                    continue
                row = await run_matrix_arm(
                    base_url=base,
                    api_key=key,
                    model=model,
                    seed=seed,
                    arm=arm,
                    words=words,
                    delay_s=delay,
                    concurrency=concurrency,
                    code_version=version,
                )
                row["arm_order"] = "-then-".join(order)
                append_row(path, row)
                rows.append(row)
                print(
                    f"{row['cell_id']} seed={seed} arm={arm} status={row['pair_status']} "
                    f"pre={row['pre_revoke_local_deltas']} post={row['post_revoke_local_deltas']} "
                    f"replan={row['replan_local_deltas']}"
                )
        cell_rows = [
            row
            for row in rows
            if row.get("target_words") == words
            and row.get("trigger_delay_s") == delay
            and row.get("concurrency") == concurrency
            and row.get("code_version") == version
        ]
        summary = summarize_cell(cell_rows, planned_pairs=5)
        summary["cell_id"] = cell_rows[0]["cell_id"] if cell_rows else f"{name}-{words}"
        print(json.dumps(summary, ensure_ascii=False))


def _include_800() -> bool:
    path = ROOT / "results" / "real" / "preflight.jsonl"
    for row in load_rows(path):
        if row.get("target_words") == 800:
            return bool(row.get("include_in_matrix"))
    return True


def write_summary(path: Path, version: str, out: Path) -> str:
    rows = [row for row in load_rows(path) if row.get("code_version") == version and not row.get("preflight")]
    cells = []
    seen = []
    for name, words, delay, concurrency, _seed0 in CELLS:
        chosen = [
            row
            for row in rows
            if row.get("target_words") == words
            and row.get("trigger_delay_s") == delay
            and row.get("concurrency") == concurrency
        ]
        if not chosen:
            continue
        summary = summarize_cell(chosen, planned_pairs=5)
        summary["cell_id"] = chosen[0]["cell_id"]
        summary["group"] = name
        cells.append(summary)
        seen.append(summary["cell_id"])
    branch = choose_branch(cells)
    payload = {
        "prompt_version": PROMPT_VERSION,
        "code_version": version,
        "deadline_s": DEADLINE_S,
        "timeout_s": TIMEOUT_S,
        "branch": branch,
        "cells": cells,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"branch={branch} cells={seen}")
    return branch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--cells", default="A,B,C")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    settings = http_settings()
    if settings is None:
        raise SystemExit("缺少 EPOCHGATE_BASE_URL、EPOCHGATE_API_KEY 或 EPOCHGATE_MODEL")
    version = code_version()
    if args.preflight:
        asyncio.run(run_preflight(settings, version, ROOT / "results" / "real" / "preflight.jsonl"))
        return
    matrix = ROOT / "results" / "real" / "matrix.jsonl"
    if args.summary:
        write_summary(matrix, version, ROOT / "results" / "real" / "matrix_summary.json")
        return
    names = {item.strip() for item in args.cells.split(",") if item.strip()}
    asyncio.run(run_cells(settings, version, matrix, names))
    write_summary(matrix, version, ROOT / "results" / "real" / "matrix_summary.json")


if __name__ == "__main__":
    main()
