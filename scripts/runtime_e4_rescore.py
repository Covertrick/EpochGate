"""从旧 360 格写出补充状态向量。不改格子，也不改原来的终态正确率。"""

from __future__ import annotations

import json
from pathlib import Path

from e4_audit import build_report, load_cells

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results" / "runtime" / "correctness" / "after" / "e4" / "cells"
DESTINATION = ROOT / "results" / "runtime" / "correctness" / "e4-audit" / "rescore.json"


def main() -> None:
    report = build_report(load_cells(SOURCE))
    report["source"] = str(SOURCE)
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scored": report["scored"], "by_arm": report["by_arm"], "tokens": report["stored_token_sums"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
