"""只读阶段一原始行，重算汇总并与已冻结 summary 比对。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from epochgate.matrix_summary import choose_branch, summarize_cell  # noqa: E402

CELLS = [
    ("A", "w300_d0.1", 300, 0.1, 1),
    ("A", "w300_d0.5", 300, 0.5, 1),
    ("A", "w300_d1.5", 300, 1.5, 1),
    ("B", "w50_d0.5", 50, 0.5, 1),
    ("B", "w800_d0.5", 800, 0.5, 1),
    ("C", "w300_d0.5_x3", 300, 0.5, 3),
]


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def build_summary(rows: list[dict]) -> dict:
    cells = []
    for group, cell_id, words, delay, concurrency in CELLS:
        chosen = [
            row
            for row in rows
            if row.get("cell_id") == cell_id
            or (
                row.get("target_words") == words
                and row.get("trigger_delay_s") == delay
                and row.get("concurrency") == concurrency
            )
        ]
        summary = summarize_cell(chosen, planned_pairs=5)
        summary["cell_id"] = cell_id
        summary["group"] = group
        cells.append(summary)
    return {
        "prompt_version": "phase1-words-v1",
        "code_version": rows[0].get("code_version") if rows else "unknown",
        "deadline_s": 120.0,
        "timeout_s": 180.0,
        "branch": choose_branch(cells),
        "cells": cells,
    }


def _close(left, right, tol: float = 1e-9) -> bool:
    if left is None and right is None:
        return True
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= tol
    return left == right


def compare(expected: dict, actual: dict) -> list[str]:
    problems = []
    if expected.get("branch") != actual.get("branch"):
        problems.append(f"branch {expected.get('branch')} != {actual.get('branch')}")
    by_id = {cell["cell_id"]: cell for cell in actual.get("cells", [])}
    for cell in expected.get("cells", []):
        other = by_id.get(cell["cell_id"])
        if other is None:
            problems.append(f"missing cell {cell['cell_id']}")
            continue
        for key, value in cell.items():
            if key == "group":
                continue
            if not _close(value, other.get(key)):
                problems.append(f"{cell['cell_id']}.{key}: {value!r} != {other.get(key)!r}")
    return problems


def write_row_index(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for index, row in enumerate(rows):
            cell_id = row.get("cell_id")
            seed = row.get("seed")
            arm = row.get("arm")
            record = {
                "line": index + 1,
                "pair_id": f"{cell_id}:{seed}",
                "request_id": f"{cell_id}:{seed}:{arm}",
                "run_key": f"phase1:{cell_id}:{seed}:{arm}",
                "cell_id": cell_id,
                "seed": seed,
                "arm": arm,
                "target_attempt_id": row.get("target_attempt_id"),
                "pair_status": row.get("pair_status"),
                "code_version": row.get("code_version"),
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        type=Path,
        default=ROOT / "results" / "real" / "matrix.jsonl",
    )
    parser.add_argument(
        "--expected",
        type=Path,
        default=ROOT / "results" / "real" / "matrix_summary.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "results" / "archive" / "phase1" / "matrix_summary.recomputed.json",
    )
    parser.add_argument(
        "--row-index",
        type=Path,
        default=ROOT / "results" / "archive" / "phase1" / "row_index.jsonl",
    )
    args = parser.parse_args()
    rows = load_rows(args.matrix)
    summary = build_summary(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_row_index(rows, args.row_index)
    print(f"rows={len(rows)} branch={summary['branch']} wrote={args.out}")
    if args.expected.is_file():
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        problems = compare(expected, summary)
        if problems:
            print("MISMATCH")
            for item in problems:
                print(item)
            raise SystemExit(1)
        print("matches results/real/matrix_summary.json")


if __name__ == "__main__":
    main()
