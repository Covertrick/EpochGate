"""只读审计 7000–7049。不写封存 JSONL。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from epochgate.v50_metrics import linear_percentile
from epochgate.v51_analysis import (
    BOOT_N,
    BOOT_SEED_CONFIRM,
    analysis_source_sha256,
    model_identifiability,
    pair_traces,
    replication_seeds,
    sample_size_plan,
    sha256_file,
    summarize_load,
)

SEALED = Path("results/experiments/v5_0/ds/confirm/traces.jsonl")
PROTOCOL_PATH = Path("results/experiments/v5_0/protocol/v50_protocol.json")
PUBLISHED_PATH = Path("results/experiments/v5_0/reports/v50_ds_confirm.json")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _recompute_p95(trace: dict[str, Any]) -> tuple[float | None, float | None, int]:
    bys = [row for row in trace.get("rows") or [] if row.get("workflow_role") == "bystander"]
    weights = [float(row["W_slot_s"]) for row in bys if row.get("W_slot_s") is not None]
    ends = [float(row["T_e2e_s"]) for row in bys if row.get("T_e2e_s") is not None]
    p95_w = linear_percentile(weights, 0.95) if len(bys) >= 2 and len(weights) >= 2 else None
    p95_e = linear_percentile(ends, 0.95) if len(bys) >= 2 and len(ends) >= 2 else None
    return p95_w, p95_e, len(bys)


def _close(left: float | None, right: float | None, tol: float = 1e-6) -> bool:
    if left is None or right is None:
        return left is right
    return abs(left - right) <= tol


def audit_records(
    rows: list[dict[str, Any]],
    *,
    protocol: dict[str, Any],
    published: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回硬门槛与描述性发现。硬门槛失败则不得开复现。"""
    expected = list(range(7000, 7050))
    loads = ("medium", "high")
    arms = ("G0", "G2-local")
    checks: list[dict[str, Any]] = []

    by_key: dict[tuple[int, str, str], dict[str, Any]] = {}
    dup = 0
    for row in rows:
        key = (int(row["seed"]), str(row["load"]), str(row["arm"]))
        if key in by_key:
            dup += 1
        by_key[key] = row
    missing = [
        {"seed": seed, "load": load, "arm": arm}
        for seed in expected
        for load in loads
        for arm in arms
        if (seed, load, arm) not in by_key
    ]
    extra = [
        {"seed": seed, "load": load, "arm": arm}
        for (seed, load, arm) in by_key
        if seed not in expected or load not in loads or arm not in arms
    ]
    checks.append(_check("no_duplicate_keys", dup == 0, f"duplicate keys={dup}"))
    checks.append(_check("complete_7000_7049", not missing and not extra, f"missing={len(missing)} extra={len(extra)}"))

    bad_roles = 0
    bad_ids = 0
    count_hist: Counter[int] = Counter()
    for trace in rows:
        roles = [r.get("workflow_role") for r in trace.get("rows") or []]
        ids = [r.get("task_id") for r in trace.get("rows") or [] if r.get("workflow_role") == "bystander"]
        count_hist[roles.count("bystander")] += 1
        if roles.count("target") != 1 or roles.count("successor") != 1 or roles.count("bystander") != 8:
            bad_roles += 1
        if len(ids) != len(set(ids)) or any(not str(i).startswith("bystander") for i in ids):
            bad_ids += 1
        if "target" in ids:
            bad_ids += 1
    checks.append(
        _check(
            "bystander_workflows",
            bad_roles == 0 and bad_ids == 0,
            f"bad_role_traces={bad_roles} bad_id_traces={bad_ids} count_hist={dict(count_hist)}",
        )
    )

    p95_mismatch = 0
    for trace in rows:
        got_w, got_e, n_by = _recompute_p95(trace)
        summary = trace.get("summary") or {}
        if n_by != summary.get("bystander_count"):
            p95_mismatch += 1
        if not _close(got_w, summary.get("W_slot_p95_s")) or not _close(got_e, summary.get("T_e2e_p95_s")):
            p95_mismatch += 1
    checks.append(
        _check(
            "p95_recompute",
            p95_mismatch == 0,
            f"mismatched_traces={p95_mismatch}; linear interpolation at rank (n-1)*0.95",
        )
    )

    unknown = 0
    residual_filled = 0
    model_bad = 0
    thinking_bad = 0
    row_n = 0
    for trace in rows:
        if trace.get("model") != "deepseek-flash":
            model_bad += 1
        if trace.get("thinking") != "disabled":
            thinking_bad += 1
        for row in trace.get("rows") or []:
            row_n += 1
            if row.get("backend_stop_status") != "unknown":
                unknown += 1
            if row.get("residual_lower_s") is not None:
                residual_filled += 1
            if row.get("model") not in {None, "deepseek-flash"}:
                model_bad += 1
    checks.append(_check("backend_unknown", unknown == 0 and residual_filled == 0, f"non_unknown={unknown} residual_filled={residual_filled}"))
    checks.append(_check("model_and_thinking", model_bad == 0 and thinking_bad == 0, f"model_bad={model_bad} thinking_bad={thinking_bad}"))

    g0_closed = 0
    g2_open = 0
    g2_order_bad = 0
    g0_stop_marked = 0
    focal_n = {"G0": 0, "G2-local": 0}
    for trace in rows:
        focal = trace.get("focal") or {}
        if not focal:
            continue
        arm = trace.get("arm")
        focal_n[arm] = focal_n.get(arm, 0) + 1
        if arm == "G0" and focal.get("closed_early"):
            g0_closed += 1
        if arm == "G0" and focal.get("local_read_stopped_mono") is not None:
            g0_stop_marked += 1
        if arm == "G2-local" and not focal.get("closed_early"):
            g2_open += 1
        if arm == "G2-local":
            revoke = focal.get("eligibility_revoked_mono")
            release = focal.get("slot_released_mono")
            stopped = focal.get("local_read_stopped_mono")
            if revoke is None or release is None or stopped is None or float(release) + 1e-6 < float(revoke):
                g2_order_bad += 1
    checks.append(
        _check(
            "event_order",
            g0_closed == 0 and g2_open == 0 and g2_order_bad == 0 and g0_stop_marked == 0,
            f"G0_closed_early={g0_closed} G2_not_closed={g2_open} G2_order_bad={g2_order_bad} G0_stop_marked={g0_stop_marked}",
        )
    )

    orders = Counter(str(trace.get("arm_order")) for trace in rows)
    g0_first = 0
    g2_first = 0
    for (_seed, load), arms in _group(rows).items():
        if load not in loads or "G0" not in arms:
            continue
        order = str(arms["G0"].get("arm_order") or "")
        if order.startswith("G0"):
            g0_first += 1
        elif order.startswith("G2-local"):
            g2_first += 1
    balanced = g0_first > 0 and g2_first > 0 and min(g0_first, g2_first) / max(g0_first, g2_first) >= 0.6
    checks.append(_check("arm_order_balanced", balanced, f"G0_first_pairs={g0_first} G2_first_pairs={g2_first} orders={dict(orders)}"))

    token_rows = 0
    token_with_output = 0
    for trace in rows:
        for row in trace.get("rows") or []:
            token_rows += 1
            if row.get("output_tokens") is not None:
                token_with_output += 1
    failures = sum(1 for trace in rows if trace.get("failed"))
    error_kinds = Counter((trace.get("error") or "")[:80] for trace in rows if trace.get("failed"))
    rate_limited = sum(1 for trace in rows if "429" in str(trace.get("error")))
    checks.append(
        _check(
            "failures_recorded",
            True,
            f"failed_traces={failures}/{len(rows)} rate_limit_markers={rate_limited} errors={dict(error_kinds)}",
        )
    )

    config_in_row = any("max_tokens" in trace or "temperature" in trace for trace in rows)
    ds = protocol.get("ds") or {}
    prompt_code = "about {words}"
    prompt_protocol = str(ds.get("prompt_template") or "")
    checks.append(
        _check(
            "config_frozen_in_records",
            False if not config_in_row else True,
            (
                "model and thinking are on every trace; max_tokens and temperature are not. "
                f"Protocol says max_tokens={ds.get('max_tokens')} temperature={ds.get('temperature')}. "
                f"Runner prompt uses {prompt_code!r}; protocol template is {prompt_protocol!r}. "
                "No wall-clock timestamp, so time-of-day confounding cannot be tested from the JSONL. "
                "The runner does not record retries."
            ),
        )
    )
    # This gap is real. Replication must persist the missing fields. It does not by itself
    # invalidate the sealed pairs, so it is not a hard gate. Mark passed False as a finding
    # and keep it out of the hard set below.

    pairs = pair_traces(rows)
    usable_n = {
        load: sum(1 for p in pairs if p.get("usable") and p["load"] == load) for load in loads
    }
    checks.append(
        _check(
            "fifty_independent_pairs_per_load",
            usable_n == {"medium": 50, "high": 50},
            (
                f"usable={usable_n}. Same seed integer is reused across loads and must not be pooled "
                "as 100 iid pairs. Bootstrap resamples these pairs, not the 8 bystanders."
            ),
        )
    )

    recomputed = {
        load: summarize_load(pairs, load, seed=BOOT_SEED_CONFIRM) for load in loads
    }
    published_ok = True
    published_detail = "no published report"
    if published:
        mismatches = []
        for load in loads:
            block = published.get(load) or {}
            got = recomputed[load]
            for label, pub_key, got_mean, got_lo, got_hi, rel in (
                (
                    "dW",
                    "dW_slot",
                    got["dW_slot"]["mean"],
                    got["dW_slot"]["p2_5"],
                    got["dW_slot"]["p97_5"],
                    got["gate_W"]["relative"],
                ),
                (
                    "dE",
                    "dT_e2e",
                    got["dT_e2e"]["mean"],
                    got["dT_e2e"]["p2_5"],
                    got["dT_e2e"]["p97_5"],
                    got["gate_E"]["relative"],
                ),
            ):
                if abs(got_mean - float(block[f"{pub_key}_mean_s"])) > 0.0015:
                    mismatches.append(f"{load} {label} mean")
                if abs(got_lo - float(block[f"{pub_key}_p2_5"])) > 0.02:
                    mismatches.append(f"{load} {label} p2.5")
                if abs(got_hi - float(block[f"{pub_key}_p97_5"])) > 0.02:
                    mismatches.append(f"{load} {label} p97.5")
                if abs(float(rel) - float(block[f"{pub_key}_relative"])) > 0.015:
                    mismatches.append(f"{load} {label} relative")
            if bool(got["gate_W"]["passed"]) is not True:
                mismatches.append(f"{load} slot gate")
            high_e2e_should_fail = load == "high"
            if high_e2e_should_fail and got["gate_E"]["passed"]:
                mismatches.append("high e2e unexpectedly passed the 10% gate")
            if load == "medium" and not got["gate_E"]["passed"]:
                mismatches.append("medium e2e gate")
        published_ok = not mismatches
        published_detail = "match" if published_ok else ", ".join(mismatches)
    checks.append(_check("published_numbers", published_ok, published_detail))

    hard_names = {
        "no_duplicate_keys",
        "complete_7000_7049",
        "bystander_workflows",
        "p95_recompute",
        "backend_unknown",
        "model_and_thinking",
        "event_order",
        "arm_order_balanced",
        "fifty_independent_pairs_per_load",
        "published_numbers",
    }
    hard = [item for item in checks if item["name"] in hard_names]
    plan = sample_size_plan(pairs) if all(item["passed"] for item in hard) else None
    seeds = replication_seeds(int(plan["n"])) if plan else []
    return {
        "n_traces": len(rows),
        "checks": checks,
        "hard_passed": all(item["passed"] for item in hard),
        "failures": failures,
        "rate_limited": rate_limited,
        "arm_order": {"G0_first": g0_first, "G2_first": g2_first},
        "token_rows": token_rows,
        "token_with_output": token_with_output,
        "bystander_count_hist": dict(count_hist),
        "loads": recomputed,
        "sample_size_plan": plan,
        "replication_seeds": seeds,
        "models": model_identifiability(),
        "bootstrap": {"n": BOOT_N, "seed": BOOT_SEED_CONFIRM, "unit": "trace pair"},
        "analysis_sha256": analysis_source_sha256(),
    }


