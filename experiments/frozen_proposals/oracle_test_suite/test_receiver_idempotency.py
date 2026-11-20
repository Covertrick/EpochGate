from reliability_contract import SharedReceiver


def test_duplicate_delivery_does_not_duplicate_logical_effect(tmp_path):
    receiver = SharedReceiver(tmp_path, "effect.bin")
    first = receiver.accept(effect_key="effect:p", attempt_id="a", payload=b"SHIP|same")
    second = receiver.accept(effect_key="effect:p", attempt_id="b", payload=b"SHIP|same")
    assert first["applied"] is True
    assert second["status"] == "replayed"
    assert second["applied"] is False
    assert receiver.accepted_count("effect:p") == 1
    assert receiver.physical() == b"SHIP|same"


def test_conflicting_payload_does_not_overwrite(tmp_path):
    receiver = SharedReceiver(tmp_path, "effect.bin")
    receiver.accept(effect_key="effect:p", attempt_id="a", payload=b"SHIP|legal")
    conflict = receiver.accept(effect_key="effect:p", attempt_id="b", payload=b"SHIP|other")
    assert conflict["status"] == "conflict"
    assert receiver.accepted_count("effect:p") == 1
    assert receiver.physical() == b"SHIP|legal"
