"""离线核对已经落盘的数字。不调用模型，不启动容器。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    freeze = json.loads((ROOT / "results/runtime/correctness/v3-coding/freeze.json").read_text(encoding="utf-8"))
    artifact = ROOT / "results/runtime/correctness/v3-coding/artifact"
    runner = sha256(artifact / "coding_v3.py")
    protocol = sha256(artifact / "Coding-v3协议.md")
    if runner != freeze["runner_sha256"] or protocol != freeze["protocol_sha256"]:
        raise SystemExit("v3 artifact hash does not match freeze.json")
    rescore = json.loads((ROOT / "results/runtime/correctness/v3-coding/rescore/summary.json").read_text(encoding="utf-8"))
    if rescore["passed_count"] != len(rescore["model_hidden_passed"]):
        raise SystemExit("rescore passed_count does not match the passed list")
    if rescore["assigned_confirm"] != 4 or rescore["budget_exceeded"] != ["code-click-ac0e297e"]:
        raise SystemExit("confirm denominator changed")
    outbox = json.loads((ROOT / "results/runtime/correctness/outbox-baseline/summary.json").read_text(encoding="utf-8"))
    if outbox["stale_token"] != "rejected" or outbox["fresh_token"] != "accepted" or outbox["claim_to_bytes"] is not False:
        raise SystemExit("outbox summary changed")
    regression = json.loads((ROOT / "results/runtime/correctness/final-regression/summary.json").read_text(encoding="utf-8"))
    if regression["power_loss"] != "unmeasured" or regression["result"] != "31 passed":
        raise SystemExit("regression record changed")
    print(json.dumps({"offline": "ok", "v3_hidden_passed": rescore["passed_count"], "confirm_denominator": 4}, ensure_ascii=False))


if __name__ == "__main__":
    main()
