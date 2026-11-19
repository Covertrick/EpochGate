"""版本检查和幂等插入放在同一个 SQLite 事务里。不是 EpochGate 的 Claim。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Outbox:
    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute("CREATE TABLE version (id INTEGER PRIMARY KEY, token INTEGER)")
        self.conn.execute("INSERT INTO version (id, token) VALUES (1, 1)")
        self.conn.execute(
            "CREATE TABLE effect (key TEXT PRIMARY KEY, payload TEXT, receipt TEXT)"
        )
        self.conn.commit()

    def bump(self) -> None:
        self.conn.execute("UPDATE version SET token = token + 1 WHERE id = 1")
        self.conn.commit()

    def commit(self, key: str, payload: str, token: int) -> dict:
        digest = sha256_text(payload)
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            current = self.conn.execute("SELECT token FROM version WHERE id = 1").fetchone()[0]
            if current != token:
                self.conn.rollback()
                return {"status": "rejected", "reason": "version"}
            row = self.conn.execute("SELECT payload, receipt FROM effect WHERE key = ?", (key,)).fetchone()
            if row is not None:
                self.conn.rollback()
                if row[0] != payload:
                    return {"status": "conflict", "receipt": row[1]}
                return {"status": "replayed", "receipt": row[1]}
            self.conn.execute("INSERT INTO effect (key, payload, receipt) VALUES (?, ?, ?)", (key, payload, digest))
            self.conn.commit()
            return {"status": "accepted", "receipt": digest}
        except sqlite3.Error:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()


def run(root: Path) -> dict:
    box = Outbox(root / "outbox.sqlite")
    same = box.commit("k", "order 3", 1)
    replay = box.commit("k", "order 3", 1)
    conflict = box.commit("k", "order 0", 1)
    box.bump()
    stale = box.commit("k2", "order 3", 1)
    fresh = box.commit("k2", "order 0", 2)
    box.close()
    return {
        "same_key_same_payload": same["status"],
        "replay": replay["status"],
        "different_payload": conflict["status"],
        "stale_token": stale["status"],
        "fresh_token": fresh["status"],
        "claim_to_bytes": False,
        "notice_missing_inferred": False,
        "external_exactly_once": False,
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "results" / "runtime" / "correctness" / "outbox-baseline"
    root.mkdir(parents=True, exist_ok=True)
    report = run(root)
    (root / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
