"""检查 Agent 可见视图里有没有密钥、gold 或未来提交。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from epochgate.eg2_audit import audit_gold_leakage


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    view = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else _sample()
    errors = audit_gold_leakage(view)
    if errors:
        print("\n".join(errors))
        sys.exit(1)
    print("leakage_ok")


def _sample() -> dict:
    return {
        "prompt": "Fix the issue described by the public statement.",
        "mounts": ["/workspace/repo"],
        "env": {},
        "tool_dirs": ["/opt/eg/tools"],
        "retrieval": [],
        "git_objects": ["a" * 40],
        "future_commits": ["b" * 40],
    }


if __name__ == "__main__":
    main()
