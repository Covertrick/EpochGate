"""撤销与 supersede 的专门对照。不计入 48 或 144。"""

from __future__ import annotations

import json
from pathlib import Path

from m4_matrix import run_cell
from m4_tasks import PILOT, legal_payload, observed_payload


def main() -> None:
    task = dict(PILOT[0])
    world = json.loads(json.dumps(task["initial"]))
    for key, value in task["event"]["set"].items():
        cursor = world
        parts = key.split(".")
        for part in parts[:-1]:
            cursor = cursor[part]
        cursor[parts[-1]] = value
    old = observed_payload(task)
    nxt = legal_payload(task, world).encode("utf-8")
    out = Path("results/runtime/correctness/m4-dedicated")
    rows = []
    for kind in ("supersede", "revoke"):
        specimen = dict(task)
        specimen["successor_applicable"] = kind != "revoke"
        for arm in ("B2", "B3"):
            row = run_cell(
                specimen, arm, "S1", out / f"{kind}-{arm}",
                old_payload=old, next_payload=nxt, authority_kind=kind,
            )
            rows.append({
                "authority": kind,
                "arm": arm,
                "successor_applicable": row["successor_applicable"],
                "successor_completed": row["successor_completed"],
                "correct_stop": row["correct_stop"],
                "stale_credential_accepted": row["stale_credential_accepted"],
                "in_matrix": False,
            })
    summary = {"kind": "dedicated_revoke_versus_supersede", "in_matrix": False, "rows": rows}
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
