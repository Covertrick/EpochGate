"""有 API key 时才重新生成 v4 开发集轨迹。没有 key 就停止，不编造轨迹。"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from m4_matrix import api_key, load_env  # noqa: E402


def main() -> None:
    load_env()
    if not api_key() and not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("no API key; model evaluation was not started")
    os.chdir(ROOT)
    runpy.run_path(str(ROOT / "scripts" / "coding_v4.py"), run_name="__main__")


if __name__ == "__main__":
    main()
