import json

from frozen_proposals.freeze_artifacts import freeze
from frozen_proposals.frozen_proposal_runner import load_jsonl, run_selected


def test_abandoned_run_is_kept_and_retry_gets_a_new_id(tmp_path):
    freeze(tmp_path)
    events = tmp_path / "run_events.jsonl"
    events.write_text(
        json.dumps(
            {
                "event": "RUN_STARTED",
                "run_id": "w2-RESUME-p01-Direct-1",
                "cohort": "RESUME",
                "proposal_id": "p01",
                "path": "Direct",
                "scenario_id": "S01",
                "common_core": True,
                "mechanism_role": "MAIN_COMPARISON",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    run_selected(tmp_path, ["p01"], "RESUME")
    saved = load_jsonl(events)
    abandoned = [row for row in saved if row.get("event") == "RUN_ABANDONED"]
    assert abandoned and abandoned[0]["run_id"] == "w2-RESUME-p01-Direct-1"
    terminals = [row for row in saved if row.get("event") == "RUN_TERMINAL" and row.get("path") == "Direct"]
    assert terminals
    assert terminals[0]["run_id"] != "w2-RESUME-p01-Direct-1"
    manifests = load_jsonl(tmp_path / "run_manifest.jsonl")
    direct = [row for row in manifests if row.get("path") == "Direct" and row.get("cohort") == "RESUME"]
    assert any(row["runner_status"] == "ABANDONED" for row in direct)
    assert any(row["runner_status"] != "ABANDONED" for row in direct)
