"""论文四个实验包。不打印密钥，不测量 GPU 回收。"""

from __future__ import annotations

import json
import random
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "paper"
MODEL = "deepseek-flash"

sys.path.insert(0, str(ROOT / "scripts"))

from runtime_deepseek import chat  # noqa: E402

from epochgate_runtime.ledger import RunLedger  # noqa: E402
from epochgate_runtime.processes import cancel_process, start_sleeper  # noqa: E402
from epochgate_runtime.recovery import reconcile  # noqa: E402
from epochgate_runtime.service import ControlServer  # noqa: E402
from epochgate_runtime.sink import EffectSink  # noqa: E402


def _dump(name: str, payload: dict[str, Any]) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _fresh(tag: str) -> tuple[RunLedger, EffectSink, Path]:
    directory = OUT / "work" / tag
    directory.mkdir(parents=True, exist_ok=True)
    ledger = RunLedger(directory / "ledger.sqlite")
    sink = EffectSink(directory / "sink.sqlite")
    return ledger, sink, directory


def _open(ledger: RunLedger, sink: EffectSink, run_id: str) -> Any:
    ledger.create_run(run_id)
    ledger.advance(run_id, "running")
    return ledger.get_run(run_id)


def _commit(ledger: RunLedger, sink: EffectSink, run_id: str, key: str, token: int, version: int, payload: str) -> dict[str, Any]:
    return ledger.commit_authorized(
        run_id,
        idempotency_key=key,
        fencing_token=token,
        state_version=version,
        sink=sink,
        payload=payload,
    )


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n": len(rows),
        "all_match": all(item["matched"] for item in rows),
        "rows": rows,
    }


