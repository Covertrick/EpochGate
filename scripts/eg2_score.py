"""对一份工作区做 patch 分类，并在干净树上检查能否应用。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from epochgate.eg2_patch import classify_trees, with_apply_result

ROOT = Path(__file__).resolve().parents[1]


def score_dirs(pristine: Path, work: Path) -> dict:
    classified = classify_trees(pristine, work)
    if classified["kind"] != "VALID_PATCH":
        return classified
    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn"],
        cwd=pristine,
        input=classified["diff"].encode("utf-8"),
        capture_output=True,
    )
    return with_apply_result(classified, check.returncode == 0, check.stderr.decode("utf-8", "replace")[-500:])


def main() -> None:
    if len(sys.argv) != 3:
        print(json.dumps({"kind": "EXPORT_ERROR", "reason": "usage: eg2_score.py pristine work"}))
        raise SystemExit(2)
    result = score_dirs(Path(sys.argv[1]), Path(sys.argv[2]))
    print(json.dumps({key: result[key] for key in ("kind", "files") if key in result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