def _group(rows: list[dict[str, Any]]) -> dict[tuple[int, str], dict[str, dict[str, Any]]]:
    grouped: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((int(row["seed"]), str(row["load"])), {})[str(row["arm"])] = row
    return grouped


def render_markdown(report: dict[str, Any], *, sealed_sha256: str) -> str:
    def fmt(value: float | None, digits: int = 3) -> str:
        if value is None:
            return "—"
        return f"{value:.{digits}f}"

    lines = [
        "# v5.1 DS 审计",
        "",
        "日期：2026-11-04。只读 `results/experiments/v5_0/ds/confirm/traces.jsonl`。本文件不改那份 JSONL。",
        "",
        f"封存文件 sha256：`{sealed_sha256}`。",
        f"分析实现 sha256：`{report['analysis_sha256']}`。",
        f"硬门槛：{'通过' if report['hard_passed'] else '未通过'}。未通过时不得开复现。",
        "",
        "## 硬门槛",
        "",
        "| 检查 | 结果 | 说明 |",
        "| --- | --- | --- |",
    ]
    for item in report["checks"]:
        if item["name"] == "config_frozen_in_records":
            continue
        flag = "通过" if item["passed"] else "未通过"
        lines.append(f"| {item['name']} | {flag} | {item['detail']} |")

    config = next(item for item in report["checks"] if item["name"] == "config_frozen_in_records")
    lines.extend(
        [
            "",
            "## 记录里没有写死的配置",
            "",
            config["detail"],
            "",
            "失败 trace "
            f"{report['failures']} 条，限流标记 {report['rate_limited']} 条。"
            f"输出 token 有值的行 {report['token_with_output']}/{report['token_rows']}。"
            f"臂序：G0 先跑 {report['arm_order']['G0_first']} 对，G2-local 先跑 {report['arm_order']['G2_first']} 对。"
            f"每条 trace 的 bystander 数分布：{report['bystander_count_hist']}。",
            "Bystander 是各自的 task，但共用 2 个本地槽，所以不是 8 个独立样本。Bootstrap 只重抽 (seed, load) 配对。",
            "",
            "## 按封存 JSONL 重算",
            "",
            "Bootstrap 10000 次，种子 20261104，单位是配对。`scripts/analyze_v50.py` 把次数截到 2000，本表不沿用那个截断。",
            "相对值 = 配对差均值 / 该负载 G0 的 P95 均值。正数表示 G2-local 更短。",
            "",
            "| 负载 | n | ΔW 均值 [2.5%, 97.5%] | 相对 | 槽门槛 | Δe2e 均值 [2.5%, 97.5%] | 相对 | 端到端门槛 | 同向 / 分离 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for load in ("medium", "high"):
        block = report["loads"][load]
        w, e = block["dW_slot"], block["dT_e2e"]
        gw, ge = block["gate_W"], block["gate_E"]
        lines.append(
            "| {load} | {n} | {m} [{a}, {b}] | {rw} | {pw} | {em} [{ea}, {eb}] | {re} | {pe} | {same} / {sep} |".format(
                load=load,
                n=block["n"],
                m=fmt(w["mean"]),
                a=fmt(w["p2_5"]),
                b=fmt(w["p97_5"]),
                rw=fmt(None if gw["relative"] is None else 100 * gw["relative"], 1) + "%",
                pw="过" if gw["passed"] else "未过",
                em=fmt(e["mean"]),
                ea=fmt(e["p2_5"]),
                eb=fmt(e["p97_5"]),
                re=fmt(None if ge["relative"] is None else 100 * ge["relative"], 1) + "%",
                pe="过" if ge["passed"] else "未过",
                same=block["same_direction"],
                sep=block["separated"],
            )
        )
    lines.extend(
        [
            "",
            "高负载端到端相对值没有过 10% 门槛。这里只复述重算结果，不把它写成过门槛。",
            "槽等待与端到端多数同向变短。分离计数不是稳定的 D2 分叉。服务端状态仍全部是 `unknown`。",
            "",
            "## 敏感度",
            "",
            "Pearson r，配对差对配对差。它描述伴随变化，不区分解释 A–D。",
            "",
        ]
    )
    for load in ("medium", "high"):
        sens = report["loads"][load]["sensitivity"]
        lines.append(f"- {load}：Δe2e 与输出 token 差 r={fmt(sens['dT_vs_d_output_tokens']['r'])}；与焦点端到端差 r={fmt(sens['dT_vs_d_focal_e2e']['r'])}；Δ槽等待与 ΔU_client r={fmt(sens['dW_vs_dU_client']['r'])}。")

    plan = report.get("sample_size_plan")
    lines.extend(["", "## 复现样本量", ""])
    if not plan:
        lines.append("硬门槛未通过，不规划复现样本量。")
    else:
        lines.append(plan["rule"])
        lines.append("")
        lines.append(f"冻结 n={plan['n']}。种子 {report['replication_seeds'][0]}–{report['replication_seeds'][-1]}，与 6000–6014、7000–7049 不相交。")
        for load, item in plan["per_load"].items():
            lines.append(
                f"- {load}：规划 SD={fmt(item.get('sd'))} 秒，该负载单独需要 n={item['n_for_half_width']}。"
            )
    lines.extend(
        [
            "",
            "## 预登记模型",
            "",
            "M0 在 DS 上可拟合：ΔU_client 是焦点本地槽占用差，不是 token；ΔT 是 bystander P95 的配对差；α、β 只在中负载估计，再预测高负载。",
            "M1、M2 需要可观测的后端残余。DS 没有这项，不把残余写成 0，也不拟合。",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def build_protocol(report: dict[str, Any], *, sealed_sha256: str) -> dict[str, Any]:
    if not report["hard_passed"]:
        raise RuntimeError("refuse to freeze a replication protocol when the audit hard gate failed")
    plan = report["sample_size_plan"]
    return {
        "protocol_id": "v5.1-replication",
        "frozen_at": "2026-11-04",
        "parent_protocol": "v5.0-direction-a",
        "sealed_confirm_sha256": sealed_sha256,
        "analysis_sha256": report["analysis_sha256"],
        "do_not_write": ["results/experiments/v5_0/ds/confirm/traces.jsonl"],
        "seeds": report["replication_seeds"],
        "excluded_ranges": ["6000-6014", "7000-7049"],
        "loads": ["medium", "high"],
        "arms": ["G0", "G2-local"],
        "model": "deepseek-flash",
        "thinking": "disabled",
        "temperature": 0,
        "max_tokens": 512,
        "slots_S": 2,
        "bystanders_B": 8,
        "t_change_s": 2.0,
        "concurrency": 1,
        "sample_size": plan,
        "bootstrap": {"n": BOOT_N, "seed": 20261105, "unit": "trace pair"},
        "models": report["models"],
        "output": "results/experiments/v5_1/ds/replicate/traces.jsonl",
        "mix_with_7000_7049": False,
    }