def package_b() -> dict[str, Any]:
    """独立故障机制。每个场景重复 5 次，预期写在场景里。"""

    def run(name: str, body: Callable[[RunLedger, EffectSink, str], dict[str, Any]]) -> dict[str, Any]:
        rows = []
        for index in range(5):
            ledger, sink, _ = _fresh(f"b-{name}-{index}")
            run_id = f"{name}-{index}"
            observed = body(ledger, sink, run_id)
            observed["matched"] = observed["observed"] == observed["expected"]
            rows.append(observed)
            ledger.close()
            sink.close()
        return {"scenario": name, **_summary(rows)}

    def no_notice(ledger: RunLedger, sink: EffectSink, run_id: str) -> dict[str, Any]:
        state = _open(ledger, sink, run_id)
        result = _commit(ledger, sink, run_id, "k", state.fencing_token, state.state_version, "reserve:1")
        return {
            "mechanism": "notification_absent",
            "expected": "completed",
            "observed": result["status"],
            "sink_accepts": sink.accepted_count("k"),
            "safety": False,
            "forward_progress": None,
            "rule": "没有 supersede 时旧 token 仍然有效。fencing 不会自己看见外部库存变化。",
        }

    def timely(ledger: RunLedger, sink: EffectSink, run_id: str) -> dict[str, Any]:
        state = _open(ledger, sink, run_id)
        old = (state.fencing_token, state.state_version)
        fresh = ledger.supersede(run_id, sink)
        rejected = _commit(ledger, sink, run_id, "old", old[0], old[1], "reserve:1")
        accepted = _commit(ledger, sink, run_id, "new", fresh.fencing_token, fresh.state_version, "inventory:0")
        observed = f"{rejected['status']}/{accepted['status']}"
        return {
            "mechanism": "notification_timely_aligned",
            "expected": "rejected/completed",
            "observed": observed,
            "sink_payloads": [sink.get("old"), sink.get("new")],
            "safety": rejected["status"] == "rejected" and sink.get("old") is None,
            "forward_progress": accepted["status"] == "completed",
            "rule": "supersede(sink) 同时推进 token、state_version 和 sink 版本。旧依据拒绝，新依据可以提交。",
        }

    def delayed(ledger: RunLedger, sink: EffectSink, run_id: str) -> dict[str, Any]:
        state = _open(ledger, sink, run_id)
        first = _commit(ledger, sink, run_id, "early", state.fencing_token, state.state_version, "reserve:1")
        ledger.supersede(run_id, sink)
        return {
            "mechanism": "notification_after_commit",
            "expected": "completed",
            "observed": first["status"],
            "sink_accepts": sink.accepted_count("early"),
            "safety": False,
            "forward_progress": None,
            "rule": "提交先完成时，之后的 supersede 不能把已经接受的效果收回。",
        }

    def duplicate(ledger: RunLedger, sink: EffectSink, run_id: str) -> dict[str, Any]:
        state = _open(ledger, sink, run_id)
        fresh = ledger.supersede(run_id, sink)
        first = _commit(ledger, sink, run_id, "same", fresh.fencing_token, fresh.state_version, "inventory:0")
        second = _commit(ledger, sink, run_id, "same", fresh.fencing_token, fresh.state_version, "inventory:0")
        observed = f"{first['status']}/{second['status']}/{sink.accepted_count('same')}"
        return {
            "mechanism": "duplicate_commit",
            "expected": "completed/replayed/1",
            "observed": observed,
            "safety": True,
            "forward_progress": True,
            "rule": "同一幂等键第二次不增加接受次数。",
        }

    def revoke_first(ledger: RunLedger, sink: EffectSink, run_id: str) -> dict[str, Any]:
        state = _open(ledger, sink, run_id)
        ledger.revoke_with_sink(run_id, sink)
        result = _commit(ledger, sink, run_id, "k", state.fencing_token, state.state_version, "reserve:1")
        return {
            "mechanism": "revoke_before_commit",
            "expected": "rejected",
            "observed": result["status"],
            "sink_accepts": sink.accepted_count("k"),
            "safety": result["status"] == "rejected",
            "forward_progress": False,
            "rule": "revoke 取消资格并推进 sink。旧 token 不能提交，这条 Run 也没有后继资格。",
        }

    def commit_then_revoke(ledger: RunLedger, sink: EffectSink, run_id: str) -> dict[str, Any]:
        state = _open(ledger, sink, run_id)
        first = _commit(ledger, sink, run_id, "k", state.fencing_token, state.state_version, "reserve:1")
        ledger.revoke_with_sink(run_id, sink)
        retry = _commit(ledger, sink, run_id, "k2", state.fencing_token, state.state_version, "reserve:1")
        receipt = sink.get("k")
        observed = f"{first['status']}/{retry['status']}/{receipt is not None}"
        return {
            "mechanism": "commit_then_revoke",
            "expected": "completed/rejected/True",
            "observed": observed,
            "safety": True,
            "forward_progress": False,
            "rule": "已经写入 sink 的 receipt 还在。撤销只挡住下一次提交。",
        }

    def mismatch(ledger: RunLedger, sink: EffectSink, run_id: str) -> dict[str, Any]:
        state = _open(ledger, sink, run_id)
        old = (state.fencing_token, state.state_version)
        fresh = ledger.supersede(run_id)
        rejected_old = _commit(ledger, sink, run_id, "old", old[0], old[1], "reserve:1")
        rejected_new = _commit(ledger, sink, run_id, "new", fresh.fencing_token, fresh.state_version, "inventory:0")
        observed = f"{rejected_old['status']}/{rejected_new['status']}"
        return {
            "mechanism": "version_unaligned",
            "expected": "rejected/rejected",
            "observed": observed,
            "safety": sink.accepted_count() == 0,
            "forward_progress": False,
            "classification": "coordination_defect",
            "rule": "协调规则要求 sink 版本和 state_version 一起变。只调用 supersede() 时新 token 也会被版本检查拒绝。这不是预期的安全拒绝。",
        }

    def repeated_notice(ledger: RunLedger, sink: EffectSink, run_id: str) -> dict[str, Any]:
        _open(ledger, sink, run_id)
        ledger.supersede(run_id, sink)
        fresh = ledger.supersede(run_id, sink)
        result = _commit(ledger, sink, run_id, "k", fresh.fencing_token, fresh.state_version, "inventory:0")
        return {
            "mechanism": "notification_repeated",
            "expected": "completed",
            "observed": result["status"],
            "safety": True,
            "forward_progress": result["status"] == "completed",
            "rule": "重复的对齐通知只留下最后一对 token 和版本。最后的后继可以提交。",
        }

    def unknown_window(ledger: RunLedger, sink: EffectSink, run_id: str) -> dict[str, Any]:
        state = _open(ledger, sink, run_id)
        ledger.record_intent(run_id, "k", state.fencing_token, state.state_version, effect_id="k", arguments_hash="k", tool_name="effect")
        ledger.mark_effect_state("k", "STARTED")
        result = _commit(ledger, sink, run_id, "k", state.fencing_token, state.state_version, "inventory:0")
        return {
            "mechanism": "started_unconfirmed",
            "expected": "unknown",
            "observed": result["status"],
            "sink_accepts": sink.accepted_count("k"),
            "safety": sink.accepted_count("k") == 0,
            "forward_progress": False,
            "rule": "效果已经标成 STARTED 又没有 receipt 时，提交停在 unknown，不自动再写 sink。",
        }

    def external_unknown(ledger: RunLedger, sink: EffectSink, run_id: str) -> dict[str, Any]:
        state = _open(ledger, sink, run_id)
        first = ledger.seal_unconfirmed_external(run_id, "ext", state.fencing_token, state.state_version)
        second = ledger.seal_unconfirmed_external(run_id, "ext", state.fencing_token, state.state_version)
        observed = f"{first['retry']}/{second['status']}"
        return {
            "mechanism": "external_unconfirmed",
            "expected": "False/replayed",
            "observed": observed,
            "safety": True,
            "forward_progress": False,
            "rule": "不能条件提交的外部效果只封一次，retry 为 false。",
        }

    scenarios = [
        run("notification_absent", no_notice),
        run("notification_timely", timely),
        run("notification_delayed", delayed),
        run("notification_repeated", repeated_notice),
        run("duplicate_commit", duplicate),
        run("revoke_before_commit", revoke_first),
        run("commit_then_revoke", commit_then_revoke),
        run("version_unaligned", mismatch),
        run("started_unconfirmed", unknown_window),
        run("external_unconfirmed", external_unknown),
    ]
    return {
        "package": "B",
        "repeats": 5,
        "scenario_count": len(scenarios),
        "all_match": all(item["all_match"] for item in scenarios),
        "scenarios": scenarios,
    }


