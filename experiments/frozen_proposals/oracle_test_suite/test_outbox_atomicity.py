from frozen_proposals.baselines.outbox import DURABILITY_CLAIM, Outbox, crash_matrix
from frozen_proposals.db import connect, init_schema


def test_crash_matrix_points_and_idempotent_relay(tmp_path):
    rows = crash_matrix(tmp_path)
    by = {row["fault_point"]: row for row in rows}
    expected = [
        "before_db_transaction",
        "inside_transaction",
        "after_commit_before_send",
        "after_send_before_receipt",
        "after_receipt_before_ack",
        "after_local_ack",
        "duplicate_relay_after_ack",
    ]
    assert list(by) == expected
    assert by["before_db_transaction"]["business_present"] == 0
    assert by["before_db_transaction"]["outbox_present"] == 0
    assert by["inside_transaction"]["business_present"] == 0
    assert by["inside_transaction"]["outbox_present"] == 0
    assert by["after_commit_before_send"]["business_present"] == 1
    assert by["after_commit_before_send"]["outbox_present"] == 1
    assert by["after_commit_before_send"]["outbox_status"] == "PENDING"
    assert by["after_commit_before_send"]["logical_effect_count"] == 0
    assert by["after_send_before_receipt"]["logical_effect_count"] == 1
    assert by["after_send_before_receipt"]["receipt_stored"] == 0
    assert by["after_send_before_receipt"]["outbox_status"] == "UNRESOLVED"
    assert by["after_receipt_before_ack"]["memory_receipt_seen"] == 1
    assert by["after_receipt_before_ack"]["receipt_stored"] == 0
    assert by["after_local_ack"]["outbox_status"] == "ACKED"
    assert by["after_local_ack"]["receipt_stored"] == 1
    assert by["duplicate_relay_after_ack"]["logical_effect_count"] == 1
    assert by["duplicate_relay_after_ack"]["outbox_status"] == "ACKED"
    for row in rows:
        assert row["process_kill"] is False
        assert row["power_loss_claim"] is False
        assert row["journal_mode"] == "delete"
        assert str(row["synchronous"]).startswith("2")
        assert row["durability_claim"] == DURABILITY_CLAIM


def test_commit_rolls_back_when_cut_inside_transaction(tmp_path):
    conn = connect(tmp_path / "cut.sqlite")
    init_schema(conn)
    conn.execute("INSERT INTO authority (id, token, epoch, eligible) VALUES (1, 1, 1, 1)")

    class Cut:
        def hit(self, point: str) -> None:
            if point == "inside_transaction":
                raise RuntimeError("inside")

    from frozen_proposals.baselines.outbox import _request

    box = Outbox()
    try:
        box.commit(conn, _request(), Cut())
        raise AssertionError("commit should have raised")
    except RuntimeError:
        pass
    business = conn.execute("SELECT COUNT(*) AS n FROM business_effects").fetchone()["n"]
    outbox = conn.execute("SELECT COUNT(*) AS n FROM outbox").fetchone()["n"]
    assert business == 0
    assert outbox == 0
    conn.close()
