import json

from epochgate.eg2_events import append_event, make_event


def test_saved_events_can_be_reread_in_order(tmp_path):
    path = tmp_path / "episode.jsonl"
    for seq, status in ((1, "started"), (2, "success")):
        append_event(
            path,
            make_event(
                episode_id="ep",
                session_id="ses",
                event_seq=seq,
                event_type="session.recorded",
                status=status,
                payload={"seq": seq},
            ),
        )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["payload"]["seq"] for row in rows] == [1, 2]
    assert rows[0]["schema_version"] == "eg2.events.v1"