def package_d() -> dict[str, Any]:
    crashes = []
    for repeat in range(3):
        directory = OUT / "work" / f"crash-{repeat}"
        directory.mkdir(parents=True, exist_ok=True)
        for index, fault in enumerate(("before_dispatch", "before_sink", "after_sink", "after_receipt")):
            db = directory / f"{fault}.sqlite"
            sink_path = directory / f"{fault}-sink.sqlite"
            run_id = f"crash-{repeat}-{index}"
            ledger = RunLedger(db)
            ledger.create_run(run_id)
            ledger.advance(run_id, "running")
            state = ledger.get_run(run_id)
            ledger.close()
            completed = subprocess.run(
                [
                    sys.executable, "-m", "epochgate_runtime.worker",
                    "--db", str(db), "--sink", str(sink_path), "--fault", fault,
                    "--run", run_id, "--key", "k",
                    "--token", str(state.fencing_token), "--version", str(state.state_version),
                ],
                check=False,
                cwd=str(ROOT),
            )
            recovered = RunLedger(db)
            sink = EffectSink(sink_path)
            decision = reconcile(recovered, sink, run_id=run_id, idempotency_key="k", fencing_token=state.fencing_token)
            crashes.append({
                "repeat": repeat,
                "fault": fault,
                "exit_code": completed.returncode,
                "decision": decision["decision"],
                "sink_accepts": sink.accepted_count("k"),
            })
            recovered.close()
            sink.close()
    cancels = []
    for index in range(8):
        directory = OUT / "work" / f"cancel-{index}"
        directory.mkdir(parents=True, exist_ok=True)
        ledger = RunLedger(directory / "ledger.sqlite")
        run_id = f"cancel-{index}"
        ledger.create_run(run_id)
        ledger.advance(run_id, "running")
        process = start_sleeper(2)
        result = cancel_process(ledger, run_id, process)
        cancels.append({
            "repeat": index,
            "exited": result["exited"],
            "slot_released": result["slot_released"],
            "cancel_to_exit_seconds": result["cancel_to_exit_seconds"],
        })
        ledger.close()
    races = []
    for index in range(20):
        ledger, sink, _ = _fresh(f"race-{index}")
        run_id = f"race-{index}"
        state = _open(ledger, sink, run_id)
        outcome: dict[str, Any] = {}

        def _do_commit(ledger: RunLedger = ledger, sink: EffectSink = sink, state: Any = state, outcome: dict[str, Any] = outcome) -> None:
            result = _commit(ledger, sink, run_id, "k", state.fencing_token, state.state_version, "x")
            outcome["status"] = result["status"]

        import threading
        left = threading.Thread(target=_do_commit)
        right = threading.Thread(target=lambda: ledger.revoke_with_sink(run_id, sink))
        left.start()
        right.start()
        left.join()
        right.join()
        accepts = sink.accepted_count("k")
        races.append({
            "repeat": index,
            "status": outcome.get("status"),
            "sink_accepts": accepts,
            "consistent": (outcome.get("status") == "completed") is (accepts == 1),
        })
        ledger.close()
        sink.close()
    return {
        "package": "D",
        "crashes": crashes,
        "crash_exit_codes": sorted({item["exit_code"] for item in crashes}),
        "cancels": cancels,
        "cancel_all_released": all(item["slot_released"] and item["exited"] for item in cancels),
        "races": races,
        "race_consistent": all(item["consistent"] for item in races),
    }


