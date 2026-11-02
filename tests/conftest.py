"""让测试可以导入 tests/mocks，而不把假后端放进安装包。"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS = str(Path(__file__).resolve().parent)
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)
