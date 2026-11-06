"""源码 patch 的四类导出。缓存和字节码不进入 diff。"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "packages" / "contracts" / "schemas" / "patch_policy.json"
KINDS = ("VALID_PATCH", "EMPTY_PATCH", "INVALID_PATCH", "EXPORT_ERROR")


def load_policy(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or POLICY_PATH).read_text(encoding="utf-8"))


def _skipped(relative: str, policy: dict[str, Any]) -> bool:
    parts = relative.split("/")
    if relative in set(policy.get("harness_files") or []):
        return True
    skip_dirs = set(policy.get("skip_dir_names") or [])
    skip_suffixes = tuple(policy.get("skip_suffixes") or [])
    dir_suffixes = tuple(policy.get("skip_dir_suffixes") or [])
    if any(part in skip_dirs or part.endswith(dir_suffixes) for part in parts):
        return True
    return relative.endswith(skip_suffixes)


def iter_source_files(root: Path, policy: dict[str, Any]) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    if not root.exists():
        return found
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if _skipped(relative, policy):
            continue
        found[relative] = path.read_bytes()
    return found


def classify_trees(pristine: Path, work: Path, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    rules = policy or load_policy()
    try:
        before = iter_source_files(pristine, rules)
        after = iter_source_files(work, rules)
    except OSError as exc:
        return {"kind": "EXPORT_ERROR", "files": [], "reason": str(exc)}
    changed = sorted(set(before) | set(after))
    files = [name for name in changed if before.get(name) != after.get(name)]
    if not files:
        return {"kind": "EMPTY_PATCH", "files": [], "diff": ""}
    chunks: list[str] = []
    for name in files:
        old_raw = before.get(name)
        new_raw = after.get(name)
        if (old_raw is not None and b"\0" in old_raw) or (new_raw is not None and b"\0" in new_raw):
            return {"kind": "EXPORT_ERROR", "files": files, "reason": f"binary content in {name}"}
        old = [] if old_raw is None else old_raw.decode("utf-8", "replace").splitlines(keepends=True)
        new = [] if new_raw is None else new_raw.decode("utf-8", "replace").splitlines(keepends=True)
        from_name = "/dev/null" if old_raw is None else f"a/{name}"
        to_name = "/dev/null" if new_raw is None else f"b/{name}"
        body = "".join(difflib.unified_diff(old, new, fromfile=from_name, tofile=to_name))
        header = f"diff --git a/{name} b/{name}\n"
        if old_raw is None:
            header += "new file mode 100644\n"
        elif new_raw is None:
            header += "deleted file mode 100644\n"
        chunks.append(header + body)
    diff = "".join(chunks)
    if not diff.strip():
        return {"kind": "EMPTY_PATCH", "files": [], "diff": ""}
    return {"kind": "VALID_PATCH", "files": files, "diff": diff}


def with_apply_result(result: dict[str, Any], apply_ok: bool, apply_error: str = "") -> dict[str, Any]:
    if result.get("kind") != "VALID_PATCH":
        return result
    if apply_ok:
        return result
    rejected = dict(result)
    rejected["kind"] = "INVALID_PATCH"
    rejected["reason"] = apply_error or "patch does not apply"
    return rejected
