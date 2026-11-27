"""Read models for the local execution observatory."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

from epochgate_runtime.sessions import _SKIP_DIRS, _refused


def workspace_preview(workspace: str) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve()
    if not workspace.strip() or not root.is_dir():
        raise ValueError("仓库目录不存在。")
    paths = []
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = sorted(name for name in folders if name not in _SKIP_DIRS and not name.startswith('.'))
        for name in sorted(files):
            relative = (Path(directory) / name).relative_to(root).as_posix()
            if name.startswith('.') or _refused(relative):
                continue
            if len(paths) == 100:
                return {"workspace": str(root), "files": paths, "truncated": True}
            paths.append(relative)
    return {"workspace": str(root), "files": paths, "truncated": False}


def evidence(runtime, run_id: str, events: list[dict], effects: list[dict]) -> dict:
    with runtime.ledger._lock:
        conn = runtime.ledger._conn
        attempts = [dict(zip(("attempt_id", "session_id", "fencing_token", "phase"), row)) for row in conn.execute(
            "SELECT attempt_id, session_id, fencing_token, phase FROM attempts WHERE run_id = ? ORDER BY rowid", (run_id,)
        )]
        metadata = {row[0]: dict(zip(("effect_id", "attempt_id", "arguments_hash", "fencing_token"), row[1:]))
                    for row in conn.execute("SELECT idempotency_key, effect_id, attempt_id, arguments_hash, fencing_token FROM effects WHERE run_id = ?", (run_id,))}
        event_attempts = dict(conn.execute("SELECT seq, attempt_id FROM runtime_events WHERE run_id = ? AND attempt_id IS NOT NULL", (run_id,)))
    attempts_by_token: dict[int, list[str]] = {}
    for attempt in attempts:
        attempts_by_token.setdefault(attempt["fencing_token"], []).append(attempt["attempt_id"])
    indexed: dict[str, list[dict]] = {}
    for event in events:
        if event["seq"] in event_attempts:
            event["attempt_id"] = event_attempts[event["seq"]]
        key = event["payload"].get("idempotency_key")
        if isinstance(key, str):
            indexed.setdefault(key, []).append(event)
    receipts = []
    for effect in effects:
        key = effect["idempotency_key"]
        effect.update(metadata.get(key, {}))
        effect_id = effect.get("effect_id")
        effect["log_id"] = effect_id if effect_id and Path(effect_id).name == effect_id and (runtime.data_dir / "logs" / f"{effect_id}.log").is_file() else None
        candidates = attempts_by_token.get(effect.get("fencing_token"), [])
        if effect.get("attempt_id"):
            effect["attempt_source"] = "effect_row"
        elif len(candidates) == 1:
            effect["attempt_id"] = candidates[0]
            effect["attempt_source"] = "unique_run_fencing_token"
        receipt = effect.get("receipt")
        if not receipt:
            continue
        sink_unavailable = False
        try:
            remote = runtime.sink.get(key)
        except sqlite3.Error:
            remote = None
            sink_unavailable = True
        matches = [event for event in indexed.get(key, []) if event["payload"].get("receipt") == receipt]
        confirmed = next((event for event in reversed(matches) if event["payload"].get("receipt_verified") is True), None)
        status = "NOT_OBSERVED"
        source = "run_ledger"
        if confirmed:
            status, source = "VERIFIED", "publish_readback"
            effect["payload_hash"] = confirmed["payload"].get("sha256")
        elif sink_unavailable:
            status, source = "NOT_AVAILABLE", "sqlite_sink"
        elif remote:
            status, source = ("VERIFIED" if remote["receipt"] == receipt else "MISMATCH"), "sqlite_sink"
            if status == "VERIFIED" and isinstance(remote.get("payload"), str):
                effect["payload_hash"] = hashlib.sha256(remote["payload"].encode()).hexdigest()
        receipts.append({"idempotency_key": key, "receipt_id": receipt, "status": status, "source": source,
                         "target_record": remote if source == "sqlite_sink" else confirmed["payload"] if confirmed else None,
                         "verification_event": confirmed["seq"] if confirmed else None,
                         "verified_at": confirmed["recorded_at"] if confirmed else None,
                         "event_seqs": [event["seq"] for event in matches],
                         "reason": "RECEIPT_MISMATCH" if status == "MISMATCH" else None})
    return {"attempts": attempts, "receipts": receipts}


def summarize(row: dict, events: list[dict], effects: list[dict], receipts: list[dict]) -> dict:
    created = next((event for event in events if event["event_type"] == "run_created"), None)
    outcome = next((event["payload"] for event in reversed(events)
                    if event["event_type"] in {"demo_finished", "agent_turn_finished"}), {})
    prefix = row["run_id"].split("-")[0]
    scenario = (created or {}).get("payload", {}).get("scenario") or {
        "token": "stale-write", "demo": "stale-write", "key": "idempotency-conflict", "idem": "idempotency-conflict", "crash": "crash-window",
        "walk": "walkthrough", "cancel": "cancellation", "sess": "workspace", "fix": "corrected-effect",
    }.get(prefix, "runtime")
    scenario = {"crash-recovery": "crash-window"}.get(scenario, scenario)
    statuses = Counter(item["status"] for item in receipts)
    rejected = sum(event["event_type"] in {"commit_rejected", "result_withheld"} for event in events)
    unknown = sum(effect["effect_state"] in {"OUTCOME_UNKNOWN", "UNKNOWN"} for effect in effects)
    publications = [event["payload"]["idempotency_key"] for event in events
                    if event["event_type"] == "file_published" and event["payload"].get("receipt_verified") is True
                    and event["payload"].get("recovered") != "receipt" and event["payload"].get("idempotency_key")]
    duplicate_publications = len(publications) - len(set(publications)) if publications else None
    return {"created_at": created["recorded_at"] if created else None,
            "last_event_at": events[-1]["recorded_at"] if events else None,
            "scenario": scenario, "task": (created or {}).get("payload", {}).get("task"),
            "task_title": (created or {}).get("payload", {}).get("title"),
            "repository": (created or {}).get("payload", {}).get("repository"),
            "git_commit": (created or {}).get("payload", {}).get("git_commit"),
            "execution_status": outcome.get("status"), "execution_seconds": outcome.get("duration_seconds"),
            "recovered": any(event["event_type"] == "reconciled_from_sink" for event in events),
            "confirmed": statuses["VERIFIED"], "receipt_mismatches": statuses["MISMATCH"],
            "rejected_submissions": rejected, "unknown_effects": unknown,
            "receipt_status": "MISMATCH" if statuses["MISMATCH"] else "VERIFIED" if receipts and not unknown and statuses["VERIFIED"] == len(receipts) else "PARTIAL" if statuses["VERIFIED"] else "NOT_OBSERVED",
            "effect_count": len(effects), "duplicate_publications": duplicate_publications}


class ResearchArchive:
    def __init__(self, repo: Path | None):
        self.repo = repo
        self.root = repo / "results/experiments/effect-history/pilot-01" if repo else None

    def data(self) -> dict:
        if self.root is None or not (self.root / "report.json").is_file():
            raise FileNotFoundError("当前安装中没有实验归档。")
        return {name: json.loads((self.root / f"{name}.json").read_bytes()) for name in ("report", "manifest", "sha256")}

    def artifact(self, name: str) -> Path:
        data = self.data()
        if name not in data["sha256"] and name != "sha256.json":
            raise FileNotFoundError("归档中没有该证据。")
        path = (self.root / name).resolve(strict=True)
        if not path.is_relative_to(self.root.resolve()):
            raise FileNotFoundError("归档中没有该证据。")
        return path

    def protocol(self, expected: str | None = None) -> bytes:
        name = "experiments/effect_history/PROTOCOL.md"
        if expected is None:
            expected = self.data()["manifest"]["sources"].get(name)
        content = (self.repo / name).read_bytes()
        lf = content.replace(b"\r\n", b"\n")
        # Preserve the exact archived representation across Git's Windows checkouts.
        return lf if hashlib.sha256(lf).hexdigest() == expected else content

    def overview(self) -> dict:
        data = self.data()
        missing, mismatched = [], []
        for name, expected in data["sha256"].items():
            path = (self.root / name).resolve()
            if not path.is_relative_to(self.root.resolve()) or not path.is_file():
                missing.append(name)
            elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                mismatched.append(name)
        command = ["git", "log", "-1", "--format=%H", "--", "results/experiments/effect-history/pilot-01"]
        try:
            commit = subprocess.run(command, cwd=self.repo, capture_output=True, text=True, timeout=5).stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            commit = None
        expected = data["manifest"]["sources"].get("experiments/effect_history/PROTOCOL.md")
        try:
            protocol_matches = hashlib.sha256(self.protocol(expected)).hexdigest() == expected
        except FileNotFoundError:
            protocol_matches = False
        return {**data, "archive_commit": commit, "protocol_sha256": expected,
                "protocol_matches": protocol_matches,
                "integrity": {"files": len(data["sha256"]), "missing": missing, "mismatched": mismatched}}

    def audit(self) -> dict:
        integrity = self.overview()["integrity"]
        if integrity["missing"] or integrity["mismatched"]:
            raise ValueError("归档文件缺失或 SHA-256 不一致。")
        command = [sys.executable, "-m", "effect_history.run", "audit", str(self.root)]
        completed = subprocess.run(command, cwd=self.repo / "experiments", capture_output=True, encoding="utf-8", errors="replace", timeout=45)
        return {"status": "VERIFIED" if completed.returncode == 0 else "AUDIT_FAILED",
                "result": json.loads(completed.stdout) if completed.returncode == 0 else None,
                "error": completed.stderr[-2000:] if completed.returncode else None}

    def cell(self, cell_id: str) -> dict:
        data = self.data()
        row = next((row for row in data["report"]["rows"] if row["id"] == cell_id), None)
        if row is None:
            raise FileNotFoundError("归档中没有该实验格。")
        database = self.artifact(f"{cell_id}/target.sqlite")
        trace_path = self.artifact(f"{cell_id}/trace.json")
        for path in (database, trace_path):
            name = f"{cell_id}/{path.name}"
            if hashlib.sha256(path.read_bytes()).hexdigest() != data["sha256"][name]:
                raise ValueError(f"Evidence hash mismatch: {name}")
        tables = ("effects", "runtime_events") if row["arm"] == "file" else ("target_effects", "target_attempts", "receiver_authority")
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            ledger = {table: [{key: _json_value(item[key]) for key in item.keys()} for item in conn.execute(f"SELECT * FROM {table}")] for table in tables}
        trace = json.loads(trace_path.read_bytes())
        return {"row": row, "trace": trace, "ledger": ledger,
                "artifacts": {name: digest for name, digest in data["sha256"].items() if name.startswith(f"{cell_id}/")}}


def _json_value(value):
    return {"hex": value.hex(), "text": value.decode("utf-8", errors="replace")} if isinstance(value, bytes) else value
