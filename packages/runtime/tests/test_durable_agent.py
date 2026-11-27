import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from epochgate_runtime.api import Runtime
from epochgate_runtime.agent_decision import EvidencePolicy, Observation, repair_plan
from epochgate_runtime.durable import Queue
from epochgate_runtime.durable_worker import run_one
from epochgate_runtime.httpapp import create_app


def expire(queue, job):
    with queue.transaction() as db:
        db.execute("UPDATE jobs SET deadline=0 WHERE id=?", (job,))


def test_model_launch_replay_calls_provider_once(tmp_path, monkeypatch):
    calls = []

    def fake_propose(prompt, resources):
        calls.append(prompt)
        return {"plan": [{"step_id": "publish", "effect": {resources[0]: "value"}}], "planner": {"tokens": 5}}

    monkeypatch.setattr("epochgate_runtime.httpapp.propose", fake_propose)
    runtime = Runtime.open(tmp_path / "epochgate.db")
    try:
        headers = {"Authorization": "Bearer test", "Idempotency-Key": "model-1"}
        body = {"prompt": "publish", "resources": ["artifact"]}
        with TestClient(create_app(runtime, tmp_path, "test"), headers=headers) as client:
            first = client.post("/agent/model-tasks", json=body)
            assert first.status_code == 202
            assert client.post("/agent/model-tasks", json=body).json() == first.json()
            assert client.post("/agent/model-tasks", json={**body, "prompt": "different"}).status_code == 409
            assert calls == ["publish"]
    finally:
        runtime.close()


def test_takeover_refuses_a_receipt_for_different_effect(tmp_path):
    queue = Queue(tmp_path / "db")
    job = queue.submit({"plan": [{"step_id": "publish", "effect": {"artifact": "expected"}}]})
    claim = queue.claim("old", 30)
    assert queue.record(job, claim["epoch"], "old", {"kind":"dispatch","step":"publish",
                                                    "proposal":{"effect":{"artifact":"expected"}}})
    expire(queue, job)
    assert queue.summary() == {"queued": 0, "leased": 0, "awaiting_takeover": 1}

    class WrongTarget:
        def receipt(self, job, step):
            return {"job": job, "step": step, "receipt": "wrong", "payload": '{"artifact":"other"}'}

    assert run_one(queue, WrongTarget(), "new", 30)
    view = queue.get(job)
    assert view["status"] == "unknown"
    assert view["decisions"][-1]["result"] == "RECEIPT_MISMATCH"
    assert view["receipts"] == []


def test_worker_process_death_and_api_reopen(tmp_path):
    runtime = Runtime.open(tmp_path / "epochgate.db")
    headers = {"Authorization": "Bearer test"}
    with TestClient(create_app(runtime, tmp_path, "test"), headers=headers) as client:
        body = {"plan": [{"step_id": "publish", "effect": {"artifact": "bytes"}}]}
        key = {**headers, "Idempotency-Key": "durable-launch"}
        response = client.post("/agent/tasks", json=body, headers=key)
        assert response.status_code == 202
        job = response.json()["run_id"]
        assert client.post("/agent/tasks", json=body, headers=key).json()["run_id"] == job
        assert client.post("/agent/tasks", json={**body, "strategy": "full"}, headers=key).status_code == 409
    runtime.close()

    queue = Queue(tmp_path / "agent-queue.sqlite")
    process = subprocess.run([sys.executable, "-m", "epochgate_runtime.durable_worker", "--data-dir", str(tmp_path),
                              "--once", "--fault-after-commit"], timeout=30)
    assert process.returncode == 86
    before = queue.get(job)
    assert before["status"] == "running" and len(before["receipts"]) == 1
    expire(queue, job)
    with ThreadPoolExecutor(2) as workers:
        def restart(_):
            return subprocess.run([sys.executable, "-m", "epochgate_runtime.durable_worker",
                                   "--data-dir", str(tmp_path), "--once"], timeout=30).returncode

        assert list(workers.map(restart, range(2))) == [0, 0]
    with queue.transaction() as db:
        assert db.execute("SELECT version FROM world WHERE resource='artifact'").fetchone()[0] == 1
    assert queue.commit(job, before["epoch"], before["owner"], "publish", {"artifact": "new"}, {}, {})["status"] == "FENCED"
    reopened = Runtime.open(tmp_path / "epochgate.db")
    try:
        with TestClient(create_app(reopened, tmp_path, "fresh"), headers={"Authorization": "Bearer fresh"}) as client:
            view = client.get(f"/agent/tasks/{job}").json()
            assert view["epoch"] == 2 and view["status"] == "completed"
            assert [e["action"] for e in view["decisions"] if e.get("kind") == "decision"] == ["CONTINUE", "QUERY_EFFECT", "CONTINUE"]
    finally:
        reopened.close()


