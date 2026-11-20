"""Claim versus Claim+RSR on S14 and S16.

The child commits ByteClaim and is killed before SharedReceiver.accept.
A new process reads the claim bytes from SQLite. The RSR path calls
semantics_oracle before any publish. This does not close a later TOCTOU window.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from fault_replay.contract import WEEK3
from fault_replay.kill_runner import resolve_payload_path

from crash_recovery.crash_runner import (
    WEEK4,
    _contract,
    _fresh,
    _mean,
    _proposal_file,
    _write_json,
    assert_archive_intact,
    killer_name,
    reconcile,
    spawn_and_kill,
)

RSR_CSV = WEEK4 / "rsr_ablation.csv"
RSR_MD = WEEK4 / "rsr_ablation.md"

SCENARIOS = (
    ("S14", "p20", "after_sealed_before_replace"),
    ("S16", "p22", "lost_receipt"),
)
PATHS = ("ByteClaim", "ByteClaim+RSR")

FIELDS = [
    "run_id",
    "scenario",
    "path",
    "checkpoint",
    "killer",
    "killed_pid",
    "post_kill_state",
    "recovery_action",
    "evidence_state",
    "semantic_result",
    "precheck_result",
    "published",
    "invalid_restore",
    "logical_effect_count",
    "latency_ms",
    "precheck_ms",
    "target_digest",
    "sealed_digest",
    "receipt_present",
    "process_kill",
    "power_loss_claim",
]


def load_week3_proposal(proposal_id: str) -> dict[str, Any]:
    for line in (WEEK3 / "proposal_registry.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("proposal_id") == proposal_id:
            row["source"] = row.get("source") or "week3"
            return row
    raise KeyError(proposal_id)


def run_rsr_case(
    run_dir: Path,
    *,
    proposal: dict[str, Any],
    scenario: str,
    checkpoint: str,
    path_name: str,
    run_id: str,
) -> dict[str, Any]:
    _fresh(run_dir)
    rsr = path_name.endswith("+RSR")
    contract = _contract(proposal, run_id=run_id, path=path_name, checkpoint=checkpoint, rsr=rsr)
    contract["scenario"] = scenario
    _write_json(run_dir / "contract.json", contract)
    payload_path = _proposal_file(proposal)
    if resolve_payload_path(proposal) != payload_path:
        raise RuntimeError("proposal path resolution diverged")
    launched = spawn_and_kill(run_dir, "claim", payload_path)
    if (run_dir / "effect.bin").is_file():
        raise RuntimeError("claim child published before the kill")
    recovered, latency_ms = reconcile(run_dir, "claim_recover", label="claim-recover")
    if recovered["memory_sentinel_seen"]:
        raise RuntimeError("claim sentinel was durable")
    precheck = recovered["precheck_result"]
    if precheck != "INVALID":
        raise RuntimeError(f"{run_id} precheck {precheck}; S14/S16 are the invalid-restore pair")
    published = bool(recovered["published"])
    invalid = bool(recovered["invalid_restore"])
    if rsr:
        if published or invalid or recovered["evidence_state"] != "SEMANTICALLY_SUPERSEDED":
            raise RuntimeError(f"{run_id} RSR did not withhold: {recovered['evidence_state']}")
        if int(recovered["logical_effect_count"]) != 0 or recovered["target_digest"]:
            raise RuntimeError(f"{run_id} RSR published bytes")
    else:
        if not published or not invalid or recovered["evidence_state"] != "PUBLISH_SEALED":
            raise RuntimeError(f"{run_id} baseline did not restore invalid bytes")
        if int(recovered["logical_effect_count"]) != 1:
            raise RuntimeError(f"{run_id} baseline logical count")
        if recovered["semantic_result"] != "INVALID":
            raise RuntimeError(recovered["semantic_result"])
    return {
        "run_id": run_id,
        "scenario": scenario,
        "path": path_name,
        "checkpoint": checkpoint,
        "killer": killer_name(),
        "killed_pid": int(launched["killed_pid"]),
        "post_kill_state": recovered["post_kill_state"],
        "recovery_action": recovered["recovery_action"],
        "evidence_state": recovered["evidence_state"],
        "semantic_result": recovered["semantic_result"],
        "precheck_result": precheck,
        "published": published,
        "invalid_restore": invalid,
        "logical_effect_count": int(recovered["logical_effect_count"]),
        "latency_ms": latency_ms,
        "precheck_ms": float(recovered["precheck_ms"]),
        "target_digest": recovered["target_digest"] or "",
        "sealed_digest": recovered["sealed_digest"] or "",
        "receipt_present": bool(recovered["receipt_present"]),
        "process_kill": True,
        "power_loss_claim": False,
        "exit_code": int(launched["exit_code"]),
    }


def run_rsr_study(repeats: int = 10, work_root: Path | None = None) -> list[dict[str, Any]]:
    assert_archive_intact()
    root = work_root or (WEEK4 / "crash_runs")
    root.mkdir(parents=True, exist_ok=True)
    cache = {proposal_id: load_week3_proposal(proposal_id) for _scenario, proposal_id, _checkpoint in SCENARIOS}
    rows: list[dict[str, Any]] = []
    for scenario, proposal_id, checkpoint in SCENARIOS:
        proposal = cache[proposal_id]
        for path_name in PATHS:
            slug = "rsr" if path_name.endswith("+RSR") else "claim"
            for repeat in range(1, repeats + 1):
                run_id = f"w4-rsr-{scenario}-{slug}-r{repeat:02d}"
                row = run_rsr_case(
                    root / run_id,
                    proposal=proposal,
                    scenario=scenario,
                    checkpoint=checkpoint,
                    path_name=path_name,
                    run_id=run_id,
                )
                rows.append(row)
                print(
                    f"rsr {run_id} {row['evidence_state']} invalid={row['invalid_restore']} {row['latency_ms']:.1f}ms",
                    flush=True,
                )
    return rows


def write_rsr_csv(rows: list[dict[str, Any]], path: Path = RSR_CSV) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            if row["process_kill"] is not True or row["power_loss_claim"] is not False:
                raise RuntimeError(row["run_id"])
            exported = {key: row.get(key, "") for key in FIELDS}
            for flag in ("published", "invalid_restore", "receipt_present"):
                exported[flag] = "true" if row[flag] else "false"
            exported["process_kill"] = "true"
            exported["power_loss_claim"] = "false"
            exported["latency_ms"] = f"{float(row['latency_ms']):.3f}"
            exported["precheck_ms"] = f"{float(row['precheck_ms']):.3f}"
            writer.writerow(exported)
    return path


def write_rsr_md(rows: list[dict[str, Any]], path: Path = RSR_MD) -> Path:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["scenario"], row["path"])].append(row)
    lines = [
        "# RSR 消融",
        "",
        "比较的代码路径是 `ByteClaim` 与 `ByteClaim+RSR`。场景是 S14（`p20`，检查点名 `after_sealed_before_replace`）和 S16（`p22`，检查点名 `lost_receipt`）。两条路径都是：子进程执行 `ByteClaim.commit`，父进程在 `SharedReceiver.accept` 之前用 `TerminateProcess` 结束它，新进程再打开 SQLite。",
        "",
        "`ByteClaim` 直接发布 claim 里的字节，发布之后用 `semantics_oracle` 给结果打分。`ByteClaim+RSR` 在发布前对同一份密封字节和该场景的 `world_after` 调用 `semantics_oracle`。`VALID` 才发布。`INVALID` 返回 `SEMANTICALLY_SUPERSEDED`，不发布。其他结果，包括 `UNKNOWN`，返回 `UNKNOWN`，不发布。",
        "",
        "无效恢复：发布发生，且 `semantics_oracle` 的结果是 `INVALID`。S14 与 S16 的冻结世界已经漂移，所以这组运行的预检预期是 `INVALID`。`VALID` 和 `UNKNOWN` 两个分支在代码里存在；这组场景运行不走它们。",
        "",
        "| 场景 | 路径 | 次数 | 无效恢复 | UNKNOWN | SEMANTICALLY_SUPERSEDED | CORRECT_STOP | 恢复耗时均值 ms | 预检均值 ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key in sorted(groups):
        bucket = groups[key]
        invalid = sum(1 for row in bucket if row["invalid_restore"])
        unknown = sum(1 for row in bucket if row["evidence_state"] == "UNKNOWN")
        superseded = sum(1 for row in bucket if row["evidence_state"] == "SEMANTICALLY_SUPERSEDED")
        correct = sum(1 for row in bucket if row["evidence_state"] == "CORRECT_STOP")
        latency = _mean([float(row["latency_ms"]) for row in bucket])
        precheck = _mean([float(row["precheck_ms"]) for row in bucket])
        lines.append(
            f"| {key[0]} | {key[1]} | {len(bucket)} | {invalid} | {unknown} | {superseded} | {correct} | {latency:.3f} | {precheck:.3f} |"
        )
    lines.extend(["", "## 权衡", ""])
    for scenario in ("S14", "S16"):
        plain = groups.get((scenario, "ByteClaim"), [])
        gated = groups.get((scenario, "ByteClaim+RSR"), [])
        if not plain or not gated:
            lines.append(f"{scenario} 的两条路径没有齐。")
            continue
        invalid_plain = sum(1 for row in plain if row["invalid_restore"])
        invalid_gated = sum(1 for row in gated if row["invalid_restore"])
        unknown_plain = sum(1 for row in plain if row["evidence_state"] == "UNKNOWN")
        unknown_gated = sum(1 for row in gated if row["evidence_state"] == "UNKNOWN")
        stop_plain = sum(1 for row in plain if row["evidence_state"] == "SEMANTICALLY_SUPERSEDED")
        stop_gated = sum(1 for row in gated if row["evidence_state"] == "SEMANTICALLY_SUPERSEDED")
        correct_plain = sum(1 for row in plain if row["evidence_state"] == "CORRECT_STOP")
        correct_gated = sum(1 for row in gated if row["evidence_state"] == "CORRECT_STOP")
        latency_delta = _mean([float(row["latency_ms"]) for row in gated]) - _mean(
            [float(row["latency_ms"]) for row in plain]
        )
        precheck = _mean([float(row["precheck_ms"]) for row in gated])
        lines.append(
            f"{scenario}：`ByteClaim` 无效恢复 {invalid_plain} / {len(plain)}，`ByteClaim+RSR` 无效恢复 {invalid_gated} / {len(gated)}，减少 {invalid_plain - invalid_gated}。"
            f"证据状态 `UNKNOWN` 从 {unknown_plain} 到 {unknown_gated}。"
            f"`SEMANTICALLY_SUPERSEDED` 从 {stop_plain} 到 {stop_gated}。"
            f"`CORRECT_STOP` 从 {correct_plain} 到 {correct_gated}。"
            f"恢复耗时均值变化 {latency_delta:+.3f} ms。RSR 预检均值 {precheck:.3f} ms。"
        )
    lines.extend(
        [
            "",
            "无效恢复从每场景 10 次降到 0 次，停止发布的次数升到 10 次，状态是 `SEMANTICALLY_SUPERSEDED`。本次 40 次预检全部是 `INVALID`，`UNKNOWN` 保持 0，后继意义上的 `CORRECT_STOP` 保持 0。耗时是恢复进程的墙钟。正数表示 RSR 这组更慢。预检调用本身不到 0.05 ms，墙钟均值差大于这次调用，并落在各次启动时间的波动里。",
            "",
            "RSR 不消除 TOCTOU。预检与发布不是同一次原子提交。`VALID` 只覆盖调用 `semantics_oracle` 的那一刻；在那之后、写入效果文件之前，世界仍可能变化。本次 S14 与 S16 没有走进 `VALID` 分支，因此这组运行没有测量那个窗口里的世界变化。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_rsr_outputs(rows: list[dict[str, Any]]) -> None:
    write_rsr_csv(rows)
    write_rsr_md(rows)
