"""v2 统计。不改历史 m6 summary，也不把零宽 bootstrap 当成总体精度。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scipy.stats import beta, binomtest

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / "results" / "runtime" / "correctness" / "m6" / "summary.json"
CONFIRM = ROOT / "results" / "runtime" / "correctness" / "m4-confirm" / "summary.json"
OUT = ROOT / "results" / "runtime" / "correctness" / "v2-m6" / "summary.json"
DOC = ROOT / "docs" / "实验" / "M4-M6-v2结果与验收.md"


def clopper(successes: int, n: int) -> dict:
    if n <= 0:
        return {"n": n, "successes": successes, "low": None, "high": None, "half_width": None}
    low = 0.0 if successes == 0 else float(beta.ppf(0.025, successes, n - successes + 1))
    high = 1.0 if successes == n else float(beta.ppf(0.975, successes + 1, n - successes))
    return {"n": n, "successes": successes, "low": low, "high": high, "half_width": (high - low) / 2}


def pairs(rows: list[dict], fault: str, left: str, right: str, field: str) -> list[dict]:
    index = {(row.get("task_id"), row.get("fault"), row.get("arm")): row for row in rows if not row.get("blocked")}
    found = []
    tasks = sorted({row.get("task_id") for row in rows if row.get("fault") == fault and not row.get("blocked")})
    for task_id in tasks:
        a = index.get((task_id, fault, left))
        b = index.get((task_id, fault, right))
        if a is None or b is None:
            continue
        found.append({"task_id": task_id, "left": bool(a.get(field)), "right": bool(b.get(field)), "diff": float(bool(a.get(field))) - float(bool(b.get(field)))})
    return found


def mcnemar(found: list[dict]) -> dict:
    b = sum(item["left"] and not item["right"] for item in found)
    c = sum(item["right"] and not item["left"] for item in found)
    if b + c == 0:
        return {"b": b, "c": c, "pvalue": None, "note": "没有不一致配对，精确检验不构成差异证据"}
    return {"b": b, "c": c, "pvalue": float(binomtest(b, b + c, 0.5).pvalue), "note": "双侧精确 McNemar"}


def positive_rate_interval(found: list[dict]) -> dict:
    positives = sum(item["diff"] == 1 for item in found)
    interval = clopper(positives, len(found))
    interval["degenerate_bootstrap"] = bool(found) and len({item["diff"] for item in found}) == 1
    return interval


def historical() -> dict:
    payload = json.loads(CONFIRM.read_text(encoding="utf-8"))
    rows = payload["rows"]
    assigned = [row for row in rows if row.get("fault") == "S1"]
    h5 = pairs(rows, "S1", "B1", "B2", "stale_credential_accepted")
    h6 = pairs(rows, "S3", "B3", "B2", "interrupted_recovered")
    old = json.loads(HISTORICAL.read_text(encoding="utf-8"))
    return {
        "historical_decision_unchanged": old.get("decision"),
        "assigned_s1": len(assigned),
        "assigned_s1_blocked": sum(1 for row in assigned if row.get("blocked")),
        "conditional_h5_pairs": h5,
        "h5_exact": positive_rate_interval(h5),
        "h5_mcnemar": mcnemar(h5),
        "h5_b1": clopper(sum(item["left"] for item in h5), len(h5)),
        "h5_b2": clopper(sum(item["right"] for item in h5), len(h5)),
        "h5_b2_zero_upper": None if not h5 or any(item["right"] for item in h5) else 1 - (0.05 ** (1 / len(h5))),
        "conditional_h6_pairs": h6,
        "h6_exact": positive_rate_interval(h6),
        "h6_mcnemar": mcnemar(h6),
        "zero_width_bootstrap_is_not_cross_task_precision": True,
    }


def coding_block(path: Path) -> dict:
    if not path.is_file():
        return {"present": False}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    labels = Counter(row.get("blocked_label") or row.get("error_type") for row in rows if row.get("blocked"))
    live = [row for row in rows if not row.get("blocked")]
    return {
        "present": True,
        "status": payload.get("status"),
        "assigned": len(rows),
        "blocked": sum(1 for row in rows if row.get("blocked")),
        "labels": dict(labels),
        "container_passed": sum(1 for row in live if row.get("container_tests_passed")),
        "stale_published": sum(1 for row in live if row.get("stale_patch_published")),
        "h5": pairs(rows, "S1", "B1", "B2", "stale_patch_published"),
        "h6": pairs(rows, "S3", "B3", "B2", "interrupted_recovered"),
    }


def main() -> None:
    report = {
        "historical": historical(),
        "coding_pilot": coding_block(ROOT / "results" / "runtime" / "correctness" / "v2-coding-pilot" / "summary.json"),
        "coding_confirm": coding_block(ROOT / "results" / "runtime" / "correctness" / "v2-coding-confirm" / "summary.json"),
        "precision_rule": "确认配对少于 8，或正差比例的 Clopper-Pearson 半宽不小于 0.25，则 PRECISION_NOT_MET，不加样本。",
    }
    confirm_pairs = report["coding_confirm"].get("h5") or []
    interval = positive_rate_interval(confirm_pairs) if confirm_pairs else {"half_width": None, "n": 0}
    report["coding_confirm_precision"] = interval
    met = interval.get("half_width") is not None and interval["half_width"] < 0.25 and interval.get("n", 0) >= 8
    report["precision_status"] = "MET" if met else "PRECISION_NOT_MET"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"precision_status": report["precision_status"], "historical_decision": report["historical"]["historical_decision_unchanged"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
