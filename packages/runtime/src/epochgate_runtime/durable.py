"""SQLite queue and controlled receiver with a shared transactional fence."""

from contextlib import closing, contextmanager
import hashlib
import json
import sqlite3
import time
import uuid


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Queue:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, task TEXT NOT NULL, status TEXT NOT NULL,
                  owner TEXT, epoch INTEGER NOT NULL DEFAULT 0, deadline REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS receiver (
                  job TEXT NOT NULL, step TEXT NOT NULL, payload TEXT NOT NULL, receipt TEXT NOT NULL,
                  epoch INTEGER NOT NULL, PRIMARY KEY(job,step)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                  seq INTEGER PRIMARY KEY, job TEXT NOT NULL, epoch INTEGER NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS world (
                  resource TEXT PRIMARY KEY, version INTEGER NOT NULL, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commit_evidence (
                  job TEXT NOT NULL, step TEXT NOT NULL, evidence TEXT NOT NULL,
                  PRIMARY KEY(job,step)
                );
            """)

    @contextmanager
    def transaction(self, *, write=True):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db

    def submit(self, task, job_id=None):
        job_id = job_id or "agent-" + uuid.uuid4().hex
        body = canonical(task)
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO jobs(id,task,status) VALUES (?,?,'queued')", (job_id, body))
            prior = json.loads(db.execute("SELECT task FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
            prior.pop("observed_world", None)
            requested = {key: value for key, value in task.items() if key != "observed_world"}
            if canonical(prior) != canonical(requested):
                raise ValueError("Job ID already belongs to a different task")
        return job_id

    def claim(self, worker, ttl):
        if ttl <= 0:
            raise ValueError("Lease TTL must be positive")
        with self.transaction() as db:
            now = time.time()
            row = db.execute("""SELECT id FROM jobs WHERE status='queued'
                OR (status='running' AND deadline<=?) ORDER BY rowid LIMIT 1""", (now,)).fetchone()
            if row is None:
                return None
            updated = db.execute("""UPDATE jobs SET owner=?, epoch=epoch+1, deadline=?,status='running'
                WHERE id=? RETURNING *""", (worker, now + ttl, row["id"])).fetchone()
            return dict(updated)

    @staticmethod
    def owns(db, job, epoch, worker):
        return db.execute("""SELECT 1 FROM jobs WHERE id=? AND epoch=? AND owner=?
            AND deadline>? AND status='running'""", (job, epoch, worker, time.time())).fetchone() is not None

    def heartbeat(self, job, epoch, worker, ttl):
        with self.transaction() as db:
            if not self.owns(db, job, epoch, worker):
                return False
            db.execute("UPDATE jobs SET deadline=? WHERE id=?", (time.time() + ttl, job))
            return True

    def record(self, job, epoch, worker, data, finish=None):
        with self.transaction() as db:
            if not self.owns(db, job, epoch, worker):
                return False
            db.execute("INSERT INTO decisions(job,epoch,data) VALUES (?,?,?)", (job, epoch, canonical(data)))
            if finish:
                db.execute("UPDATE jobs SET status=? WHERE id=?", (finish, job))
            return True

    def cancel(self, job):
        with self.transaction() as db:
            cancelled = db.execute("""UPDATE jobs SET status='cancelled',epoch=epoch+1,deadline=0
                WHERE id=? AND status IN ('queued','running') RETURNING epoch""", (job,)).fetchone()
            if cancelled is None:
                return False
            db.execute("INSERT INTO decisions(job,epoch,data) VALUES (?,?,?)",
                       (job, cancelled["epoch"], canonical({"kind":"terminal","result":"CANCELLED"})))
            return True

    def get(self, job):
        with self.transaction(write=False) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            if not row:
                return None
            trace = [{"sequence": item["seq"], "epoch": item["epoch"], **json.loads(item["data"])}
                     for item in db.execute("SELECT * FROM decisions WHERE job=? ORDER BY seq", (job,))]
            receipts = [self.read_receipt(item) for item in db.execute("""SELECT receiver.*, evidence FROM receiver
                LEFT JOIN commit_evidence USING(job,step) WHERE job=? ORDER BY receiver.rowid""", (job,))]
            return {**dict(row), "task": json.loads(row["task"]), "decisions": trace, "receipts": receipts}

    def list(self):
        with self.transaction(write=False) as db:
            return [dict(row) for row in db.execute("SELECT id,status,owner,epoch FROM jobs ORDER BY rowid DESC")]

    def summary(self):
        now = time.time()
        with self.transaction(write=False) as db:
            row = db.execute("""SELECT COUNT(CASE WHEN status='queued' THEN 1 END) AS queued,
                COUNT(CASE WHEN status='running' AND deadline>? THEN 1 END) AS leased,
                COUNT(CASE WHEN status='running' AND deadline<=? THEN 1 END) AS awaiting_takeover
                FROM jobs""", (now, now)).fetchone()
            return dict(row)

    def receipt(self, job, step):
        with self.transaction(write=False) as db:
            row = db.execute("""SELECT receiver.*, evidence FROM receiver LEFT JOIN commit_evidence USING(job,step)
                WHERE job=? AND step=?""", (job, step)).fetchone()
            return self.read_receipt(row) if row else None

    @staticmethod
    def read_receipt(row):
        record = dict(row)
        proof = record.pop("evidence")
        return {**record, "commit_evidence": json.loads(proof) if proof else None}

    def world(self):
        with self.transaction(write=False) as db:
            return {row["resource"]: {"version": row["version"], "value": json.loads(row["value"])}
                    for row in db.execute("SELECT * FROM world")}

    def commit(self, job, epoch, worker, step, effect, versions, predicates):
        payload = canonical(effect)
        with self.transaction() as db:
            if not self.owns(db, job, epoch, worker):
                return {"status": "FENCED"}
            dispatch = db.execute("""SELECT data FROM decisions WHERE job=? AND epoch=?
                AND json_extract(data,'$.kind')='dispatch' AND json_extract(data,'$.step')=?
                ORDER BY seq DESC LIMIT 1""", (job, epoch, step)).fetchone()
            proposal = {"effect": effect, "versions": versions, "predicates": predicates}
            if not dispatch or canonical(json.loads(dispatch["data"]).get("proposal")) != canonical(proposal):
                return {"status": "NOT_ADMITTED"}
            existing = db.execute("SELECT * FROM receiver WHERE job=? AND step=?", (job, step)).fetchone()
            if existing:
                return {"status": "REPLAY" if existing["payload"] == payload else "CONFLICT",
                        "receipt": existing["receipt"]}
            observed = {}
            for key in set(versions) | set(predicates):
                current = db.execute("SELECT version,value FROM world WHERE resource=?", (key,)).fetchone()
                observed[key] = {"version": current["version"], "value": json.loads(current["value"])} if current else None
                if key in versions and (current["version"] if current else 0) != versions[key]:
                    return {"status": "STALE_WORLD"}
                if key in predicates and (not current or current["value"] != canonical(predicates[key])):
                    return {"status": "INVALID_PREDICATE"}
            for resource, value in effect.items():
                db.execute("""INSERT INTO world VALUES (?,1,?)
                    ON CONFLICT(resource) DO UPDATE SET version=version+1,value=excluded.value""",
                           (resource, canonical(value)))
            receipt = hashlib.sha256(canonical([job, step, effect]).encode()).hexdigest()
            db.execute("INSERT INTO receiver VALUES (?,?,?,?,?)", (job, step, payload, receipt, epoch))
            db.execute("INSERT INTO commit_evidence VALUES (?,?,?)",
                       (job, step, canonical({"observed": observed, "versions": versions, "predicates": predicates})))
            return {"status": "COMMITTED", "receipt": receipt}
