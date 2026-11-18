"""从原始记录生成主表。H5 用配对 bootstrap，不用 Wilson 重叠。"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any


def _rows(path: Path) -> list[dict[str, Any]]:
    summary = path / "summary.json"
    if not summary.is_file():
        return []
    payload = json.loads(summary.read_text(encoding="utf-8"))
    return [row for row in payload.get("rows", []) if not row.get("blocked")]


def _pair(rows: list[dict[str, Any]], fault: str, left: str, right: str, field: str) -> list[float]:
    index = {(row["task_id"], row["fault"], row["arm"]): row for row in rows}
    diffs = []
    tasks = sorted({row["task_id"] for row in rows if row["fault"] == fault})
    for task_id in tasks:
        a = index.get((task_id, fault, left))
        b = index.get((task_id, fault, right))
        if a is None or b is None:
            continue
        diffs.append(float(bool(a.get(field))) - float(bool(b.get(field))))
    return diffs


def bootstrap_mean(diffs: list[float], *, seed: int = 20261119, draws: int = 2000) -> dict[str, Any]:
    if not diffs:
        return {"n": 0, "mean": None, "low": None, "high": None, "half_width": None}
    rng = random.Random(seed)
    means = []
    size = len(diffs)
    for _ in range(draws):
        sample = [diffs[rng.randrange(size)] for _ in range(size)]
        means.append(sum(sample) / size)
    means.sort()
    low = means[int(0.025 * (draws - 1))]
    high = means[int(0.975 * (draws - 1))]
    mean = sum(diffs) / size
    return {"n": size, "mean": mean, "low": low, "high": high, "half_width": (high - low) / 2}


def binomial_upper(failures: int, n: int) -> float | None:
    if n <= 0:
        return None
    # Clopper-Pearson 上界的保守近似：1 - 0.05^(1/n) 当失败为 0。
    if failures == 0:
        return 1 - (0.05 ** (1 / n))
    return None


def h8(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """规则在协议里冻结：D0/D1 弃权；D1+ 只看通知和终态；D2 才看 Claim。"""
    labeled = [row for row in rows if row.get("fault") == "S1"]
    def predict(level: str, row: dict[str, Any]) -> str:
        if level in {"D0", "D1"}:
            return "abstain"
        if level == "D1+":
            if row.get("stale_credential_accepted"):
                return "stale"
            if row.get("successor_completed"):
                return "successor"
            return "abstain"
        if row.get("claim") not in (None, "NOT_SUPPORTED") and row.get("oracle", {}).get("interrupted_recovered"):
            return "recovered"
        return predict("D1+", row)
    report = {}
    for level in ("D0", "D1", "D1+", "D2"):
        preds = [predict(level, row) for row in labeled]
        abstain = sum(pred == "abstain" for pred in preds)
        false_confirm = sum(pred == "successor" and not row.get("successor_completed") for pred, row in zip(preds, labeled))
        report[level] = {
            "n": len(labeled),
            "abstention_rate": abstain / len(labeled) if labeled else None,
            "false_confirmation_rate": false_confirm / len(labeled) if labeled else None,
            "coverage": 1 - (abstain / len(labeled)) if labeled else None,
        }
    return report


def score(pilot: Path, confirm: Path, m5: Path, out: Path) -> dict[str, Any]:
    confirm_rows = _rows(confirm)
    h5 = bootstrap_mean(_pair(confirm_rows, "S1", "B1", "B2", "stale_credential_accepted"))
    h6 = bootstrap_mean(_pair(confirm_rows, "S3", "B3", "B2", "interrupted_recovered"))
    families: dict[str, list[float]] = {}
    for row in confirm_rows:
        if row.get("fault") != "S1":
            continue
        families.setdefault(row["family"], [])
    joint = []
    seen = set()
    for row in confirm_rows:
        ident = (row["task_id"], row["fault"])
        if ident in seen or row.get("fault") != "S1":
            continue
        seen.add(ident)
        arms = [item for item in confirm_rows if item["task_id"] == row["task_id"] and item["fault"] == "S1"]
        terminals = {item["arm"]: item.get("task_terminal_match") for item in arms}
        safeties = {item["arm"]: item.get("stale_credential_accepted") for item in arms}
        same_terminal = len(set(terminals.values())) == 1
        different_safety = len(set(safeties.values())) > 1
        joint.append({"task_id": row["task_id"], "same_terminal": same_terminal, "different_safety": different_safety})
    h4 = sum(1 for item in joint if item["same_terminal"] and item["different_safety"]) / len(joint) if joint else None
    m5_payload = json.loads((m5 / "summary.json").read_text(encoding="utf-8")) if (m5 / "summary.json").is_file() else {"rows": []}
    m5_rows = m5_payload.get("rows", [])
    observable = sum(1 for row in m5_rows if row.get("observable") and not row.get("blocked"))
    h7 = observable / len(m5_rows) if m5_rows else None
    fresh = [row for row in confirm_rows if row.get("fault") == "S1" and row.get("arm") == "B2"]
    failures = sum(1 for row in fresh if row.get("stale_credential_accepted"))
    upper = binomial_upper(failures, len(fresh))
    half = h5.get("half_width")
    precision = bool(half is not None and (half <= 0.20 or (h5["mean"] is not None and h5["mean"] >= 0.15 and h5["low"] is not None and h5["low"] > 0)))
    migration = bool(m5_rows) and not m5_payload.get("kernel_modified", True) and any(not row.get("blocked") for row in m5_rows)
    decision = "GO" if precision and migration and confirm_rows else "NO-GO"
    reason = []
    if not precision:
        reason.append("H5 区间没有达到协议里的精度目标，预算不允许扩到 288")
    if not migration:
        reason.append("M5 没有在不改内核的情况下给出可核验结果")
    if not confirm_rows:
        reason.append("确认批没有可用记录")
    if decision == "GO":
        reason.append("H5 达到精度目标，且 M5 没有修改 LangGraph 内核")
    report = {
        "role": "confirm_rows_are_the_only_formal_candidates",
        "pilot_n": len(_rows(pilot)),
        "confirm_n": len(confirm_rows),
        "h4_same_terminal_different_safety": h4,
        "h5": h5,
        "h6": h6,
        "h7_observable_coverage": h7,
        "h8": h8(confirm_rows),
        "b2_s1_stale_upper": upper,
        "zero_failures_are_not_perfect_reliability": True,
        "decision": decision,
        "reasons": reason,
        "manuscript": "本轮不写新稿" if decision == "NO-GO" else "精度和迁移条件满足，仍不在本轮改冻结稿",
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", type=Path, default=Path("results/runtime/correctness/m4-pilot"))
    parser.add_argument("--confirm", type=Path, default=Path("results/runtime/correctness/m4-confirm"))
    parser.add_argument("--m5", type=Path, default=Path("results/runtime/correctness/m5"))
    parser.add_argument("--out", type=Path, default=Path("results/runtime/correctness/m6"))
    args = parser.parse_args()
    report = score(args.pilot, args.confirm, args.m5, args.out)
    print(json.dumps({"decision": report["decision"], "h5": report["h5"], "h4": report["h4_same_terminal_different_safety"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
