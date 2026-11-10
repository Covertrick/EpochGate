"""执行控制层。

DSH 保存模型交互。这里保存不能随意重放的外部执行意图、工具结果和提交资格。
客户端停止读取不表示 GPU 已回收。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class ControlError(RuntimeError):
    pass


PHASES = (
    "created",
    "running",
    "checkpointed",
    "cancel_requested",
    "stopping",
    "stopped",
    "commit_pending",
    "committed",
    "rejected",
    "failed",
    "unknown",
    "reconciled",
)

_TRANSITIONS = {
    "created": {"running"},
    "running": {"checkpointed", "cancel_requested", "commit_pending", "failed"},
    "checkpointed": {"running", "cancel_requested", "failed"},
    "cancel_requested": {"stopping"},
    "stopping": {"stopped", "unknown"},
    "stopped": {"reconciled"},
    "commit_pending": {"committed", "rejected"},
    "committed": {"reconciled"},
    "rejected": {"reconciled"},
    "failed": {"reconciled"},
    "unknown": {"reconciled"},
    "reconciled": set(),
}


def transition(current: str, target: str) -> str:
    if target not in _TRANSITIONS.get(current, set()):
        raise ControlError(f"illegal transition {current} -> {target}")
    return target


@dataclass
class CancelRecord:
    eligibility_revoked: bool = False
    executor_acked: bool | None = None
    cancellation_confirmed: bool = False
    slot_released: bool = False
    gpu_reclaimed: bool = False


@dataclass
class RunState:
    run_id: str
    phase: str = "created"
    fencing_token: int = 1
    state_version: int = 1
    eligible: bool = True
    cancel: CancelRecord = field(default_factory=CancelRecord)


class RunLedger:
    """工具副作用账本。同一把锁里核对 fencing token 和幂等键。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path or ":memory:"), check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                phase TEXT NOT NULL,
                fencing_token INTEGER NOT NULL,
                state_version INTEGER NOT NULL,
                eligible INTEGER NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS effects (
                idempotency_key TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                fencing_token INTEGER NOT NULL,
                phase TEXT NOT NULL,
                result TEXT,
                result_hash TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cancels (
                run_id TEXT PRIMARY KEY,
                eligibility_revoked INTEGER NOT NULL,
                executor_acked INTEGER,
                slot_released INTEGER NOT NULL,
                gpu_reclaimed INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def create_run(self, run_id: str) -> RunState:
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (run_id, phase, fencing_token, state_version, eligible) VALUES (?, 'created', 1, 1, 1)",
                (run_id,),
            )
            self._conn.commit()
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunState:
        row = self._conn.execute(
            "SELECT phase, fencing_token, state_version, eligible FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        state = RunState(
            run_id=run_id,
            phase=row[0],
            fencing_token=row[1],
            state_version=row[2],
            eligible=bool(row[3]),
        )
        cancel = self._conn.execute(
            "SELECT eligibility_revoked, executor_acked, slot_released, gpu_reclaimed FROM cancels WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if cancel is not None:
            state.cancel = CancelRecord(
                eligibility_revoked=bool(cancel[0]),
                executor_acked=None if cancel[1] is None else bool(cancel[1]),
                cancellation_confirmed=cancel[1] == 1,
                slot_released=bool(cancel[2]),
                gpu_reclaimed=bool(cancel[3]),
            )
        return state

    def _save_phase(self, state: RunState) -> None:
        self._conn.execute(
            "UPDATE runs SET phase = ?, fencing_token = ?, state_version = ?, eligible = ? WHERE run_id = ?",
            (state.phase, state.fencing_token, state.state_version, int(state.eligible), state.run_id),
        )
        self._conn.commit()

    def advance(self, run_id: str, target: str) -> RunState:
        with self._lock:
            state = self.get_run(run_id)
            state.phase = transition(state.phase, target)
            self._save_phase(state)
            return state

    def revoke(self, run_id: str) -> RunState:
        """抬高 fencing token。之后携带旧 token 的提交不能产生副作用。"""
        with self._lock:
            state = self.get_run(run_id)
            if state.phase == "committed":
                return state
            state.eligible = False
            state.fencing_token += 1
            state.state_version += 1
            if state.phase == "created":
                state.phase = "running"
            if state.phase in {"running", "checkpointed"}:
                state.phase = transition(state.phase, "cancel_requested")
            state.cancel.eligibility_revoked = True
            self._save_phase(state)
            self._write_cancel(state)
            return self.get_run(run_id)

    def execute(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        fencing_token: int,
        state_version: int,
        effect: Callable[[], str],
    ) -> dict[str, Any]:
        with self._lock:
            state = self.get_run(run_id)
            if not state.eligible or fencing_token != state.fencing_token or state_version != state.state_version:
                return {"status": "rejected", "applied": False, "reason": "stale_or_unauthorized"}
            existing = self._conn.execute(
                "SELECT phase, result FROM effects WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None and existing[0] == "completed":
                return {"status": "replayed", "applied": False, "result": existing[1]}
            if existing is not None:
                reason = "result_not_durable" if existing[0] in {"started", "unknown"} else "effect_not_committable"
                status = "unknown" if existing[0] in {"started", "unknown"} else "rejected"
                return {"status": status, "applied": False, "reason": reason}
            self._conn.execute(
                "INSERT INTO effects (idempotency_key, run_id, fencing_token, phase, result, result_hash) VALUES (?, ?, ?, 'started', NULL, NULL)",
                (idempotency_key, run_id, fencing_token),
            )
            self._conn.commit()
        try:
            result = effect()
        except Exception:
            with self._lock:
                self._conn.execute(
                    "UPDATE effects SET phase = 'unknown' WHERE idempotency_key = ?",
                    (idempotency_key,),
                )
                self._conn.commit()
            raise
        with self._lock:
            fresh = self.get_run(run_id)
            if fencing_token != fresh.fencing_token or not fresh.eligible:
                self._conn.execute(
                    "UPDATE effects SET phase = 'rejected' WHERE idempotency_key = ?",
                    (idempotency_key,),
                )
                self._conn.commit()
                return {"status": "rejected", "applied": True, "reason": "revoked_during_effect", "result": result}
            digest = str(abs(hash(result)))
            self._conn.execute(
                "UPDATE effects SET phase = 'completed', result = ?, result_hash = ? WHERE idempotency_key = ?",
                (result, digest, idempotency_key),
            )
            self._conn.commit()
            return {"status": "completed", "applied": True, "result": result}

    def mark_started_only(self, run_id: str, idempotency_key: str, fencing_token: int) -> None:
        """模拟工具已经开始、结果还没写完时进程崩溃。"""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO effects (idempotency_key, run_id, fencing_token, phase, result, result_hash) VALUES (?, ?, ?, 'started', NULL, NULL)",
                (idempotency_key, run_id, fencing_token),
            )
            self._conn.commit()

    def signal_executor(self, run_id: str, *, acked: bool) -> RunState:
        with self._lock:
            state = self.get_run(run_id)
            if state.phase == "cancel_requested":
                state.phase = transition(state.phase, "stopping")
            if state.phase == "stopping":
                state.phase = transition(state.phase, "stopped" if acked else "unknown")
            state.cancel.executor_acked = acked
            state.cancel.cancellation_confirmed = acked
            self._save_phase(state)
            self._write_cancel(state)
            return self.get_run(run_id)

    def observe_slot(self, run_id: str, *, free: bool) -> RunState:
        """只记录本执行器的槽位。不把槽位空闲写成 GPU 已回收。"""
        with self._lock:
            state = self.get_run(run_id)
            state.cancel.slot_released = free
            state.cancel.gpu_reclaimed = False
            if state.phase in {"stopped", "unknown", "committed", "rejected", "failed"}:
                state.phase = transition(state.phase, "reconciled")
            self._save_phase(state)
            self._write_cancel(state)
            return self.get_run(run_id)

    def _write_cancel(self, state: RunState) -> None:
        acked = None if state.cancel.executor_acked is None else int(state.cancel.executor_acked)
        self._conn.execute(
            """
            INSERT INTO cancels (run_id, eligibility_revoked, executor_acked, slot_released, gpu_reclaimed)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(run_id) DO UPDATE SET
                eligibility_revoked = excluded.eligibility_revoked,
                executor_acked = excluded.executor_acked,
                slot_released = excluded.slot_released,
                gpu_reclaimed = 0
            """,
            (state.run_id, int(state.cancel.eligibility_revoked), acked, int(state.cancel.slot_released)),
        )
        self._conn.commit()

    def _migrate(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                session_id TEXT,
                fencing_token INTEGER NOT NULL,
                phase TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leases (
                lease_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                resource_class TEXT NOT NULL,
                state TEXT NOT NULL,
                pid INTEGER
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                attempt_id TEXT,
                event_type TEXT NOT NULL,
                causation_id INTEGER,
                payload TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        effect_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(effects)")}
        for name, declaration in (
            ("effect_id", "TEXT"),
            ("attempt_id", "TEXT"),
            ("arguments_hash", "TEXT"),
            ("effect_state", "TEXT"),
            ("receipt", "TEXT"),
        ):
            if name not in effect_columns:
                self._conn.execute(f"ALTER TABLE effects ADD COLUMN {name} {declaration}")
        cancel_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(cancels)")}
        if "resource_observation" not in cancel_columns:
            self._conn.execute("ALTER TABLE cancels ADD COLUMN resource_observation TEXT DEFAULT 'not_observable'")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.execute("INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', '1')")
        self._conn.commit()

    def schema_version(self) -> str:
        row = self._conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        return "0" if row is None else str(row[0])

    def list_runs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT run_id, phase, fencing_token, state_version, eligible FROM runs ORDER BY run_id"
        ).fetchall()
        return [
            {
                "run_id": row[0],
                "phase": row[1],
                "fencing_token": row[2],
                "state_version": row[3],
                "eligible": bool(row[4]),
            }
            for row in rows
        ]

    def effects_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT idempotency_key, phase, effect_state, receipt
            FROM effects WHERE run_id = ? ORDER BY idempotency_key
            """,
            (run_id,),
        ).fetchall()
        return [
            {"idempotency_key": row[0], "phase": row[1], "effect_state": row[2], "receipt": row[3]}
            for row in rows
        ]

    def supersede(self, run_id: str, sink: Any | None = None) -> RunState:
        """作废当前 fencing token，并留下一个仍可提交的新 token。

        这和 ``revoke`` 不同。``revoke`` 会取消资格。这里的 Run 继续处于 running。
        传入 sink 时，在同一把锁里把接收器版本和 Run 的 state_version 一起推进。
        不传 sink 时只推进 Run，接收器版本保持原值。
        """
        with self._lock:
            state = self.get_run(run_id)
            if state.phase != "running" or not state.eligible:
                raise ControlError("successor requires an eligible running run")
            state.fencing_token += 1
            state.state_version += 1
            if sink is not None:
                sink.bump()
            self._save_phase(state)
            self._event_locked(
                run_id,
                "eligibility_superseded",
                {"fencing_token": state.fencing_token, "state_version": state.state_version, "sink_aligned": sink is not None},
            )
            return self.get_run(run_id)

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any], *, attempt_id: str | None = None, causation_id: int | None = None) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO runtime_events (run_id, attempt_id, event_type, causation_id, payload, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    attempt_id,
                    event_type,
                    causation_id,
                    json.dumps(payload, sort_keys=True, ensure_ascii=False),
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT seq, event_type, causation_id, payload, recorded_at FROM runtime_events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [
            {
                "seq": row[0],
                "event_type": row[1],
                "causation_id": row[2],
                "payload": json.loads(row[3]),
                "recorded_at": row[4],
            }
            for row in rows
        ]

    def open_attempt(self, run_id: str, attempt_id: str, session_id: str) -> dict[str, Any]:
        with self._lock:
            state = self.get_run(run_id)
            self._conn.execute(
                """
                INSERT INTO attempts (attempt_id, run_id, session_id, fencing_token, phase)
                VALUES (?, ?, ?, ?, 'open')
                """,
                (attempt_id, run_id, session_id, state.fencing_token),
            )
            self._conn.commit()
        self.append_event(run_id, "attempt_opened", {"attempt_id": attempt_id, "session_id": session_id}, attempt_id=attempt_id)
        return {"attempt_id": attempt_id, "session_id": session_id, "fencing_token": state.fencing_token}

    def effect_row(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT phase, effect_state, receipt, result FROM effects WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return {"phase": row[0], "effect_state": row[1], "receipt": row[2], "result": row[3]}

    def record_intent(self, run_id: str, idempotency_key: str, fencing_token: int, state_version: int, *, effect_id: str, arguments_hash: str, tool_name: str = "") -> dict[str, Any]:
        with self._lock:
            state = self.get_run(run_id)
            if not state.eligible or fencing_token != state.fencing_token or state_version != state.state_version:
                return {"status": "rejected", "allowed": False}
            existing = self.effect_row(idempotency_key)
            if existing is not None:
                return {"status": "replayed" if existing["phase"] == "completed" else "unknown", "allowed": False, "receipt": existing["receipt"]}
            self._conn.execute(
                """
                INSERT INTO effects (
                    idempotency_key, run_id, fencing_token, phase, result, result_hash,
                    effect_id, arguments_hash, effect_state, receipt
                ) VALUES (?, ?, ?, 'started', NULL, NULL, ?, ?, 'INTENT_RECORDED', NULL)
                """,
                (idempotency_key, run_id, fencing_token, effect_id, arguments_hash),
            )
            self._conn.commit()
        self.append_event(run_id, "intent_recorded", {"effect_id": effect_id, "idempotency_key": idempotency_key, "tool_name": tool_name})
        return {"status": "reserved", "allowed": True, "effect_id": effect_id, "fencing_token": fencing_token, "state_version": state_version}

    def start_command(self, run_id: str, idempotency_key: str, fencing_token: int) -> dict[str, Any]:
        """意图已经记下之后才允许启动。资格不对就停在未知，命令不该再跑。"""
        with self._lock:
            state = self.get_run(run_id)
            row = self.effect_row(idempotency_key)
            if row is None or row["effect_state"] != "INTENT_RECORDED":
                current = None if row is None else row["effect_state"]
                return {"allowed": False, "effect_state": current}
            if not state.eligible or fencing_token != state.fencing_token:
                self._conn.execute(
                    "UPDATE effects SET effect_state = 'OUTCOME_UNKNOWN', phase = 'unknown' WHERE idempotency_key = ?",
                    (idempotency_key,),
                )
                self._conn.commit()
                self._event_locked(run_id, "result_withheld", {"idempotency_key": idempotency_key})
                return {"allowed": False, "effect_state": "OUTCOME_UNKNOWN"}
            self._conn.execute(
                "UPDATE effects SET effect_state = 'STARTED' WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            self._conn.commit()
            self._event_locked(run_id, "command_started", {"idempotency_key": idempotency_key})
            return {"allowed": True, "effect_state": "STARTED"}

    def finish_command(self, run_id: str, idempotency_key: str, fencing_token: int, exit_code: int | None) -> dict[str, Any]:
        """看到退出码才进入 EXITED。token 仍匹配才把观察记成 RESULT_PERSISTED。"""
        with self._lock:
            state = self.get_run(run_id)
            row = self.effect_row(idempotency_key)
            if row is None:
                return {"effect_state": "OUTCOME_UNKNOWN", "persisted": False}
            if row["effect_state"] in {"RESULT_PERSISTED", "OUTCOME_UNKNOWN", "CANCELLED_CONFIRMED"}:
                return {
                    "effect_state": row["effect_state"],
                    "persisted": False,
                    "replayed": row["effect_state"] == "RESULT_PERSISTED",
                    "receipt": row["receipt"],
                }
            if exit_code is None:
                self._conn.execute(
                    "UPDATE effects SET effect_state = 'OUTCOME_UNKNOWN', phase = 'unknown' WHERE idempotency_key = ?",
                    (idempotency_key,),
                )
                self._conn.commit()
                self._event_locked(run_id, "outcome_unknown", {"idempotency_key": idempotency_key})
                return {"effect_state": "OUTCOME_UNKNOWN", "persisted": False}
            self._conn.execute(
                "UPDATE effects SET effect_state = 'EXITED' WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            self._conn.commit()
            self._event_locked(run_id, "command_exited", {"idempotency_key": idempotency_key, "exit_code": exit_code})
            if not state.eligible or fencing_token != state.fencing_token:
                self._conn.execute(
                    "UPDATE effects SET effect_state = 'OUTCOME_UNKNOWN', phase = 'unknown' WHERE idempotency_key = ?",
                    (idempotency_key,),
                )
                self._conn.commit()
                self._event_locked(run_id, "result_withheld", {"idempotency_key": idempotency_key, "exit_code": exit_code})
                return {"effect_state": "OUTCOME_UNKNOWN", "persisted": False}
            receipt = f"command:{exit_code}:{idempotency_key}"
            self._store_receipt_locked(idempotency_key, run_id, fencing_token, receipt)
            return {"effect_state": "RESULT_PERSISTED", "persisted": True, "receipt": receipt}

    def seal_unconfirmed_external(self, run_id: str, idempotency_key: str, fencing_token: int, state_version: int) -> dict[str, Any]:
        """对方不能条件提交时只记未知。同一键再来不会记第二次，也不表示可以重试。"""
        with self._lock:
            state = self.get_run(run_id)
            existing = self.effect_row(idempotency_key)
            if existing is not None:
                return {"status": "replayed", "recorded": False, "retry": False, "effect_state": existing["effect_state"]}
            if not state.eligible or fencing_token != state.fencing_token or state_version != state.state_version:
                return {"status": "rejected", "recorded": False, "retry": False}
            self._conn.execute(
                """
                INSERT INTO effects (
                    idempotency_key, run_id, fencing_token, phase, effect_id, arguments_hash, effect_state
                ) VALUES (?, ?, ?, 'unknown', ?, 'external', 'OUTCOME_UNKNOWN')
                """,
                (idempotency_key, run_id, fencing_token, idempotency_key),
            )
            self._conn.commit()
            self._event_locked(run_id, "outcome_unknown", {"idempotency_key": idempotency_key, "kind": "external_unconfirmed"})
            return {"status": "unknown", "recorded": True, "retry": False, "effect_state": "OUTCOME_UNKNOWN"}

    def mark_effect_state(self, idempotency_key: str, effect_state: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE effects SET effect_state = ? WHERE idempotency_key = ?",
                (effect_state, idempotency_key),
            )
            self._conn.commit()

    def store_receipt(self, run_id: str, idempotency_key: str, fencing_token: int, receipt: str) -> None:
        with self._lock:
            self._store_receipt_locked(idempotency_key, run_id, fencing_token, receipt)

    def _begin_immediate(self) -> None:
        """跨进程的提交和撤销用写事务排队。线程锁只覆盖本进程。"""
        if self._conn.in_transaction:
            self._conn.commit()
        self._conn.execute("BEGIN IMMEDIATE")

    def claim_publish(self, run_id: str, idempotency_key: str, fencing_token: int, digest: str) -> dict[str, Any]:
        with self._lock:
            self._begin_immediate()
            try:
                state = self.get_run(run_id)
                existing = self.effect_row(idempotency_key)
                if existing is not None and existing["phase"] == "completed":
                    self._conn.commit()
                    return {"status": "replayed", "claimed": False, "receipt": existing["receipt"]}
                if not state.eligible or fencing_token != state.fencing_token:
                    self._event_locked(run_id, "result_withheld", {"idempotency_key": idempotency_key})
                    return {"status": "rejected", "claimed": False}
                self._conn.execute(
                    """
                    INSERT INTO effects (
                        idempotency_key, run_id, fencing_token, phase, result, result_hash, effect_state
                    ) VALUES (?, ?, ?, 'started', ?, ?, 'DISPATCHED')
                    ON CONFLICT(idempotency_key) DO UPDATE SET
                        phase = 'started',
                        result = excluded.result,
                        result_hash = excluded.result_hash,
                        effect_state = 'DISPATCHED'
                    """,
                    (idempotency_key, run_id, fencing_token, digest, digest),
                )
                self._conn.commit()
                return {"status": "claimed", "claimed": True, "digest": digest}
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def complete_claimed_publish(self, run_id: str, idempotency_key: str, digest: str, receipt: str, *, path: str) -> dict[str, Any]:
        with self._lock:
            self._begin_immediate()
            try:
                existing = self.effect_row(idempotency_key)
                if existing is None:
                    self._conn.commit()
                    return {"status": "missing", "published": False}
                if existing["phase"] == "completed":
                    self._conn.commit()
                    return {"status": "replayed", "published": False, "receipt": existing["receipt"]}
                if existing["result"] != digest:
                    self._conn.execute(
                        "UPDATE effects SET effect_state = 'OUTCOME_UNKNOWN', phase = 'unknown' WHERE idempotency_key = ?",
                        (idempotency_key,),
                    )
                    self._event_locked(run_id, "outcome_unknown", {"idempotency_key": idempotency_key, "path": path})
                    return {"status": "unknown", "published": False}
                self._store_receipt_locked(idempotency_key, run_id, 0, receipt)
                self._event_locked(
                    run_id,
                    "file_published",
                    {"path": path, "receipt": receipt, "sha256": digest, "receipt_verified": True, "idempotency_key": idempotency_key},
                )
                return {"status": "completed", "published": True, "receipt": receipt, "sha256": digest}
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def commit_authorized(self, run_id: str, *, idempotency_key: str, fencing_token: int, state_version: int, sink: Any, payload: str) -> dict[str, Any]:
        """写事务里核对 fencing token，再写入可控制的 sink。"""
        with self._lock:
            self._begin_immediate()
            try:
                return self._commit_authorized_locked(
                    run_id,
                    idempotency_key=idempotency_key,
                    fencing_token=fencing_token,
                    state_version=state_version,
                    sink=sink,
                    payload=payload,
                )
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def _commit_authorized_locked(self, run_id: str, *, idempotency_key: str, fencing_token: int, state_version: int, sink: Any, payload: str) -> dict[str, Any]:
            state = self.get_run(run_id)
            if not state.eligible or fencing_token != state.fencing_token or state_version != state.state_version:
                self._event_locked(run_id, "commit_rejected", {"idempotency_key": idempotency_key})
                return {"status": "rejected", "applied": False, "sink_accepted": False}
            existing = self.effect_row(idempotency_key)
            if existing is not None and existing["phase"] == "completed":
                self._conn.commit()
                return {"status": "replayed", "applied": False, "sink_accepted": False, "receipt": existing["receipt"]}
            if existing is not None and existing["phase"] in {"started", "unknown"}:
                self._conn.commit()
                return {"status": "unknown", "applied": False, "sink_accepted": False}
            decision = sink.accept(idempotency_key, expected_version=state_version, payload=payload)
            if decision["status"] == "replayed":
                self._store_receipt_locked(idempotency_key, run_id, fencing_token, decision["receipt"])
                return {"status": "replayed", "applied": False, "sink_accepted": False, "receipt": decision["receipt"]}
            if decision["status"] != "accepted":
                self._event_locked(run_id, "commit_rejected", {"idempotency_key": idempotency_key, "reason": decision.get("reason")})
                return {"status": "rejected", "applied": False, "sink_accepted": False, "reason": decision.get("reason")}
            self._store_receipt_locked(idempotency_key, run_id, fencing_token, decision["receipt"])
            self._event_locked(run_id, "effect_committed", {"idempotency_key": idempotency_key, "receipt": decision["receipt"]})
            return {"status": "completed", "applied": True, "sink_accepted": True, "receipt": decision["receipt"]}

    def revoke_with_sink(self, run_id: str, sink: Any) -> RunState:
        with self._lock:
            self._begin_immediate()
            try:
                return self._revoke_with_sink_locked(run_id, sink)
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def _revoke_with_sink_locked(self, run_id: str, sink: Any) -> RunState:
            state = self.get_run(run_id)
            if state.phase == "committed":
                self._conn.commit()
                return state
            state.eligible = False
            state.fencing_token += 1
            state.state_version += 1
            if state.phase == "created":
                state.phase = "running"
            if state.phase in {"running", "checkpointed"}:
                state.phase = transition(state.phase, "cancel_requested")
            state.cancel.eligibility_revoked = True
            sink.bump()
            self._save_phase(state)
            self._write_cancel(state)
            self._event_locked(run_id, "eligibility_revoked", {"fencing_token": state.fencing_token})
            return self.get_run(run_id)

    def grant_lease(self, lease_id: str, run_id: str, worker_id: str, resource_class: str, pid: int | None) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO leases (lease_id, run_id, worker_id, resource_class, state, pid)
                VALUES (?, ?, ?, ?, 'held', ?)
                """,
                (lease_id, run_id, worker_id, resource_class, pid),
            )
            self._conn.commit()

    def release_lease(self, lease_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE leases SET state = 'released', pid = NULL WHERE lease_id = ?", (lease_id,))
            self._conn.commit()

    def held_leases(self, resource_class: str | None = None) -> list[dict[str, Any]]:
        if resource_class is None:
            rows = self._conn.execute("SELECT lease_id, run_id, worker_id, resource_class, state, pid FROM leases WHERE state = 'held'").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT lease_id, run_id, worker_id, resource_class, state, pid FROM leases WHERE state = 'held' AND resource_class = ?",
                (resource_class,),
            ).fetchall()
        return [
            {"lease_id": row[0], "run_id": row[1], "worker_id": row[2], "resource_class": row[3], "state": row[4], "pid": row[5]}
            for row in rows
        ]

    def _store_receipt_locked(self, idempotency_key: str, run_id: str, fencing_token: int, receipt: str) -> None:
        digest = hashlib.sha256(receipt.encode("utf-8")).hexdigest()
        self._conn.execute(
            """
            INSERT INTO effects (
                idempotency_key, run_id, fencing_token, phase, result, result_hash, effect_state, receipt
            ) VALUES (?, ?, ?, 'completed', ?, ?, 'RESULT_PERSISTED', ?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
                phase = 'completed',
                result = excluded.result,
                result_hash = excluded.result_hash,
                effect_state = 'RESULT_PERSISTED',
                receipt = excluded.receipt
            """,
            (idempotency_key, run_id, fencing_token, receipt, digest, receipt),
        )
        self._conn.commit()

    def _event_locked(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO runtime_events (run_id, attempt_id, event_type, causation_id, payload, recorded_at)
            VALUES (?, NULL, ?, NULL, ?, ?)
            """,
            (run_id, event_type, json.dumps(payload, sort_keys=True), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
        self._conn.commit()


class SlotPool:
    """固定槽位和有界队列。测量的是这个执行器，不是推理引擎。"""

    def __init__(self, *, capacity: int, queue_limit: int) -> None:
        if capacity < 1 or queue_limit < 0:
            raise ControlError("capacity and queue_limit must be non-negative")
        self.capacity = capacity
        self.queue_limit = queue_limit
        self._running: dict[str, dict[str, Any]] = {}
        self._queue: deque[dict[str, Any]] = deque()
        self.rejected = 0
        self.completed = 0
        self.deadline_misses = 0
        self.waits: list[float] = []
        self._clock = 0.0

    def submit(self, task_id: str, *, service_s: float, deadline_s: float | None = None) -> str:
        item = {
            "task_id": task_id,
            "service_s": service_s,
            "deadline_s": deadline_s,
            "enqueued_at": self._clock,
        }
        if len(self._running) < self.capacity:
            self._start(item)
            return "running"
        if len(self._queue) < self.queue_limit:
            self._queue.append(item)
            return "queued"
        self.rejected += 1
        return "rejected"

    def finish_one(self) -> str | None:
        if not self._running:
            return None
        task_id = next(iter(self._running))
        item = self._running.pop(task_id)
        self._clock += float(item["service_s"])
        self.completed += 1
        if self._queue:
            nxt = self._queue.popleft()
            self._start(nxt)
        return task_id

    def drain(self) -> None:
        while self._running or self._queue:
            self.finish_one()

    @property
    def active(self) -> int:
        return len(self._running)

    def wait_summary(self) -> dict[str, float | int]:
        ordered = sorted(self.waits)
        def pick(q: float) -> float:
            if not ordered:
                return 0.0
            index = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
            return ordered[index]
        return {
            "completed": self.completed,
            "rejected": self.rejected,
            "deadline_misses": self.deadline_misses,
            "wait_p50": pick(0.50),
            "wait_p95": pick(0.95),
        }

    def _start(self, item: dict[str, Any]) -> None:
        wait = self._clock - float(item["enqueued_at"])
        self.waits.append(wait)
        deadline = item["deadline_s"]
        if deadline is not None and self._clock > float(deadline):
            self.deadline_misses += 1
        self._running[item["task_id"]] = item


def run_acceptance() -> dict[str, Any]:
    """跑一组确定性场景。返回值可以直接写进检查报告。"""
    checks: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    ledger = RunLedger()
    ledger.create_run("run-stale")
    ledger.advance("run-stale", "running")
    token = ledger.get_run("run-stale").fencing_token
    version = ledger.get_run("run-stale").state_version
    ledger.revoke("run-stale")
    calls: list[int] = []
    stale = ledger.execute(
        "run-stale",
        idempotency_key="act-1",
        fencing_token=token,
        state_version=version,
        effect=lambda: calls.append(1) or "wrote",
    )
    record("revoked_action_not_applied", stale["status"] == "rejected" and calls == [], stale["status"])

    ledger.create_run("run-once")
    ledger.advance("run-once", "running")
    state = ledger.get_run("run-once")
    calls.clear()
    first = ledger.execute("run-once", idempotency_key="act-2", fencing_token=state.fencing_token, state_version=state.state_version, effect=lambda: calls.append(1) or "ok")
    second = ledger.execute("run-once", idempotency_key="act-2", fencing_token=state.fencing_token, state_version=state.state_version, effect=lambda: calls.append(1) or "ok")
    record("duplicate_effect_replayed", first["applied"] and second["status"] == "replayed" and calls == [1], second["status"])

    ledger.create_run("run-late")
    ledger.advance("run-late", "running")
    before = ledger.get_run("run-late")
    ledger.revoke("run-late")
    late = ledger.execute("run-late", idempotency_key="act-3", fencing_token=before.fencing_token, state_version=before.state_version, effect=lambda: "late")
    record("late_output_rejected", late["status"] == "rejected", late["status"])

    ledger.create_run("run-cancel")
    ledger.advance("run-cancel", "running")
    ledger.revoke("run-cancel")
    unknown = ledger.signal_executor("run-cancel", acked=False)
    record(
        "unacked_cancel_is_not_release",
        unknown.cancel.cancellation_confirmed is False and unknown.cancel.slot_released is False and unknown.cancel.gpu_reclaimed is False,
        unknown.phase,
    )
    released = ledger.observe_slot("run-cancel", free=True)
    record(
        "slot_release_is_not_gpu_reclaim",
        released.cancel.slot_released is True and released.cancel.gpu_reclaimed is False,
        released.phase,
    )

    path_ledger = RunLedger()
    path_ledger.create_run("run-crash")
    path_ledger.advance("run-crash", "running")
    crashed = path_ledger.get_run("run-crash")
    path_ledger.mark_started_only("run-crash", "act-4", crashed.fencing_token)
    recovered = path_ledger.execute(
        "run-crash",
        idempotency_key="act-4",
        fencing_token=crashed.fencing_token,
        state_version=crashed.state_version,
        effect=lambda: calls.append(9) or "again",
    )
    record("unknown_effect_not_retried", recovered["status"] == "unknown" and 9 not in calls, recovered["status"])

    pool = SlotPool(capacity=2, queue_limit=2)
    statuses = [pool.submit(f"t{i}", service_s=0.5, deadline_s=0.4) for i in range(6)]
    pool.drain()
    summary = pool.wait_summary()
    record(
        "backpressure_rejects_overflow",
        statuses.count("rejected") == 2 and summary["rejected"] == 2 and summary["deadline_misses"] >= 1,
        str(summary),
    )
    ledger.close()
    path_ledger.close()
    return {
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "executor": summary,
        "measurement": "local slot pool with scripted service time; not a GPU or vLLM measurement",
    }
