"""Test ordinary Git push against reset and compensating-commit controls."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from .run import REF, git, save, scenario


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sender = output / "sender.git"
    plan = scenario(sender, "original", "retained", False)["planned"]
    rows = []
    for name, history in (
        ("not_dispatched", "not_dispatched"),
        ("reset_to_old_tip", "reverted"),
        ("compensating_commit", "original"),
    ):
        target = output / f"{name}.git"
        fixture = scenario(target, history, "retained", False)
        if fixture["planned"] != plan:
            raise ValueError("Sender and receiver disagree about fixture objects")
        if name == "compensating_commit":
            tree = git(target, "rev-parse", f"{plan['old']}^{{tree}}")
            revert = git(target, "commit-tree", tree, "-p", plan["new"], "-m", "compensate")
            git(target, "update-ref", "-m", "operation:compensate", REF, revert, plan["new"])
        before = git(target, "rev-parse", REF)
        try:
            git(sender, "push", "--porcelain", target.as_uri(), f"{plan['new']}:{REF}")
            returned = 0
        except subprocess.CalledProcessError as exc:
            returned = exc.returncode
        after = git(target, "rev-parse", REF)
        if name == "compensating_commit":
            expected = returned != 0 and before == after
        else:
            expected = returned == 0 and before == plan["old"] and after == plan["new"]
        if not expected:
            raise ValueError(f"Unexpected ordinary push effect in {name}")
        rows.append({"case": name, "returncode": returned, "target_before": before,
                     "target_after": after, "force_used": False})
    save(output / "report.json", {"plan": plan, "rows": rows})
    print("3/3 transport controls passed")


if __name__ == "__main__":
    main()
