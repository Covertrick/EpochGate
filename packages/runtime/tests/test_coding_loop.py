import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[3] / "experiments/coding_loop/agent.py"
SPEC = importlib.util.spec_from_file_location("coding_fixture_agent", MODULE_PATH)
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def test_coding_receipt_does_not_replay_confirmed_bytes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "shipping.py"
    target.write_text("old = True\n", encoding="utf-8")
    state = {"plan": [{"step_id": "modify"}], "invalidated": [],
             "events": [{"action": "read", "path": path} for path in agent.READABLE] +
                       [{"action":"test_result","exit_code":1}]}
    action = {"action":"write","path":"shipping.py","step_id":"modify","content":"new = True\n"}
    accepted = agent.execute_step(tmp_path, state, action)
    assert accepted["result"] == "VERIFIED"
    state["events"].append(accepted)
    target.write_text("external = True\n", encoding="utf-8")
    replay = agent.execute_step(tmp_path, state, action)
    assert replay["result"] == "HISTORICAL_EFFECT_ALREADY_CONFIRMED"
    assert target.read_text(encoding="utf-8") == "external = True\n"


def test_coding_repair_preserves_graph_even_when_proposal_changes_edges(tmp_path):
    original = [
        {"step_id": "inspect", "read_set":["ISSUE.md"], "description":"read"},
        {"step_id": "verify", "read_set":["rules.py"], "dependencies":["inspect"], "description":"old"},
    ]
    state = {"plan": agent.coding_plan(original), "invalidated": ["verify"], "events": []}
    event = agent.execute_step(tmp_path,state,{"action":"repair","plan":[
        {"step_id":"verify","dependencies":[],"description":"retest revised threshold"}
    ]})
    assert event["after"][0] == event["before"][0]
    assert event["after"][1]["dependencies"] == ["inspect"]
    assert event["after"][1]["description"] == "retest revised threshold"
    assert state["repaired"]
