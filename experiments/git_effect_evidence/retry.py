"""Check a real conditional retry on the two histories whose Git tip returned to A."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .run import REF, git, save, scenario


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for history in ("not_dispatched", "reverted"):
        repo = output / f"{history}.git"
        row = scenario(repo, history, "retained", True)
        a, b = row["planned"]["old"], row["planned"]["new"]
        if row["target"] != a:
            raise ValueError("The two retry cases must begin at the old tip")
        git(repo, "update-ref", "-m", "operation:retry", REF, b, a)
        final = git(repo, "rev-parse", REF)
        if final != b:
            raise ValueError("Conditional retry did not publish the target OID")
        rows.append({
            "history": history, "pre_retry": row, "new_tip": final,
            "retry_wrote": True, "preceding_rollback_overridden": history == "reverted",
        })
    if rows[0]["pre_retry"]["transport_marker_hidden"] != rows[1]["pre_retry"]["transport_marker_hidden"]:
        raise ValueError("The two outcomes have distinguishable pre-retry transport views")
    result = {"cases": rows, "rollback_overrides": sum(r["preceding_rollback_overridden"] for r in rows)}
    save(output / "retry-report.json", result)
    print(json.dumps({"cases": len(rows), "rollback_overrides": result["rollback_overrides"]}))


if __name__ == "__main__":
    main()
