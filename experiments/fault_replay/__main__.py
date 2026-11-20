"""Run the Week 3 formal sample after the statistical plan is on disk."""

from __future__ import annotations

import hashlib

from fault_replay.analyze import build_report
from fault_replay.contract import WEEK3
from fault_replay.replay import replay_all, write_joint


def main() -> None:
    plan = WEEK3 / "statistical_plan.md"
    if not plan.is_file():
        raise SystemExit("statistical_plan.md is missing; refuse to interpret results")
    plan_sha = hashlib.sha256(plan.read_bytes()).hexdigest()
    (WEEK3 / "statistical_plan.sha256").write_text(plan_sha + "\n", encoding="utf-8")
    if not (WEEK3 / "crash_matrix.csv").is_file():
        raise SystemExit("crash_matrix.csv is missing; run python -m fault_replay.kill_runner first")
    rows = replay_all()
    write_joint(rows)
    # Re-read the plan hash. A change during the run is reported, not patched.
    current = hashlib.sha256(plan.read_bytes()).hexdigest()
    if current != plan_sha:
        raise SystemExit("statistical_plan.md changed during the run")
    report = build_report(rows, plan_sha)
    print(report["decision"], {name: item["pass"] for name, item in report["gates"].items()})


if __name__ == "__main__":
    main()
