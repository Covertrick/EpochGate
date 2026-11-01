"""实验 manifest。记录可以复现一个 cell 的配置，不记录密钥。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any


def cell_id(policy: str, seed: int, scenario: str | None = None) -> str:
    """生成单个配置的 id。

    输入：策略、种子、可选场景路径。
    输出：``{policy}-seed-{seed}-{场景名或 none}``。
    副作用：无。
    失败：无。
    """
    stem = "none" if not scenario else Path(scenario).stem
    return f"{policy}-seed-{seed}-{stem}"


def env_flags() -> dict[str, str]:
    """只记录相关环境变量有没有设置。

    输入：无。
    输出：``set`` 或 ``unset``。不含变量的值。
    副作用：无。
    失败：无。
    """
    names = ("EPOCHGATE_BASE_URL", "EPOCHGATE_API_KEY", "EPOCHGATE_MODEL")
    return {name: "set" if os.environ.get(name) else "unset" for name in names}


def git_commit(root: str | Path | None = None) -> str:
    """读取当前 HEAD。读不到时返回 unknown。

    输入：仓库根。
    输出：commit hash 或 ``unknown``。
    副作用：调用 git。
    失败：不抛出；git 不可用时返回 ``unknown``。
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=None if root is None else str(root),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def lock_sha256(path: str | Path) -> str:
    """计算锁文件的 sha256。

    输入：文件路径。
    输出：十六进制摘要；文件不存在时为 ``missing``。
    副作用：只读文件。
    失败：无。
    """
    target = Path(path)
    if not target.is_file():
        return "missing"
    digest = hashlib.sha256()
    digest.update(target.read_bytes())
    return digest.hexdigest()


def build_manifest(
    cells: list[dict[str, Any]],
    *,
    root: str | Path | None = None,
    exogenous_seed: int | None = None,
) -> dict[str, Any]:
    """组装一次实验的 manifest。

    输入：cell 列表、仓库根、外生计划种子。
    输出：可写成 JSON 的字典。含场景和冻结配置的哈希，不含密钥。
    副作用：读 git、``uv.lock`` 和配置文件。
    失败：无。
    """
    base = Path.cwd() if root is None else Path(root)
    fault_count = sum(int(cell.get("fault_injected_count") or 0) for cell in cells)
    return {
        "git_commit": git_commit(base),
        "lock_sha256": lock_sha256(base / "uv.lock"),
        "chaos_sha256": lock_sha256(base / "scenarios" / "chaos.yaml"),
        "frozen_sha256": lock_sha256(base / "configs" / "frozen.yaml"),
        "exogenous_seed": exogenous_seed,
        "fault_injected_count": fault_count,
        "python": platform.python_version(),
        "env": env_flags(),
        "cells": cells,
    }


def write_manifest(path: str | Path, manifest: dict[str, Any]) -> None:
    """把 manifest 写成 JSON。

    输入：路径和字典。
    输出：无。
    副作用：覆盖该文件。
    失败：目录不可写时向上抛。
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_manifest(path: str | Path) -> dict[str, Any]:
    """读取 manifest。

    输入：路径。
    输出：字典。
    副作用：只读文件。
    失败：不是 JSON 对象时抛出 ``ValueError``。
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "cells" not in payload:
        raise ValueError("manifest is missing cells")
    return payload


def find_cell(manifest: dict[str, Any], cell_id_value: str) -> dict[str, Any]:
    """按 id 取出一个 cell。

    输入：manifest 和 cell id。
    输出：cell 字典。
    副作用：无。
    失败：找不到时抛出 ``KeyError``。
    """
    for cell in manifest["cells"]:
        if cell.get("cell_id") == cell_id_value:
            return cell
    raise KeyError(f"unknown cell: {cell_id_value}")
