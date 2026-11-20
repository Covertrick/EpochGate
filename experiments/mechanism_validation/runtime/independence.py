"""Assess whether the new Claim and Outbox mechanisms share a core."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

RUNTIME = Path(__file__).resolve().parent


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _function_texts(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    bodies: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            segment = ast.get_source_segment(text, node) or ""
            bodies.append(segment)
    return bodies


def assess() -> dict[str, Any]:
    from mechanism_validation.runtime.claim_mechanism import ClaimMechanism, run_claim_path
    from mechanism_validation.runtime.outbox_mechanism import OutboxMechanism, run_outbox_path

    claim_src = (RUNTIME / "claim_mechanism.py").read_text(encoding="utf-8")
    outbox_src = (RUNTIME / "outbox_mechanism.py").read_text(encoding="utf-8")
    both_tables = []
    for path in sorted(RUNTIME.glob("*.py")):
        if path.name in {"independence.py", "not_shared.py"}:
            continue
        for body in _function_texts(path):
            if "claim_rows" in body and "outbox_relay" in body:
                both_tables.append(f"{path.name}")
    claim_imports = _imports(RUNTIME / "claim_mechanism.py")
    outbox_imports = _imports(RUNTIME / "outbox_mechanism.py")
    checks = {
        "commit_functions_distinct": ClaimMechanism.commit_claim.__code__ is not OutboxMechanism.commit_business_and_relay.__code__,
        "recover_functions_distinct": ClaimMechanism.recover.__code__ is not OutboxMechanism.recover.__code__,
        "acknowledge_functions_distinct": ClaimMechanism.acknowledge_claim.__code__ is not OutboxMechanism.acknowledge_relay.__code__,
        "transition_functions_distinct": ClaimMechanism.transition_to_sending.__code__ is not OutboxMechanism.transition_attempt.__code__,
        "run_functions_distinct": run_claim_path.__code__ is not run_outbox_path.__code__,
        "modules_do_not_import_each_other": (
            "mechanism_validation.runtime.outbox_mechanism" not in claim_imports
            and "mechanism_validation.runtime.claim_mechanism" not in outbox_imports
        ),
        "no_shared_private_transition": (
            "private_transition" not in claim_src
            and "private_transition" not in outbox_src
            and "not_shared" not in claim_src
            and "not_shared" not in outbox_src
        ),
        "tables_disjoint": (
            "outbox_relay" not in claim_src
            and "outbox_business" not in claim_src
            and "claim_rows" not in outbox_src
        ),
        "no_function_mentions_both_tables": not both_tables,
    }
    if not checks["commit_functions_distinct"] or not checks["recover_functions_distinct"] or not checks["no_shared_private_transition"]:
        decision = "SAME_CORE"
    elif not all(checks.values()):
        decision = "PARTIALLY_SHARED"
    else:
        decision = "INDEPENDENT"
    return {
        "decision": decision,
        "decision_scope": "new week5 runtime commit, persistence, state transitions, recovery, and acknowledge",
        "historical_week2_to_week4": "PARTIALLY_SHARED",
        "history_not_rewritten": True,
        "shared_receiver_does_not_establish_independence": True,
        "shared_but_not_sufficient": [
            "mechanism_validation.runtime.harness",
            "mechanism_validation.runtime.receiver_client.PathReceiverClient",
            "mechanism_validation.runtime.scorer",
            "mechanism_validation.runtime.hashing",
            "mechanism_validation.runtime.sqlite_util",
            "mechanism_validation.runtime.faults.FaultState",
        ],
        "checks": checks,
        "functions_mentioning_both_tables": both_tables,
        "claim_only": [
            "ClaimMechanism.commit_claim",
            "ClaimMechanism.transition_to_sending",
            "ClaimMechanism.acknowledge_claim",
            "ClaimMechanism.recover",
            "run_claim_path",
            "table:claim_authority",
            "table:claim_rows",
        ],
        "outbox_only": [
            "OutboxMechanism.commit_business_and_relay",
            "OutboxMechanism.transition_attempt",
            "OutboxMechanism.relay_after_commit",
            "OutboxMechanism.acknowledge_relay",
            "OutboxMechanism.recover",
            "run_outbox_path",
            "table:outbox_authority",
            "table:outbox_business",
            "table:outbox_relay",
            "constant:RETRY_LIMIT=3",
            "status:DEAD",
        ],
        "note": "共享接收端、harness 和计分器不能单独构成机制独立。Week 2 到 Week 4 仍是 PARTIALLY_SHARED，这里不改写那段历史。",
    }


def main() -> None:
    payload = assess()
    destination = RUNTIME.parent / "architecture" / "mechanism_independence.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(payload["decision"])


if __name__ == "__main__":
    main()
