"""Replay four paths on the frozen Week 5 manifest. Does not rewrite that manifest."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mechanism_validation.runtime.hashing import canonical_bytes, runtime_code_hash, sha256_bytes
from mechanism_validation.runtime.harness import execute_path
from mechanism_validation.runtime.receiver_process import ReceiverProcess

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).resolve().parent / "replay_manifest.json"
SIDECAR = MANIFEST.with_suffix(".sha256")
JOINT = Path(__file__).resolve().parent / "replay_joint.csv"
REPORT = Path(__file__).resolve().parent / "coverage_report.md"
INDEPENDENCE = ROOT / "mechanism_validation" / "architecture" / "mechanism_independence.json"
COMPARISON = ROOT / "mechanism_validation" / "proposals" / "generator_comparison.csv"

FIELDS = [
    "run_id",
    "proposal_id",
    "scenario_id",
    "generator_id",
    "path_id",
    "fault_id",
    "attempt_id",
    "code_hash",
    "proposal_hash",
    "world_hash",
    "authority_hash",
    "oracle_version",
    "receiver_version",
    "timestamp",
    "process_kill",
    "power_loss_claim",
    "family",
    "seed",
    "path_order_index",
    "arm_outcome",
    "published_new",
    "withheld",
    "recovery",
    "confirmed_effect",
    "effect_count",
    "duplicate_effect",
    "conflict_observed",
    "semantic_validity",
    "physical_effect_state",
    "authority_decision",
    "successor_status",
    "stop_correctness",
    "error",
]


def _fault_id(proposal: dict[str, Any]) -> str:
    points = proposal.get("fault_schedule", {}).get("points") or []
    if not points:
        return "NONE"
    action = points[0]["actions"][0]["op"]
    return f"{points[0]['point']}:{action}"


def _load_manifest() -> dict[str, Any]:
    text = MANIFEST.read_text(encoding="utf-8")
    parsed = json.loads(text)
    digest = sha256_bytes(canonical_bytes(parsed))
    stored = SIDECAR.read_text(encoding="utf-8").strip()
    if digest != stored:
        raise RuntimeError("replay manifest hash does not match the sidecar written before replay")
    if parsed.get("frozen_before_replay") is not True:
        raise RuntimeError("manifest was not frozen")
    return parsed


def _write_report(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    decision = json.loads(INDEPENDENCE.read_text(encoding="utf-8"))["decision"]
    comparison_rows = list(csv.DictReader(COMPARISON.open(encoding="utf-8")))
    conclusion = comparison_rows[0]["conclusion"] if comparison_rows else "MISSING"
    exceeded = [row["axis"] for row in comparison_rows if row.get("exceeded") == "1"]
    families = sorted({row["family"] for row in rows})
    clusters = sorted({row["proposal_id"] for row in rows})
    paths = ["Direct", "Admission", "Claim", "Outbox"]
    by_path = {path: sum(1 for row in rows if row["path_id"] == path) for path in paths}
    unknown = sum(1 for row in rows if "UNKNOWN" in str(row["arm_outcome"]) or row["semantic_validity"] == "UNKNOWN")
    errors = sum(1 for row in rows if row["error"])
    if decision == "INDEPENDENT":
        independence_sentence = (
            "A3 对新代码的判定是 INDEPENDENT。这次回放仍然不建立 Claim 优于 Outbox 的结论；"
            "独立性只说明新的提交、持久化、状态转移、恢复和确认不是同一个函数。"
        )
    else:
        independence_sentence = (
            f"A3 对新代码的判定是 {decision}。这次回放不能建立独立机制优越性。"
        )
    lines = [
        "# 第二生成器回放覆盖",
        "",
        independence_sentence,
        "",
        f"生成器比较结论是 {conclusion}。越过预先冻结阈值的轴：{', '.join(exceeded) if exceeded else '无'}。",
        "第二生成器是确定性的 `structured_factor_generator_v2`，不是语言模型。",
        "可选模型生成：NOT_AVAILABLE。已有配置指向 `https://api.deepseek.com/v1`，那是远程付费接口。本次没有调用，也没有编造补全。",
        "",
        f"冻结清单里的提案-场景簇：{manifest['cluster_count']}。实际写出的簇：{len(clusters)}。单元格：{len(rows)}。",
        f"路径计数：{by_path}。",
        f"家族：{', '.join(families)}。",
        f"错误行：{errors}。出现 UNKNOWN 的行：{unknown}。这些行都留在 `replay_joint.csv`。",
        "",
        "每条路径使用提案自己的幂等键。单元格之间由实验控制器清空目标账本，避免上一条路径的效果变成下一条路径的重放。",
        "恢复家族在 Claim 和 Outbox 上从各自的 SQLite 行继续；Direct 和 Admission 没有可恢复的行，结果是 `NO_DURABLE_STATE`。这是机制适用性差异，不是 Claim 优于 Outbox。",
        "`RESTORED_BYTES` 只表示从本机制的持久行读回了字节。`confirmed_effect` 才表示审计进程在目标账本里看见了合法效果。",
        "`process_kill=false`，`power_loss_claim=false`。这里没有 `TerminateProcess`，也没有把异常当成崩溃。",
        "",
        "Week 4 的 w4l01–w4l18 只把散列抄进 `mechanism_validation/proposals/registry.jsonl`。原始字节没有改，也没有拿来填这次回放。Week 1 到 Week 4 的格子没有写入本文件。",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def _payload_path(path: str) -> Path:
    legacy_prefix = "week5/"
    if path.startswith(legacy_prefix):
        return ROOT / "mechanism_validation" / path.removeprefix(legacy_prefix)
    return ROOT / path


def main() -> None:
    before = MANIFEST.read_bytes()
    manifest = _load_manifest()
    code_hash = runtime_code_hash()
    oracle_version = json.dumps(manifest["oracle_versions"], sort_keys=True, separators=(",", ":"))
    receiver = ReceiverProcess(Path(__file__).resolve().parent / "replay_receiver")
    receiver.start()
    rows: list[dict[str, Any]] = []
    try:
        client = receiver.path_client()
        control = receiver.control_client()
        for proposal in manifest["clusters"]:
            payload_path = _payload_path(proposal["payload_path"])
            payload = payload_path.read_bytes()
            payload_hash = sha256_bytes(payload)
            for index, path_name in enumerate(proposal["path_order"]):
                run_id = f"w5rep-{proposal['proposal_id']}-{path_name}"
                base = {
                    "run_id": run_id,
                    "proposal_id": proposal["proposal_id"],
                    "scenario_id": proposal["scenario_id"],
                    "generator_id": proposal["generator_id"],
                    "path_id": path_name,
                    "fault_id": _fault_id(proposal),
                    "attempt_id": "r01",
                    "code_hash": code_hash,
                    "proposal_hash": proposal["payload_sha256"],
                    "world_hash": proposal["world_before_hash"],
                    "authority_hash": proposal["authority_initial_hash"],
                    "oracle_version": oracle_version,
                    "receiver_version": manifest["receiver_version"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "process_kill": "false",
                    "power_loss_claim": "false",
                    "family": proposal["family"],
                    "seed": proposal["seed"],
                    "path_order_index": index,
                }
                if payload_hash != proposal["payload_sha256"]:
                    rows.append({**base, "arm_outcome": "INVALID", "error": "proposal_hash_mismatch"})
                    continue
                control.reset_effects()
                control.set_world_version(int(proposal["world_version"]))
                auth = proposal["authority_initial"]
                control.set_authority(token=int(auth["token"]), epoch=int(auth["epoch"]), eligible=bool(auth["eligible"]))
                try:
                    result = execute_path(
                        path_name=path_name,
                        proposal=proposal,
                        payload=payload,
                        client=client,
                        work_dir=Path(__file__).resolve().parent / "replay_work" / run_id,
                        receiver_db=receiver.db_path,
                        attempt_id=run_id,
                    )
                except Exception as exc:
                    rows.append({**base, "arm_outcome": "ERROR", "error": f"{type(exc).__name__}: {exc}"})
                    continue
                scores = result["scores"]
                rows.append(
                    {
                        **base,
                        "arm_outcome": result["outcome"],
                        "published_new": int(bool(result["published_new"])),
                        "withheld": int(bool(result["withheld"])),
                        "recovery": result["recovery"],
                        "confirmed_effect": int(bool(result["confirmed_effect"])),
                        "effect_count": result["effect_count"],
                        "duplicate_effect": int(bool(result["duplicate_effect"])),
                        "conflict_observed": int(bool(result["conflict_observed"])),
                        "semantic_validity": scores["semantic_validity"],
                        "physical_effect_state": scores["physical_effect_state"],
                        "authority_decision": scores["authority_decision"],
                        "successor_status": scores["successor_status"],
                        "stop_correctness": scores["stop_correctness"],
                        "error": "",
                    }
                )
    finally:
        receiver.stop()
    if MANIFEST.read_bytes() != before:
        raise RuntimeError("replay rewrote the frozen manifest")
    with JOINT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in FIELDS})
    _write_report(rows, manifest)
    print(f"cells={len(rows)} clusters={len({row['proposal_id'] for row in rows})}")


if __name__ == "__main__":
    main()