def test_unknown_without_query_is_not_resent(tmp_path):
    queue = Queue(tmp_path / "db")
    job = queue.submit({"plan": [{"step_id": "publish", "effect": {"artifact": "bytes"}}], "queryable": False})
    claim = queue.claim("old", 30)
    assert queue.record(job, claim["epoch"], "old", {"kind":"dispatch","step":"publish"})
    expire(queue, job)
    assert run_one(queue, queue, "new", 30)
    view = queue.get(job)
    assert view["status"] == "unknown"
    assert view["receipts"] == []
    assert view["decisions"][1]["action"] == "WAIT"


def test_receiver_rejects_old_epoch_even_during_active_takeover(tmp_path):
    queue = Queue(tmp_path / "db")
    job = queue.submit({"plan": [{"step_id": "write", "effect": {"artifact": True}}]})
    old = queue.claim("old", 30)
    expire(queue, job)
    new = queue.claim("new", 30)
    proposal = {"effect": {"artifact": True}, "versions": {"artifact": 0}, "predicates": {}}
    assert queue.record(job, new["epoch"], "new", {"kind":"dispatch","step":"write","proposal":proposal})
    assert queue.commit(job, old["epoch"], "old", "write", **proposal)["status"] == "FENCED"
    mismatched = {**proposal, "effect": {"artifact": 1}}
    assert queue.commit(job, new["epoch"], "new", "write", **mismatched)["status"] == "NOT_ADMITTED"
    assert queue.commit(job, new["epoch"], "new", "write", **proposal)["status"] == "COMMITTED"


def test_repair_propagates_and_preserves_committed_node():
    plan = [
        {"step_id": "earlier", "effect": {"audit": 1}},
        {"step_id": "parent", "read_set": ["v"], "preconditions": {"v": 1},
         "repair": {"preconditions": {"v": 2}}},
        {"step_id": "child", "dependencies": ["parent"], "effect": {"artifact": 1},
         "repair": {"effect": {"artifact": 2}}},
    ]
    before = {"v": {"version": 1, "value": 1}}
    after = {"v": {"version": 2, "value": 2}}
    decision = repair_plan(plan, before, after, ["earlier"])
    assert decision["invalidated_steps"] == ["child", "parent"]
    assert decision["preserved_steps"] == ["earlier"]
    assert decision["plan"][2]["effect"] == {"artifact": 2}
    assert repair_plan(plan, before, {**before, "other": {"version": 1, "value": 2}})["invalidated_steps"] == []


def test_cost_and_evidence_constraints_override_dispatch():
    policy = EvidencePolicy(optimize=True)
    budget = {"tools": 0, "replans": 1, "tokens": 0}
    uncertain = policy.decide(Observation("UNKNOWN", "VALID", "UNKNOWN", True, True), budget)
    assert "RETRY" not in uncertain["safe_candidates"] and uncertain["action"] == "WAIT"
    invalid = policy.decide(Observation("NEW", "VALID", "INVALID", True, True), budget)
    assert invalid["action"] == "LOCAL_REPLAN"


def test_unresolved_dependency_refreshes_before_stopping(tmp_path):
    queue = Queue(tmp_path / "db")
    job = queue.submit({"plan": [{"step_id": "publish", "dependencies_known": False, "effect": {"artifact": 1}}]})
    assert run_one(queue, queue, "worker", 30)
    view = queue.get(job)
    assert view["status"] == "stopped"
    assert view["decisions"][0]["action"] == "REFRESH_STATE"
    assert view["decisions"][1]["result"] == "STATE_REFRESHED"
    assert view["receipts"] == []


def test_cancellation_fences_late_effects_and_preserves_receipts(tmp_path):
    queue = Queue(tmp_path / "db")
    job = queue.submit({"plan": [{"step_id": "first"}, {"step_id": "next"}]})
    claim = queue.claim("worker", 30)
    authority = (job, claim["epoch"], "worker")
    proposal = {"effect": {"first": 1}, "versions": {}, "predicates": {}}
    assert queue.record(*authority, {"kind": "dispatch", "step": "first", "proposal": proposal})
    committed = queue.commit(*authority, "first", **proposal)
    assert committed["status"] == "COMMITTED"
    assert queue.cancel(job)
    assert queue.commit(*authority, "next", **proposal)["status"] == "FENCED"
    assert queue.claim("another", 30) is None
    view = queue.get(job)
    assert view["status"] == "cancelled"
    assert [receipt["receipt"] for receipt in view["receipts"]] == [committed["receipt"]]
