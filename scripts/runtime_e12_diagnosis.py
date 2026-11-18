"""E12：独立 Oracle 与四个诊断器。规则来自补实验协议，不从结果里回改。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

LABELS = (
    "unnotified_stale",
    "stale_admitted",
    "successor_completed",
    "correct_stop",
    "authorized_not_published",
    "payload_mismatch",
)


def oracle_label(record: dict) -> str:
    """只读注入结果、文件字节、接纳与回执原文。不给诊断器提供这个函数的输出。"""
    if record.get("claim_result_digest") and record.get("physical_digest") and record["claim_result_digest"] != record["physical_digest"]:
        return "payload_mismatch"
    if record.get("receipt_digest") and record.get("physical_digest") and record["receipt_digest"] != record["physical_digest"]:
        return "payload_mismatch"
    if record.get("claim_phase") == "started" and not record.get("receipt_digest") and record.get("file_kind") == "absent":
        return "authorized_not_published"
    if record.get("unprovable_receiver") and record.get("receiver_effect") == "happened":
        return "effect_happened_unprovable"
    if record.get("old_accepted") and record.get("successor_completed") and not record.get("stale_credential_accepted"):
        return "both_completed"
    if record.get("stale_credential_accepted"):
        return "stale_admitted"
    if record.get("notice") == "missing" and record.get("old_accepted") and record.get("file_kind") == "old":
        return "unnotified_stale"
    if record.get("successor_completed") and not record.get("old_accepted"):
        return "successor_completed"
    if record.get("study") == "revoke" and not record.get("old_accepted") and record.get("file_kind") == "absent":
        return "correct_stop"
    return "other"


def _view_d0(record: dict) -> dict:
    return {"task_terminal_match": record.get("task_terminal_match")}


def _view_d1(record: dict) -> dict:
    return {
        "task_terminal_match": record.get("task_terminal_match"),
        "file_kind": record.get("file_kind"),
        "tool_trace": record.get("tool_trace"),
    }


def _view_d1_plus(record: dict) -> dict:
    view = _view_d1(record)
    view.update({
        "notice": record.get("notice"),
        "authority_updated": record.get("authority_updated"),
        "old_rejected": record.get("old_rejected", False),
    })
    return view


def _view_d2(record: dict) -> dict:
    view = _view_d1_plus(record)
    view.update({
        "claim_phase": record.get("claim_phase"),
        "receipt_digest": record.get("receipt_digest"),
        "physical_digest": record.get("physical_digest"),
    })
    return view


def diagnose_d0(view: dict) -> str:
    del view
    return "abstain"


def diagnose_d1(view: dict) -> str:
    del view
    return "abstain"


def diagnose_d1_plus(view: dict) -> str:
    if view.get("notice") == "missing" and view.get("file_kind") == "old":
        return "unnotified_stale"
    if view.get("authority_updated") and view.get("file_kind") == "old":
        return "stale_admitted"
    if view.get("authority_updated") and view.get("file_kind") == "new" and view.get("old_rejected"):
        return "successor_completed"
    if view.get("authority_updated") and view.get("file_kind") == "absent":
        return "correct_stop"
    return "abstain"


def diagnose_d2(view: dict) -> str:
    if view.get("receipt_digest") and view.get("physical_digest") and view["receipt_digest"] != view["physical_digest"]:
        return "payload_mismatch"
    if view.get("claim_phase") == "started" and not view.get("receipt_digest") and view.get("file_kind") == "absent":
        return "authorized_not_published"
    if view.get("receipt_digest") and view.get("physical_digest") and view["receipt_digest"] == view["physical_digest"] and view.get("old_rejected"):
        return "successor_completed"
    return diagnose_d1_plus(view)


def evidence_sufficient(level: str, view: dict) -> bool:
    if level in {"D0", "D1"}:
        return False
    if level == "D1+":
        return diagnose_d1_plus(view) != "abstain"
    return diagnose_d2(view) != "abstain"


DIAGNOSERS = {
    "D0": (_view_d0, diagnose_d0),
    "D1": (_view_d1, diagnose_d1),
    "D1+": (_view_d1_plus, diagnose_d1_plus),
    "D2": (_view_d2, diagnose_d2),
}


def _file_kind(cell: dict) -> str:
    if not cell.get("file_present"):
        return "absent"
    if cell.get("task_terminal_match"):
        return "new"
    if cell.get("world_stale_payload"):
        return "old"
    return "other"


def _authority_updated(cell: dict) -> bool:
    return any(item["name"] == "authority_updated" for item in cell.get("timeline") or [])


def from_e9(summary: dict) -> list[dict]:
    records = []
    for cell in summary["cells"]:
        records.append({
            "source": "e9",
            "study": cell["study"],
            "mechanism": cell["mechanism"],
            "notice": cell["notice"],
            "holdout": cell["mechanism"] == "B2_nonatomic" and cell["notice"] == "delayed",
            "task_terminal_match": cell["task_terminal_match"],
            "file_kind": _file_kind(cell),
            "file_present": cell["file_present"],
        "tool_trace": ["write"] if cell["old_accepted"] else [],
        "authority_updated": _authority_updated(cell),
        "old_accepted": cell["old_accepted"],
        "old_rejected": not cell["old_accepted"],
            "stale_credential_accepted": cell["stale_credential_accepted"],
            "successor_completed": cell["successor_completed"],
            "world_stale_payload": cell["world_stale_payload"],
            "claim_phase": None,
            "receipt_digest": None,
            "physical_digest": None,
            "receiver_effect": "happened" if cell["old_accepted"] else "absent",
            "runtime_can_see_receiver": False,
        })
    for item in summary["ablations"]:
        if item["name"] != "full_minus_recovery":
            continue
        records.append({
            "source": "e9-ablation",
            "study": "supersede",
            "mechanism": "B3",
            "notice": "missing",
            "holdout": False,
            "task_terminal_match": False,
            "file_kind": "absent" if not item.get("file_present") else "old",
            "file_present": item.get("file_present"),
            "tool_trace": [],
            "authority_updated": False,
            "old_accepted": False,
            "old_rejected": False,
            "stale_credential_accepted": False,
            "successor_completed": False,
            "claim_phase": item.get("phase"),
            "receipt_digest": None,
            "physical_digest": None,
            "receiver_effect": "absent",
            "runtime_can_see_receiver": False,
        })
    records.append({
        "source": "protocol-case",
        "study": "receiver",
        "mechanism": "B0",
        "notice": None,
        "holdout": True,
        "task_terminal_match": False,
        "file_kind": "absent",
        "file_present": False,
        "tool_trace": [],
        "authority_updated": False,
        "old_accepted": False,
        "old_rejected": False,
        "stale_credential_accepted": False,
        "successor_completed": False,
        "claim_phase": None,
        "receipt_digest": None,
        "physical_digest": None,
        "receiver_effect": "happened",
        "runtime_can_see_receiver": False,
        "unprovable_receiver": True,
        "note": "接收端知道效果发生，运行时没有回执也不能查询接收端",
    })
    return records


def from_e10(summary: dict) -> list[dict]:
    records = []
    for row in summary["rows"]:
        if row.get("status") in {"未测", "环境差异"}:
            continue
        if row["fault"] not in {"staging_replace", "staging_truncate", "external_target"}:
            continue
        matched = bool(row.get("physical_payload_matches_claim"))
        records.append({
            "source": "e10",
            "study": "publish",
            "mechanism": row["publisher"],
            "notice": None,
            "holdout": row["fault"] == "external_target",
            "task_terminal_match": matched,
            "file_kind": "other",
            "file_present": True,
        "tool_trace": ["publish"],
        "authority_updated": False,
        "old_accepted": False,
        "old_rejected": False,
            "stale_credential_accepted": False,
            "successor_completed": False,
            "claim_phase": "unknown" if row.get("unknown_outcome") else "completed",
            "claim_result_digest": "claim",
            "receipt_digest": "claim" if row.get("receipt_matches_physical_effect") else None,
            "physical_digest": "claim" if matched else "other",
            "receiver_effect": "absent",
            "runtime_can_see_receiver": False,
        })
    return records


def _f1(pairs: list[tuple[str, str]]) -> float | None:
    scores = []
    for label in LABELS:
        tp = sum(gold == label and pred == label for gold, pred in pairs)
        fp = sum(pred == label and gold != label for gold, pred in pairs)
        fn = sum(gold == label and pred != label for gold, pred in pairs)
        if tp + fp + fn == 0:
            continue
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))
    if not scores:
        return None
    return sum(scores) / len(scores)


def score(records: list[dict]) -> dict:
    levels = {}
    primary = [record for record in records if not record.get("holdout")]
    holdout = [record for record in records if record.get("holdout")]
    for name, (make_view, diagnose) in DIAGNOSERS.items():
        pairs = []
        calibrated = 0
        false_confirm = 0
        abstain = 0
        labeled = 0
        recalled = 0
        for record in primary:
            view = make_view(record)
            predicted = diagnose(view)
            gold = oracle_label(record)
            sufficient = evidence_sufficient(name, view)
            if predicted == "abstain":
                abstain += 1
            if sufficient:
                calibrated += int(predicted == gold)
            else:
                calibrated += int(predicted == "abstain")
            if predicted in {"successor_completed", "correct_stop"} and predicted != gold:
                false_confirm += 1
            if gold in LABELS:
                labeled += 1
                recalled += int(predicted == gold)
                pairs.append((gold, predicted))
        levels[name] = {
            "n": len(primary),
            "macro_f1": _f1(pairs),
            "abstention_rate": abstain / len(primary) if primary else None,
            "false_confirmation_rate": false_confirm / len(primary) if primary else None,
            "evidence_calibrated_accuracy": calibrated / len(primary) if primary else None,
            "labeled_recall": recalled / labeled if labeled else None,
            "fields": len(make_view(primary[0])) if primary else 0,
        }
    holdout_rows = []
    for record in holdout:
        view = _view_d2(record)
        holdout_rows.append({
            "source": record["source"],
            "mechanism": record.get("mechanism"),
            "notice": record.get("notice"),
            "oracle": oracle_label(record),
            "d2": diagnose_d2(view),
        })
    d2_gain = None
    if levels["D2"]["macro_f1"] is not None and levels["D1+"]["macro_f1"] is not None:
        d2_gain = levels["D2"]["macro_f1"] - levels["D1+"]["macro_f1"]
    return {
        "levels": levels,
        "d2_minus_d1_plus_macro_f1": d2_gain,
        "contribution": "故障实证评估" if d2_gain is None or levels["D2"]["evidence_calibrated_accuracy"] <= levels["D1+"]["evidence_calibrated_accuracy"] else "D2 的证据校准正确性高于 D1+",
        "holdout": holdout_rows,
        "unprovable_effect_abstains": diagnose_d2(_view_d2(next(record for record in records if record["source"] == "protocol-case"))) == "abstain",
    }


def run(e9_path: Path, e10_path: Path, out: Path) -> dict:
    e9 = json.loads(e9_path.read_text(encoding="utf-8"))
    e10 = json.loads(e10_path.read_text(encoding="utf-8"))
    records = from_e9(e9) + from_e10(e10)
    summary = score(records)
    summary["e9"] = str(e9_path)
    summary["e10"] = str(e10_path)
    summary["protocol"] = "docs/开发文档/补实验协议.md"
    out.mkdir(parents=True, exist_ok=True)
    (out / "confirm.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--e9", type=Path, required=True)
    parser.add_argument("--e10", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("results/runtime/correctness/e12"))
    args = parser.parse_args()
    summary = run(args.e9, args.e10, args.out)
    print(json.dumps(summary["levels"], ensure_ascii=False))


if __name__ == "__main__":
    main()