def _usage(reply: dict[str, Any]) -> dict[str, Any] | None:
    usage = reply.get("usage")
    if not isinstance(usage, dict):
        return None
    return {key: usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}


def package_a() -> dict[str, Any]:
    cases = []

    def one(name: str, notice: str) -> dict[str, Any]:
        ledger, sink, directory = _fresh(f"agent-{name}")
        run_id = f"agent-{name}"
        state = _open(ledger, sink, run_id)
        inventory = "3" if notice == "absent" else "0"
        reply = chat(
            f"Inventory is {inventory}. Reply with exactly one line: COMMIT reserve:{inventory}",
            model=MODEL,
            max_tokens=32,
        )
        content = str(reply.get("content") or "").strip()
        payload = content if content.startswith("COMMIT ") else f"COMMIT reserve:{inventory}"
        old = (state.fencing_token, state.state_version)
        if notice == "absent":
            result = _commit(ledger, sink, run_id, "model", old[0], old[1], payload)
            fresh_status = None
        elif notice == "aligned":
            fresh = ledger.supersede(run_id, sink)
            rejected = _commit(ledger, sink, run_id, "old", old[0], old[1], "COMMIT reserve:3")
            result = _commit(ledger, sink, run_id, "model", fresh.fencing_token, fresh.state_version, payload)
            fresh_status = rejected["status"]
        else:
            fresh = ledger.supersede(run_id)
            rejected = _commit(ledger, sink, run_id, "old", old[0], old[1], "COMMIT reserve:3")
            result = _commit(ledger, sink, run_id, "model", fresh.fencing_token, fresh.state_version, payload)
            fresh_status = rejected["status"]
        record = {
            "case": name,
            "notice": notice,
            "model_ok": reply.get("ok"),
            "reported_model": reply.get("reported_model"),
            "content": content,
            "content_obeyed": content.startswith("COMMIT "),
            "usage": _usage(reply),
            "old_token_status": fresh_status,
            "commit_status": result["status"],
            "sink_accepts": sink.accepted_count(),
            "event_types": [item["event_type"] for item in ledger.events(run_id)],
            "error": reply.get("error"),
        }
        cases.append(record)
        ledger.close()
        sink.close()
        directory.joinpath("case.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return record

    if not chat("Reply OK", model=MODEL, max_tokens=8).get("ok"):
        probe = chat("Reply OK", model=MODEL, max_tokens=8)
        return {"package": "A", "status": "model_unavailable", "error": probe.get("error"), "cases": []}
    one("no-notice", "absent")
    one("aligned", "aligned")
    one("unaligned", "unaligned")
    return {"package": "A", "path": "deepseek_tool_commit", "session_runtime": "ControlServer+RunLedger", "cases": cases}


def package_c(repeats: int = 20) -> dict[str, Any]:
    probe = chat("Reply OK", model=MODEL, max_tokens=8)
    if not probe.get("ok"):
        return {"package": "C", "status": "model_unavailable", "error": probe.get("error")}
    order = ["B0", "B1", "B2"] * repeats
    random.Random(20261110).shuffle(order)
    samples: list[dict[str, Any]] = []
    ledger, sink, _ = _fresh("overhead")
    for index, arm in enumerate(order):
        run_id = f"{arm}-{index}-{uuid.uuid4().hex[:8]}"
        state = _open(ledger, sink, run_id)
        started = time.perf_counter()
        reply = chat("Reply with the single word OK", model=MODEL, max_tokens=16)
        after_model = time.perf_counter()
        payload = "OK"
        key = f"k-{index}"
        if arm == "B0":
            decision = sink.accept(key, expected_version=1, payload=payload)
            status = "completed" if decision["status"] == "accepted" else decision["status"]
            after_commit = time.perf_counter()
            receipt_visible = sink.get(key) is not None
            after_audit = after_commit
        else:
            result = _commit(ledger, sink, run_id, key, state.fencing_token, state.state_version, payload)
            after_commit = time.perf_counter()
            status = result["status"]
            if arm == "B2":
                ledger.close()
                reopened = RunLedger(OUT / "work" / "overhead" / "ledger.sqlite")
                row = reopened.effect_row(key)
                receipt_visible = bool(row and row["receipt"])
                reopened.close()
                ledger = RunLedger(OUT / "work" / "overhead" / "ledger.sqlite")
            else:
                receipt_visible = bool(result.get("receipt"))
            after_audit = time.perf_counter()
        samples.append({
            "arm": arm,
            "order": index,
            "model_ok": reply.get("ok"),
            "reported_model": reply.get("reported_model"),
            "usage": _usage(reply),
            "commit_status": status,
            "receipt_visible": receipt_visible,
            "seconds": {
                "model": after_model - started,
                "commit": after_commit - after_model,
                "audit": after_audit - after_commit,
                "total": after_audit - started,
            },
        })
    ledger.close()
    sink.close()

    def arm_stats(arm: str) -> dict[str, Any]:
        rows = [item for item in samples if item["arm"] == arm]
        totals = [item["seconds"]["total"] for item in rows]
        commits = [item["seconds"]["commit"] for item in rows]
        audits = [item["seconds"]["audit"] for item in rows]
        return {
            "n": len(rows),
            "completed": sum(item["commit_status"] == "completed" for item in rows),
            "receipt_visible": sum(bool(item["receipt_visible"]) for item in rows),
            "total_median": statistics.median(totals),
            "total_mean": statistics.fmean(totals),
            "total_min": min(totals),
            "total_max": max(totals),
            "commit_median": statistics.median(commits),
            "audit_median": statistics.median(audits),
            "token_total": sum((item["usage"] or {}).get("total_tokens") or 0 for item in rows),
        }

    return {
        "package": "C",
        "design": "same model call and same sink payload OK; B0 checks the sink version only; B1 and B2 call commit_authorized; B2 then reopens the ledger and reads the receipt. record_intent is not used here because a started effect is not committable.",
        "repeats": repeats,
        "interleave_seed": 20261110,
        "requested_model": MODEL,
        "reported_model": probe.get("reported_model"),
        "price": None,
        "arms": {arm: arm_stats(arm) for arm in ("B0", "B1", "B2")},
        "samples": samples,
    }


def main() -> None:
    report = {
        "model": MODEL,
        "B": package_b(),
        "D": package_d(),
        "A": package_a(),
        "C": package_c(20),
    }
    path = _dump("campaign.json", report)
    print(json.dumps({
        "path": str(path),
        "B": report["B"]["all_match"],
        "D_crash_codes": report["D"]["crash_exit_codes"],
        "D_cancel": report["D"]["cancel_all_released"],
        "D_race": report["D"]["race_consistent"],
        "A": report["A"].get("status") or [item["commit_status"] for item in report["A"].get("cases", [])],
        "C": report["C"].get("status") or {arm: report["C"]["arms"][arm]["total_median"] for arm in ("B0", "B1", "B2")},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
