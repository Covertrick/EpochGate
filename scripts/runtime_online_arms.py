"""同一模型、同一任务、同一容器隔离下的在线 B0/B1/B2。不写密钥，不编造价格。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.measurements import measure_stale_world
from epochgate_runtime.sandbox import (
    container_boundary,
    exec_in,
    exec_root,
    lock_workspace,
    publish_container,
    start_container,
    stop_container,
    write_file,
)

from runtime_deepseek import chat

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime"
MODEL = "deepseek-flash"
PROMPT = (
    "Write the complete source of bug.py.\n"
    "Define value() so it returns the integer 1.\n"
    "Output only Python source. No markdown.\n"
)
GOLD = "def value():\n    return 1\n"


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_source(text: str) -> tuple[str, bool]:
    raw = text.strip()
    changed = False
    if raw.startswith("```"):
        changed = True
        lines = raw.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    if raw and not raw.endswith("\n"):
        raw += "\n"
        changed = True
    return raw, changed


def _usage(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    return {key: value for key, value in payload.items() if isinstance(value, int)}


def _score(container: str) -> bool:
    result = exec_root(
        container,
        ["python", "-B", "-c", "import bug; raise SystemExit(0 if bug.value() == 1 else 1)"],
    )
    return result.returncode == 0


def _apply(arm: str, container: str, content: str, ledger: RunLedger, run_id: str) -> dict[str, Any]:
    if arm == "B0":
        exec_root(container, ["rm", "-f", "/workspace/bug.py"])
        write_file(container, "bug.py", content)
        return {"arm": arm, "ledger": False, "receipt": None, "published": None, "score_passed": _score(container)}
    ledger.create_run(run_id)
    ledger.advance(run_id, "running")
    state = ledger.get_run(run_id)
    reserved = ledger.record_intent(
        run_id,
        run_id,
        state.fencing_token,
        state.state_version,
        effect_id=run_id,
        arguments_hash="bug.py",
        tool_name="write_file",
    )
    if not reserved.get("allowed"):
        return {"arm": arm, "allowed": False, "receipt": None, "score_passed": False}
    if arm == "B1":
        exec_root(container, ["rm", "-f", "/workspace/bug.py"])
        write_file(container, "bug.py", content)
        effect = ledger.effect_row(run_id)
        return {
            "arm": arm,
            "ledger": True,
            "fencing_token": state.fencing_token,
            "effect_state": None if effect is None else effect.get("effect_state"),
            "receipt": None if effect is None else effect.get("receipt"),
            "score_passed": _score(container),
        }
    exec_root(container, ["rm", "-f", "/workspace/bug.py", "/scratch/.eg-staging/bug.py"])
    staged = exec_in(
        container,
        ["python", "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())", "/scratch/.eg-staging/bug.py"],
        content.encode("utf-8"),
    )
    if staged.returncode != 0:
        return {"arm": arm, "error": staged.stderr.decode("utf-8", "replace")[-300:], "score_passed": False, "receipt": None}
    published = publish_container(
        ledger,
        container=container,
        run_id=run_id,
        idempotency_key=run_id,
        fencing_token=state.fencing_token,
        relative_path="bug.py",
        staging_path="/scratch/.eg-staging/bug.py",
    )
    effect = ledger.effect_row(run_id)
    return {
        "arm": arm,
        "ledger": True,
        "published": published.get("published"),
        "status": published.get("status"),
        "sha256": published.get("sha256"),
        "receipt": None if effect is None else effect.get("receipt"),
            "score_passed": _score(container),
        }


def _one(arm: str, container: str, ledger_path: Path, repeat: int, slot: int) -> dict[str, Any]:
    started = time.perf_counter()
    completion = chat(PROMPT, model=MODEL, max_tokens=256)
    source, normalized = normalize_source(completion.get("content") or "")
    applied: dict[str, Any]
    ledger = RunLedger(ledger_path)
    try:
        if not completion.get("ok"):
            applied = {"arm": arm, "score_passed": False, "receipt": None, "error": completion.get("error")}
        else:
            applied = _apply(arm, container, source, ledger, f"{arm}-c-r{repeat}-s{slot}-{uuid.uuid4().hex[:8]}")
    finally:
        ledger.close()
    elapsed = time.perf_counter() - started
    return {
        "arm": arm,
        "repeat": repeat,
        "slot": slot,
        "seconds": elapsed,
        "ok": bool(completion.get("ok")),
        "requested_model": MODEL,
        "reported_model": completion.get("reported_model"),
        "usage": _usage(completion.get("usage")),
        "source_normalized": normalized,
        "source_sha256": __import__("hashlib").sha256(source.encode("utf-8")).hexdigest() if source else None,
        "source_equals_gold": source == GOLD,
        "apply": {key: value for key, value in applied.items() if key != "error" or True},
        "error": None if completion.get("ok") else completion.get("error"),
    }


def _batch(arm: str, containers: list[str], ledger_dir: Path, repeat: int) -> dict[str, Any]:
    started = time.perf_counter()
    samples: list[dict[str, Any] | None] = [None] * len(containers)

    def run_slot(index: int) -> None:
        ledger_path = ledger_dir / f"{arm}-r{repeat}-s{index}.sqlite"
        try:
            samples[index] = _one(arm, containers[index], ledger_path, repeat, index)
        except Exception as exc:  # noqa: BLE001 - keep the failed slot in the result
            samples[index] = {
                "arm": arm,
                "repeat": repeat,
                "slot": index,
                "ok": False,
                "error": type(exc).__name__ + ": " + str(exc)[:300],
                "apply": {"score_passed": False, "receipt": None},
                "usage": None,
            }

    threads = [threading.Thread(target=run_slot, args=(index,)) for index in range(len(containers))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.perf_counter() - started
    done = [item for item in samples if item is not None]
    return {
        "arm": arm,
        "concurrency": len(containers),
        "repeat": repeat,
        "batch_seconds": elapsed,
        "completed": sum(1 for item in done if item["apply"].get("score_passed")),
        "samples": done,
    }


def _summarize(level: dict[str, Any]) -> dict[str, Any]:
    batches = level["batches"]
    return {
        "concurrency": level["concurrency"],
        "repeats": len(batches),
        "batch_seconds": [item["batch_seconds"] for item in batches],
        "completed_per_batch": [item["completed"] for item in batches],
    }


def _stale_model() -> dict[str, Any]:
    completion = chat(
        "Inventory is 3. A reservation is valid only while inventory stays 3. Reply with exactly RESERVE 1 or SKIP.",
        model=MODEL,
        max_tokens=64,
    )
    root = OUT / "stale_world_model"
    deterministic = measure_stale_world(root / "control")
    trajectory: dict[str, Any] = {
        "requested_model": MODEL,
        "ok": bool(completion.get("ok")),
        "reported_model": completion.get("reported_model"),
        "usage": _usage(completion.get("usage")),
        "error": None if completion.get("ok") else completion.get("error"),
        "reply": (completion.get("content") or "")[:500],
        "deterministic_control": {
            "expired_status": deterministic["with_epoch_advance"]["expired_status"],
            "valid_published": deterministic["with_epoch_advance"]["valid_published"],
            "bytes": deterministic["with_epoch_advance"]["bytes"],
        },
    }
    if not completion.get("ok"):
        trajectory["model_publish"] = None
        return trajectory
    from epochgate_runtime.api import Runtime

    runtime = Runtime.open(root / "model.sqlite", data_dir=root / "model-run")
    try:
        inventory = root / "model-inventory.txt"
        inventory.write_text("3\n", encoding="utf-8")
        first = runtime.reserve("model-stale", "workspace/order.txt")
        inventory.write_text("0\n", encoding="utf-8")
        runtime.ledger.append_event(
            "model-stale",
            "world_changed",
            {"inventory_at_grant": 3, "inventory_before_commit": 0, "source": "model_reply_not_applied_with_old_token"},
        )
        second = runtime.reserve("model-stale", "workspace/order.txt")
        proposal = (completion.get("content") or "").encode("utf-8")
        stale = runtime.publish(first, proposal)
        fresh = runtime.publish(second, b"inventory:0\n")
        target = runtime.workspace / "model-stale" / "order.txt"
        body = target.read_bytes() if target.is_file() else b""
        trajectory["model_publish"] = {
            "expired_status": stale.get("status"),
            "expired_published": stale.get("published"),
            "valid_published": fresh.get("published"),
            "bytes": body.decode("utf-8", "replace"),
        }
    finally:
        runtime.close()
    return trajectory


def main() -> None:
    probe = chat("Reply with exactly: API_OK", model=MODEL, max_tokens=32)
    probe_out = {
        "requested_model": MODEL,
        "product_name_expected_by_repo": "DeepSeek-V4.1-Flash",
        "ok": bool(probe.get("ok")),
        "reported_model": probe.get("reported_model"),
        "usage": _usage(probe.get("usage")),
        "content": probe.get("content"),
        "error": None if probe.get("ok") else probe.get("error"),
        "url": probe.get("url"),
        "thinking": probe.get("thinking"),
    }
    _write(OUT / "model_probe.json", probe_out)
    print(f"probe ok={probe_out['ok']} reported_model={probe_out['reported_model']}")
    if not probe.get("ok"):
        _write(
            OUT / "online_b0b1b2.json",
            {
                "status": "api_rejected",
                "requested_model": MODEL,
                "error": probe.get("error"),
                "concurrency_ran": [],
                "price": None,
                "price_reason": "no successful response; price is not invented",
            },
        )
        return

    base = {"B0": "eg-paper-b0", "B1": "eg-paper-b1", "B2": "eg-paper-b2"}
    extra = {"B0": "eg-paper-b0b", "B1": "eg-paper-b1b", "B2": "eg-paper-b2b"}
    started_names = list(base.values())
    started = time.perf_counter()
    try:
        for name in base.values():
            start_container(name)
        lock_workspace(base["B2"])
        startup = time.perf_counter() - started
        boundaries = {arm: container_boundary(name) for arm, name in base.items()}
        ledger_dir = OUT / "online_ledgers"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        levels = []
        level1 = {"concurrency": 1, "batches": []}
        for arm, name in base.items():
            for repeat in range(3):
                level1["batches"].append(_batch(arm, [name], ledger_dir, repeat))
        levels.append(level1)
        flat = [sample for batch in level1["batches"] for sample in batch["samples"]]
        total_tokens = sum((sample.get("usage") or {}).get("total_tokens") or 0 for sample in flat)
        all_scored = all(sample["apply"].get("score_passed") for sample in flat)
        all_ok = all(sample["ok"] for sample in flat)
        extended: list[Any] = []
        if all_ok and all_scored and total_tokens and total_tokens <= 4000:
            for name in extra.values():
                start_container(name)
                started_names.append(name)
            lock_workspace(extra["B2"])
            level2 = {"concurrency": 2, "batches": []}
            for arm in ("B0", "B1", "B2"):
                pair = [base[arm], extra[arm]]
                for repeat in range(3):
                    level2["batches"].append(_batch(arm, pair, ledger_dir, repeat))
            levels.append(level2)
            extended.append(2)
        else:
            extended.append("stopped_after_1")
        report = {
            "status": "measured",
            "requested_model": MODEL,
            "reported_models": sorted({sample.get("reported_model") for level in levels for batch in level["batches"] for sample in batch["samples"] if sample.get("reported_model")}),
            "task": "bug.py value() == 1",
            "isolation": {
                "image": "python:3.10-slim",
                "container_flags": ["--network", "none", "--security-opt", "no-new-privileges", "--memory", "256m", "--pids-limit", "64"],
                "boundaries": boundaries,
                "b2_workspace_locked_to_uid_1001": True,
                "score_user": "root",
                "clock_includes": ["api", "tool_apply"],
                "container_start_seconds": startup,
                "extend_token_ceiling": 4000,
            },
            "concurrency_ran": [level["concurrency"] for level in levels],
            "extend_decision": extended,
            "token_total_concurrency_1": total_tokens,
            "price": None,
            "price_reason": "API usage has token counts and no price field. No price was invented.",
            "summary": [_summarize(level) for level in levels],
            "levels": levels,
            "gpu_reclaimed": False,
            "resource_observation": "not_observable",
        }
        _write(OUT / "online_b0b1b2.json", report)
        print(f"online concurrency={report['concurrency_ran']} c1_tokens={total_tokens} scored={all_scored}")
    finally:
        for name in started_names:
            try:
                stop_container(name)
            except (OSError, RuntimeError, TimeoutError):
                pass
    stale = _stale_model()
    _write(OUT / "stale_world_model.json", stale)
    print(f"stale_model ok={stale['ok']} publish={stale.get('model_publish')}")


if __name__ == "__main__":
    main()
