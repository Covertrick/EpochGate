"""Architectural guard: only gateway.py may call environment.commit."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "epochgate"

# Actual call sites, not documentation mentions.
_CALL = re.compile(r"""(?:self\._env|env|environment)\.commit\s*\(""")


def test_only_gateway_calls_env_commit() -> None:
    offenders: list[str] = []
    for path in ROOT.glob("*.py"):
        if path.name in {"environment.py", "gateway.py"}:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _CALL.search(stripped):
                offenders.append(f"{path.name}:{i}:{stripped}")
    assert offenders == []


def test_scheduler_does_not_import_gateway_or_environment() -> None:
    text = (ROOT / "scheduler.py").read_text(encoding="utf-8")
    assert "from epochgate.gateway" not in text
    assert "import epochgate.gateway" not in text
    assert "from epochgate.environment" not in text
    assert "import epochgate.environment" not in text
    assert "from epochgate.backend" not in text
    assert "import epochgate.backend" not in text
