"""用固定镜像重跑 v3 冻结补丁的容器评分。这不是在线模型复现。"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
runpy.run_module("coding_v3_rescore", run_name="__main__")
