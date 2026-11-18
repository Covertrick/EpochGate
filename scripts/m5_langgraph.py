"""LangGraph 薄适配。不修改 LangGraph 或 EpochGate 的内核，也不补造 Claim。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TypedDict

from m4_matrix import git_sha, model_proposals, protocol_sha, successor_proposal
from m4_tasks import M5_TASKS
from reliability_contract import SharedReceiver, sha256_bytes, sha256_text

FAULTS = ("notice_missing", "notice_delivered", "response_lost", "interrupt_resume")


class GraphState(TypedDict, total=False):
    digest: str
    writes: int


def _load_graph():
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    return MemorySaver, END, START, StateGraph, Command, interrupt


def run_mode(task: dict[str, Any], fault: str, mode: str, payload: bytes, root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    root.mkdir(parents=True, exist_ok=True)
    MemorySaver, END, START, StateGraph, Command, interrupt = _load_graph()
    log: list[str] = []
    receiver = SharedReceiver(root, task["relative"])
    key = f"{task['task_id']}-{mode}-{fault}"

    def effect(state: GraphState) -> GraphState:
        if fault == "interrupt_resume" and not log and receiver.accepted_count(key) == 0:
            interrupt({"boundary": "before_effect"})
        digest = sha256_bytes(payload)
        if mode == "R0":
            log.append(digest)
            (root / "native-effect.log").write_text("\n".join(log), encoding="utf-8")
            happened = len(log)
        else:
            receiver.accept(effect_key=key, attempt_id=key, payload=payload)
            happened = receiver.accepted_count(key)
        if fault == "response_lost" and happened == 1:
            interrupt({"boundary": "after_accept"})
        return {"digest": digest, "writes": happened}

    graph = StateGraph(GraphState)
    graph.add_node("effect", effect)
    graph.add_edge(START, "effect")
    graph.add_edge("effect", END)
    app = graph.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": key}}
    result = app.invoke({}, config)
    paused = isinstance(result, dict) and bool(result.get("__interrupt__"))
    if fault in {"interrupt_resume", "response_lost"}:
        app.invoke(Command(resume=True), config)
    duplicate = len(log) > 1 if mode == "R0" else receiver.accepted_count(key) > 1
    return {
        "task_id": task["task_id"],
        "family": task["family"],
        "run_id": key,
        "attempt_id": key,
        "arm": mode,
        "mode": mode,
        "runtime": "langgraph",
        "fault": fault,
        "git_sha": git_sha(),
        "protocol_sha256": protocol_sha(),
        "task_manifest_sha256": sha256_text(json.dumps({"task_id": task["task_id"], "event": task["event"]}, sort_keys=True)),
        "image_digest": "not_applicable",
        "claim": "NOT_SUPPORTED",
        "capability_owner": "langgraph_native" if mode == "R0" else "external_receiver",
        "paused": paused,
        "native_writes": len(log) if mode == "R0" else None,
        "receiver_accepts": None if mode == "R0" else receiver.accepted_count(key),
        "duplicate_effect": duplicate,
        "observable": True,
        "blocked": False,
        "elapsed_s": time.perf_counter() - started,
        "end_reason": "langgraph_public_api",
    }


def run_m5(out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        _load_graph()
    except Exception as exc:
        summary = {"blocked": True, "error_type": type(exc).__name__, "reason": "langgraph_import_failed", "rows": []}
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    for task in M5_TASKS:
        for fault in FAULTS:
            mapped = "S2" if fault == "notice_missing" else "S1"
            key = (task["task_id"], mapped)
            if key not in cache:
                cache[key] = model_proposals(task, mapped)
            proposal = cache[key]
            if not proposal.get("ok"):
                for mode in ("R0", "R1"):
                    rows.append({
                        "task_id": task["task_id"], "family": task["family"], "arm": mode, "runtime": "langgraph",
                        "fault": fault, "blocked": True, "error_type": proposal.get("error"), "claim": "NOT_SUPPORTED",
                    })
                continue
            follow = successor_proposal(task, task["initial"]) if fault == "notice_delivered" else {"ok": False}
            for mode in ("R0", "R1"):
                cell = out / f"{task['task_id']}__{mode}__{fault}"
                try:
                    row = run_mode(task, fault, mode, proposal["old"], cell)
                except Exception as exc:
                    row = {
                        "task_id": task["task_id"], "family": task["family"], "arm": mode, "runtime": "langgraph",
                        "fault": fault, "blocked": True, "error_type": type(exc).__name__,
                        "claim": "NOT_SUPPORTED", "end_reason": "adapter_error",
                    }
                row["model"] = proposal.get("model")
                row["proposal_fault"] = mapped
                row["successor_proposed"] = bool(follow.get("ok"))
                row["input_tokens"] = proposal.get("tokens", (0, 0))[0]
                row["output_tokens"] = proposal.get("tokens", (0, 0))[1]
                (cell / "evidence.json").parent.mkdir(parents=True, exist_ok=True)
                (cell / "evidence.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
                rows.append(row)
    coding = {
        "family": "coding",
        "blocked": True,
        "error_type": "feasibility_failed",
        "reason": "没有通过可行性检查的真实仓库进入本轮，不计为模型失败",
    }
    summary = {
        "n": len(rows),
        "blocked": sum(1 for row in rows if row.get("blocked")),
        "coding": coding,
        "kernel_modified": False,
        "rows": rows,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("results/runtime/correctness/m5"))
    args = parser.parse_args()
    summary = run_m5(args.out)
    print(json.dumps({"n": summary["n"], "blocked": summary["blocked"], "kernel_modified": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
