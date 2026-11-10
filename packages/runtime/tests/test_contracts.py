import json
import subprocess
from pathlib import Path

import jsonschema
import pytest

from epochgate_runtime.events import envelope, load_schema, schema_path, validate_runtime_event
from epochgate_runtime.ledger import RunLedger


def test_python_and_schema_reject_a_missing_required_field():
    schema = load_schema()
    event = {
        "schema_version": "runtime.v1",
        "event_id": "run:1",
        "run_id": "run",
        "attempt_id": None,
        "effect_id": None,
        "epoch": 1,
        "event_type": "file_published",
        "timestamp": "2026-11-10T00:00:00Z",
        "ledger_sequence": 1,
    }
    validate_runtime_event(event, schema)
    broken = dict(event)
    del broken["event_id"]
    with pytest.raises(jsonschema.ValidationError):
        validate_runtime_event(broken, schema)


def test_ledger_export_matches_runtime_v1():
    ledger = RunLedger()
    ledger.create_run("run")
    ledger.append_event("run", "attempt_opened", {"attempt_id": "a"})
    exported = envelope("run", ledger.events("run")[0])
    validate_runtime_event(exported)
    assert exported["schema_version"] == "runtime.v1"
    assert exported["ledger_sequence"] == 1
    ledger.close()


def test_node_checker_uses_the_same_schema_file():
    text = schema_path().read_text(encoding="utf-8")
    assert json.loads(text)["required"] == load_schema()["required"]
    completed = subprocess.run(
        ["node", "--test", "packages/contracts/tests/runtime-event.test.js"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
