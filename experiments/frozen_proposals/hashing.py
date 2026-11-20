"""Canonical hashes for frozen proposals and fairness checks."""

from __future__ import annotations

import hashlib
import json
import platform
import sqlite3
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_obj(obj: Any) -> str:
    return sha256_bytes(canonical_bytes(obj))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


_PACKAGES = (
    "pytest",
    "pydantic",
    "epochgate-runtime",
    "aiosqlite",
    "fastapi",
    "uvicorn",
    "httpx",
    "typer",
    "tiktoken",
    "scikit-learn",
    "pytest-asyncio",
    "hypothesis",
)


def environment_record() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for name in _PACKAGES:
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "ABSENT"
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "packages": packages,
    }


def environment_hash() -> str:
    """SHA256 of python version, platform, sqlite version, and pinned package versions."""
    return sha256_obj(environment_record())


def git_head(repo: Path) -> str:
    out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True)
    return out.strip()


def implementation_hash(week2: Path) -> str:
    """SHA256 of Week 2 Python sources. Git HEAD does not contain this tree."""
    digest = hashlib.sha256()
    files = sorted(
        path for path in week2.rglob("*.py") if "__pycache__" not in path.parts
    )
    for path in files:
        digest.update(path.relative_to(week2).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
