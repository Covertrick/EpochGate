import json

from epochgate.eg2_events import append_event, index_event, make_event, next_seq, spill_large_output, validate_event


def test_events_keep_sequence_and_the_index_does_not_rewrite_jsonl(tmp_path):
    jsonl = tmp_path / "events.jsonl"
    first = make_event(
        episode_id="ep-1",
        session_id="ses-1",
        event_seq=next_seq(jsonl),
        event_type="tool.execution.finished",
        status="success",
        payload={"tool_name": "eg_read"},
    )
    append_event(jsonl, first)
    second = make_event(
        episode_id="ep-1",
        session_id="ses-1",
        event_seq=next_seq(jsonl),
        event_type="tool.execution.finished",
        status="success",
        payload={"stdout": "x" * 50},
    )
    spilled = spill_large_output(second, tmp_path / "artifacts", limit=10)
    append_event(jsonl, spilled)
    index_event(tmp_path / "index.sqlite", first)
    original = jsonl.read_text(encoding="utf-8")
    index_event(tmp_path / "index.sqlite", second)
    assert jsonl.read_text(encoding="utf-8") == original
    rows = [json.loads(line) for line in original.splitlines()]
    assert [row["event_seq"] for row in rows] == [1, 2]
    assert "stdout_sha256" in rows[1]["payload"]
    assert validate_event(first) == []
