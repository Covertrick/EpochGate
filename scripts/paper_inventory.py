"""哈希论文资料范围内的全部文件。初次运行时阅读状态一律是 UNREAD。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "论文整理"
INVENTORY = OUT / "file-inventory.json"
LIST_MD = OUT / "00-文档全量清单.md"
ROOTS = ("docs", "results", "scripts", "packages/runtime", "packages/eg-workspace")
SKIP = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}


def tracked() -> set[str]:
    completed = subprocess.run(["git", "ls-files"], cwd=ROOT, text=True, capture_output=True, check=False)
    return {line.replace("\\", "/") for line in completed.stdout.splitlines() if line}


def stage_of(path: str) -> str:
    rules = (
        ("results/runtime/correctness/v4c-isolated", "coding-v4c"),
        ("results/runtime/correctness/v4b-historical", "coding-v4b"),
        ("results/runtime/correctness/v4b-coding", "coding-v4b"),
        ("results/runtime/correctness/v4-coding", "coding-v4"),
        ("results/runtime/correctness/v3-coding", "coding-v3"),
        ("results/runtime/correctness/v2-coding", "coding-v2"),
        ("results/runtime/correctness/v2-m5", "m5"),
        ("results/runtime/correctness/v2-m6", "m6"),
        ("results/runtime/correctness/m4", "m4"),
        ("results/runtime/correctness/m5", "m5"),
        ("results/runtime/correctness/m6", "m6"),
        ("results/runtime/correctness/e4-repeat", "e4-120"),
        ("results/runtime/correctness/after/e4", "e4-360"),
        ("results/runtime/correctness/e4-", "e4"),
        ("results/runtime/correctness/final-regression", "final-regression"),
        ("results/runtime/correctness/outbox-baseline", "outbox"),
        ("results/runtime/correctness/langgraph-boundary", "langgraph"),
        ("docs/开发文档", "dev-docs"),
        ("docs/研究", "research-docs"),
        ("docs/现行/论文", "frozen-paper"),
        ("docs/现行", "current-docs"),
        ("docs/档案", "archive"),
        ("docs/论文整理", "paper-library"),
        ("packages/runtime", "runtime"),
        ("scripts", "scripts"),
    )
    for prefix, name in rules:
        if path.startswith(prefix):
            return name
    return "other"


def title_of(path: Path) -> str:
    if path.suffix.lower() not in {".md", ".txt"}:
        return ""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()[:180]
    except OSError:
        return ""
    return ""


def files() -> list[Path]:
    found = []
    for name in ROOTS:
        root = ROOT / name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP for part in path.parts):
                continue
            if path.name == "file-inventory.json":
                continue
            found.append(path)
    return sorted(found)


def main() -> None:
    known = {}
    if INVENTORY.exists():
        previous = json.loads(INVENTORY.read_text(encoding="utf-8"))
        known = {row["path"]: row for row in previous.get("files", [])}
    in_git = tracked()
    rows = []
    for path in files():
        relative = path.relative_to(ROOT).as_posix()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        old = known.get(relative, {})
        # A prior reading judgment survives only when the bytes are unchanged.
        same = old.get("sha256") == digest
        rows.append({
            "path": relative,
            "type": path.suffix.lower() or "none",
            "sha256": digest,
            "bytes": len(data),
            "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "git_tracked": relative in in_git,
            "stage": stage_of(relative),
            "title": title_of(path),
            "role": old.get("role", "") if same else "",
            "has_raw_data": relative.startswith("results/"),
            "has_scorer": "score" in path.name or path.name in {"coding_v3_rescore.py", "evidence_index.py"},
            "paper_relevant": not relative.startswith("docs/论文整理"),
            "read_status": old.get("read_status", "UNREAD") if same else "UNREAD",
            "decision": old.get("decision", "") if same else "",
        })
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True).stdout.strip(),
        "file_count": len(rows),
        "unread": sum(1 for row in rows if row["read_status"] == "UNREAD"),
        "files": rows,
    }
    INVENTORY.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["stage"]] = counts.get(row["stage"], 0) + 1
    lines = [
        "# 文档全量清单",
        "",
        f"生成时间 `{payload['generated_at']}`。Git HEAD `{payload['git_head']}`。文件 {payload['file_count']} 个，未读 {payload['unread']} 个。",
        "",
        "阅读状态在打开正文或由审计脚本解析原始格子之后才改变。这一份清单本身不构成实验结论。",
        "",
        "## 分阶段数量",
        "",
    ]
    for name, count in sorted(counts.items()):
        lines.append(f"- `{name}`：{count}")
    lines.extend(["", "完整字段在 `docs/论文整理/file-inventory.json`。", ""])
    LIST_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"files": len(rows), "unread": payload["unread"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
