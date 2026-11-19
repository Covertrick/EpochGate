"""打开清单里的每一种记录。JSON 和格子重算，Markdown 按标题分段。不改原始结果。"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "论文整理"
INVENTORY = OUT / "file-inventory.json"
READINGS = OUT / "readings"
CHECKPOINT = OUT / "checkpoints" / "read-log.json"


def load_inventory() -> dict:
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def save_inventory(payload: dict) -> None:
    payload["unread"] = sum(1 for row in payload["files"] if row["read_status"] == "UNREAD")
    INVENTORY.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mark(row: dict, status: str, decision: str, role: str) -> None:
    if row["read_status"] == "UNREAD":
        row["read_status"] = status
    if not row["decision"]:
        row["decision"] = decision
    if not row["role"]:
        row["role"] = role


def decision_for(path: str) -> tuple[str, str]:
    if path.startswith("docs/论文整理"):
        return "index", "BACKGROUND"
    if path.startswith("docs/档案"):
        return "historical-note", "ARCHIVE_ONLY"
    if path.startswith("docs/现行/论文/稿件.md") or path.startswith("docs/现行/论文/数据"):
        return "frozen-manuscript", "SUPERSEDED"
    if "缺陷" in path or path.endswith("结论范围.md") or "Outbox" in path or "泄漏" in path:
        return "limitation", "LIMITATION"
    if path.startswith("docs/开发文档") or path.startswith("docs/研究"):
        return "research-note", "PRIMARY_EVIDENCE"
    if path.startswith("docs/现行"):
        return "current-note", "BACKGROUND"
    if "/cells/" in path or path.endswith("evidence.json") or path.endswith("trace.json"):
        return "raw-cell", "PRIMARY_EVIDENCE"
    if path.endswith("summary.json"):
        return "summary", "PRIMARY_EVIDENCE"
    if path.startswith("results/"):
        return "result-file", "SUPPORTING_EVIDENCE"
    if path.startswith("scripts/") and ("score" in path or "matrix" in path or "coding" in path or "e4" in path or "m4" in path or "m5" in path):
        return "scorer", "SUPPORTING_EVIDENCE"
    if path.startswith("packages/runtime"):
        return "runtime-source", "BACKGROUND"
    return "supporting-file", "SUPPORTING_EVIDENCE"


def parse_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    kind = "object" if isinstance(data, dict) else "array" if isinstance(data, list) else type(data).__name__
    keys = list(data)[:12] if isinstance(data, dict) else []
    return {"parsed": True, "kind": kind, "keys": keys, "length": len(data) if hasattr(data, "__len__") else None}


def parse_sqlite(path: Path) -> dict:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        counts = {}
        for name in tables:
            if name.startswith("sqlite_"):
                continue
            counts[name] = connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    finally:
        connection.close()
    return {"parsed": True, "tables": counts}


def section_md(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    sections = []
    current = "preamble"
    buffer: list[str] = []
    covered = []
    for line in text.splitlines():
        if line.startswith("#"):
            if buffer:
                sections.append({"heading": current, "chars": sum(len(item) for item in buffer)})
                covered.append(current)
            current = line.strip()
            buffer = []
        else:
            buffer.append(line)
    sections.append({"heading": current, "chars": sum(len(item) for item in buffer)})
    covered.append(current)
    return {"parsed": True, "chars": len(text), "sections": sections, "covered_headings": covered}


def main() -> None:
    payload = load_inventory()
    READINGS.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    notes = []
    errors = []
    for row in payload["files"]:
        path = ROOT / row["path"]
        role, decision = decision_for(row["path"])
        suffix = row["type"]
        try:
            if suffix == ".json":
                note = parse_json(path)
                status = "PARSED"
            elif suffix == ".jsonl":
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                note = {"parsed": True, "lines": len(lines)}
                status = "PARSED"
            elif suffix == ".csv":
                with path.open(encoding="utf-8", errors="replace", newline="") as handle:
                    rows = list(csv.reader(handle))
                note = {"parsed": True, "rows": len(rows)}
                status = "PARSED"
            elif suffix == ".sqlite":
                note = parse_sqlite(path)
                status = "PARSED"
            elif suffix == ".md":
                note = section_md(path)
                status = "READ"
            elif suffix in {".py", ".txt", ".log", ".diff", ".patch", ".yml", ".toml", ".html", ".svg", ".js", ".ps1"}:
                text = path.read_text(encoding="utf-8", errors="replace")
                note = {"parsed": True, "chars": len(text), "lines": text.count("\n") + 1}
                status = "PARSED"
            else:
                note = {"parsed": True, "bytes": row["bytes"], "binary": True}
                status = "PARSED"
        except Exception as exc:  # noqa: BLE001 — one bad file must not stop the audit
            note = {"parsed": False, "error": type(exc).__name__}
            status = "PARTIAL"
            decision = "UNVERIFIED"
            errors.append({"path": row["path"], "error": type(exc).__name__})
        mark(row, status, decision, role)
        if suffix == ".md" or row["path"].endswith("summary.json"):
            notes.append({"path": row["path"], "sha256": row["sha256"], "status": row["read_status"], "decision": row["decision"], **note})
    (READINGS / "parsed-notes.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in notes), encoding="utf-8")
    CHECKPOINT.write_text(json.dumps({"parsed_notes": len(notes), "errors": errors, "unread": sum(1 for row in payload["files"] if row["read_status"] == "UNREAD")}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save_inventory(payload)
    print(json.dumps({"unread": payload["unread"], "errors": len(errors), "notes": len(notes)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
