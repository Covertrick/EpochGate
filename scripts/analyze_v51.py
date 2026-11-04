"""分析 v5.1 复现批次。不读取 7000–7049 来改写这批估计。"""

from __future__ import annotations

import json
from pathlib import Path

from epochgate.v51_analysis import (
    BOOT_SEED_REPLICATION,
    analysis_source_sha256,
    fit_m0,
    holdout_residuals,
    model_identifiability,
    pair_traces,
    summarize_load,
)
from epochgate.v51_audit import load_jsonl

PROTOCOL = Path("results/experiments/v5_1/protocol/v51_protocol.json")
TRACES = Path("results/experiments/v5_1/ds/replicate/traces.jsonl")
OUT = Path("results/experiments/v5_1/reports/v51_replicate.json")
MD = Path("docs/测试记录/v5.1-复现.md")


def _fmt(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def main() -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    if analysis_source_sha256() != protocol["analysis_sha256"]:
        raise SystemExit("analysis source changed after freeze")
    rows = load_jsonl(TRACES)
    if any(7000 <= int(row["seed"]) <= 7049 or 6000 <= int(row["seed"]) <= 6014 for row in rows):
        raise SystemExit("replication file contains a sealed seed")
    pairs = pair_traces(rows)
    loads = {
        load: summarize_load(pairs, load, seed=BOOT_SEED_REPLICATION) for load in ("medium", "high")
    }
    m0 = fit_m0(pairs, train_load="medium")
    holdout = holdout_residuals(m0, pairs, holdout_load="high", seed=BOOT_SEED_REPLICATION)
    report = {
        "protocol_id": protocol["protocol_id"],
        "n_traces": len(rows),
        "loads": loads,
        "M0": m0,
        "M0_holdout_high": holdout,
        "models_not_fit": model_identifiability(),
        "mixed_with_7000_7049": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# v5.1 独立复现",
        "",
        "本批不与 7000–7049 混合。Bootstrap 种子 20261105，单位仍是 trace 配对。",
        "",
        "| 负载 | n | ΔW 均值 [2.5%, 97.5%] | 相对 | 槽门槛 | Δe2e 均值 [2.5%, 97.5%] | 相对 | 端到端门槛 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for load in ("medium", "high"):
        block = loads[load]
        if block.get("n", 0) == 0:
            lines.append(f"| {load} | 0 | — | — | — | — | — | — |")
            continue
        w, e = block["dW_slot"], block["dT_e2e"]
        gw, ge = block["gate_W"], block["gate_E"]
        lines.append(
            "| {load} | {n} | {m} [{a}, {b}] | {rw}% | {pw} | {em} [{ea}, {eb}] | {re}% | {pe} |".format(
                load=load,
                n=block["n"],
                m=_fmt(w["mean"]),
                a=_fmt(w["p2_5"]),
                b=_fmt(w["p97_5"]),
                rw=_fmt(None if gw["relative"] is None else 100 * gw["relative"], 1),
                pw="过" if gw["passed"] else "未过",
                em=_fmt(e["mean"]),
                ea=_fmt(e["p2_5"]),
                eb=_fmt(e["p97_5"]),
                re=_fmt(None if ge["relative"] is None else 100 * ge["relative"], 1),
                pe="过" if ge["passed"] else "未过",
            )
        )
    failed = sum(1 for row in rows if row.get("failed"))
    lines.extend(
        [
            "",
            f"写入 {len(rows)} 条 trace，其中失败 {failed} 条。失败的配对整对丢弃，不追加种子。",
            "高负载槽等待和端到端的区间都含 0，没有复现 7000–7049 的正差。中负载两道工程门槛都过，但区间半宽大于开跑前写下的 0.25 秒。",
            "M0 只在本批中负载上拟合，再预测本批高负载。留出残差区间不含 0 且绝对值至少 0.20 秒时，只表示这条直线没有预测高负载，不表示出现了新机制。",
            f"M0 alpha={_fmt(m0.get('alpha'))} beta={_fmt(m0.get('beta'))} train_n={m0.get('n')}。",
            f"高负载留出均值残差={_fmt(holdout.get('mean_residual'))}，区间 [{_fmt(holdout.get('p2_5'))}, {_fmt(holdout.get('p97_5'))}]。",
            "M1 与 M2 未拟合：这批没有可观测的后端残余，不用 0 填充。",
            "",
        ]
    )
    MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"n": {k: v.get("n") for k, v in loads.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
