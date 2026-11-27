"""Drive an opt-in model coding job using a fixed tool surface and a Docker test bridge."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen

from epochgate_runtime.agent_decision import order_plan, repair_plan
from epochgate_runtime.agent_loop import api_key


READABLE = {"ISSUE.md", "shipping.py", "rules.py", "test_shipping.py"}
PROMPT = """You are a repository coding agent. Respond with exactly one JSON object, with no other output.
Only choose an action in allowed_actions. Follow required_reply_shape if present. First create a plan DAG:
{"action":"plan","plan":[{"step_id":"inspect","read_set":["ISSUE.md"],"dependencies":[]}]}
Then choose {"action":"read","path":"shipping.py"}, {"action":"test"},
{"action":"write","path":"shipping.py","step_id":"implement","content":"complete Python source"},
or {"action":"finish"}. Read the issue, implementation, configuration and tests before modifying.
Start by collecting a failing test, then modify and test. After a world change, use
{"action":"repair","plan":[...]} to replace only marked invalidated plan nodes:
same step_id, dependencies and read_set. Keep valid work; read the changed file and
run tests against the new world before finish. A repair proposes new descriptions for
the invalidated step identifiers. The harness retains their dependency edges.
Source-only task; no shell, no network tools.
Plan test steps reading rules.py and depending on implementation, and implementation
reading rules.py. Use an independent first node reading ISSUE.md only, to preserve valid issue analysis
when rules.py changes. Only the exact filenames ISSUE.md, shipping.py, rules.py, test_shipping.py
are exposed. The action stream can include multiple test runs. Return no private reasoning."""


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def save(path, value):
    staging = path.with_suffix(".tmp")
    staging.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(staging, path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_file(path, content):
    staging = path.with_suffix(".stage")
    staging.write_text(content, encoding="utf-8", newline="\n")
    os.replace(staging, path)


def coding_plan(nodes):
    return order_plan([{field: node[field] for field in ("step_id", "read_set", "dependencies", "description")
                        if field in node} for node in nodes])


def permitted(state):
    if not state["plan"]:
        return {"plan"}
    trace = state["events"]
    if state["invalidated"]:
        change = max(index for index,row in enumerate(trace) if row["action"] == "world_change")
        fresh = any(row["action"]=="read" and row.get("path")=="rules.py" for row in trace[change+1:])
        return {"repair"} if fresh else {"read"}
    files = {row.get("path") for row in trace if row["action"] == "read"}
    if not READABLE <= files:
        return {"read"}
    tests = [row for row in trace if row["action"] == "test_result"]
    if not tests:
        return {"test"}
    if state["changed"] and tests[-1].get("phase") == "after" and tests[-1]["exit_code"] == 0:
        return {"finish"}
    return {"write","test"}


def decide(state):
    key = api_key()
    if not key:
        raise RuntimeError("DeepSeek credentials are unavailable")
    context = {key: value for key, value in state.items() if key != "events"}
    context["allowed_actions"] = sorted(permitted(state))
    context["files_still_required"] = sorted(READABLE - {row.get("path") for row in state["events"] if row["action"]=="read"})
    if context["allowed_actions"] == ["repair"]:
        context["required_reply_shape"] = {"action":"repair","plan":[
            {"step_id": key,"description":"Describe the revised step using the new configuration"}
            for key in state["invalidated"]
        ]}
    elif state["invalidated"]:
        context["required_reply_shape"] = {"action":"read","path":"rules.py"}
    recent = state["events"][-5:]
    context["observations"] = [{key: value for key, value in row.items()
                                if key not in {"usage", "before", "after", "content", "output"}}
                               for row in recent]
    context["files_read"] = {row["path"]: row["content"] for row in state["events"] if "content" in row}
    last_test = next((row for row in reversed(state["events"]) if row["action"] == "test_result"), None)
    if last_test:
        context["last_test"] = {"exit_code": last_test["exit_code"], "phase": last_test["phase"],
                                "output": last_test["output"][-1500:]}
    payload = {
        "model": "deepseek-flash", "thinking": {"type": "disabled"},
        "max_tokens": 1200, "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(context)}],
    }
    request = Request("https://api.deepseek.com/chat/completions", data=json.dumps(payload).encode(),
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    start = time.perf_counter()
    with urlopen(request, timeout=50) as response:
        result = json.load(response)
    usage = {"model": result["model"], **result["usage"], "seconds": time.perf_counter() - start}
    try:
        data = json.loads(result["choices"][0]["message"]["content"])
        if not isinstance(data, dict):
            raise ValueError("Expected a single JSON action object")
    except (ValueError, KeyError, TypeError):
        data = {"action": "invalid_response"}
    return data, usage


def execute_step(root, state, model_action):
    repo = root / "repo"
    action = model_action.get("action")
    trace = state["events"]
    if action == "invalid_response":
        return {"action": action, "result": "RETURN_ONE_JSON_ACTION"}
    if action == "plan" and not state["plan"]:
        plan = coding_plan(model_action["plan"])
        affected = repair_plan(plan, {}, {"rules.py": {"value": "changed"}}, strategy="version")["affected_steps"]
        if len(plan) < 3 or not affected or len(affected) == len(plan):
            return {"action": action, "result": "PLAN_MUST_DECLARE_CONFIG_DEPENDENCY"}
        state["plan"] = plan
        return {"action": action, "steps": state["plan"]}
    if not state["plan"]:
        return {"action": action, "result": "PLAN_REQUIRED"}
    if action == "repair":
        pending = set(state["invalidated"])
        replacements = model_action.get("plan") or []
        old = {node["step_id"]: node for node in state["plan"]}
        proposed = {node["step_id"]: node for node in replacements}
        accepted = pending.intersection(proposed)
        if not accepted or proposed.keys() - old.keys():
            return {"action": action, "result": "REPAIR_SCOPE_MISMATCH", "invalidated": sorted(pending)}
        before = state["plan"]
        revised = [{**node, "description": proposed[k].get("description", node.get("description",""))}
                   if k in accepted else node for k, node in old.items()]
        state["plan"] = coding_plan(revised)
        state["invalidated"] = sorted(pending - accepted)
        state["repaired"] = not state["invalidated"]
        return {"action": action, "before": before, "after": state["plan"], "invalidated": sorted(accepted)}
    if action == "read":
        path = str(model_action.get("path", "")).replace("\\", "/").removeprefix("./").removeprefix("repo/")
        if path not in READABLE:
            return {"action": action, "result": "TOOL_NOT_ALLOWED", "allowed": sorted(READABLE)}
        return {"action": action, "path": path, "content": (repo / path).read_text(encoding="utf-8-sig")}
    if action == "test":
        ticket = len(trace)
        state["pending_test"] = ticket
        return {"action": action, "ticket": ticket}
    if action == "write" and model_action.get("path") == "shipping.py":
        missing = READABLE - {e.get("path") for e in trace if e["action"] == "read"}
        if missing:
            return {"action": action, "result": "READS_REQUIRED", "missing": sorted(missing)}
        if not any(e["action"] == "test_result" and e["exit_code"] != 0 for e in trace):
            return {"action": action, "result": "BASELINE_FAILURE_REQUIRED"}
        if state["invalidated"]:
            return {"action": action, "result": "REPAIR_REQUIRED"}
        target = repo / "shipping.py"
        content = model_action["content"]
        if len(content) > 12000:
            return {"action": action, "result": "CONTENT_LIMIT"}
        compile(content, "shipping.py", "exec")
        before = digest(target.read_text(encoding="utf-8-sig"))
        after = digest(content)
        if before == after:
            return {"action": action, "result": "ALREADY_PRESENT", "sha256": after}
        if any(e.get("action") == "write" and e.get("sha256") == after and e.get("result") == "VERIFIED"
               for e in trace):
            return {"action": action, "result": "HISTORICAL_EFFECT_ALREADY_CONFIRMED", "sha256": after}
        state["pending_write"] = {"before": before, "sha256": after, "path": "shipping.py"}
        save(root / "trace.json", state)
        write_file(target, content)
        return resolve_write(root, state)
    if action == "finish":
        tests = [e for e in trace if e["action"] == "test_result"]
        current = digest((repo / "shipping.py").read_text(encoding="utf-8-sig"))
        if (state["changed"] and state["repaired"] and tests and tests[-1]["exit_code"] == 0
                and tests[-1]["source_sha256"] == current and tests[-1]["phase"] == "after"):
            state["status"] = "completed"
            return {"action": action, "result": "VERIFIED", "source_sha256": current}
        return {"action": action, "result": "POST_CHANGE_TEST_REQUIRED"}
    return {"action": action, "result": "TOOL_NOT_ALLOWED"}


def resolve_write(root, state):
    pending = state.pop("pending_write")
    actual = digest((root / "repo" / pending["path"]).read_text(encoding="utf-8-sig"))
    result = "VERIFIED" if actual == pending["sha256"] else "NOT_APPLIED" if actual == pending["before"] else "UNKNOWN"
    if result == "UNKNOWN":
        state["status"] = "unknown"
    return {"action": "write", "result": result, **pending}


def advance(root):
    path = root / "trace.json"
    state = read_json(path) if path.is_file() else {
        "status": "running", "events": [], "plan": [], "invalidated": [], "changed": False, "repaired": False,
        "started": time.time(),
    }
    if state.get("pending_write"):
        state["events"].append(resolve_write(root, state))
        save(path, state)
    if state.get("pending_test") is not None:
        test = root / "test-result.json"
        if not test.is_file():
            return
        event = read_json(test)
        if event["ticket"] != state["pending_test"]:
            return
        state["pending_test"] = None
        state["events"].append({"action": "test_result", "phase": "after" if state["changed"] else "before", **event})
        if event["exit_code"] == 0 and not state["changed"]:
            rule = root / "repo" / "rules.py"
            old = digest(rule.read_text(encoding="utf-8-sig"))
            # Announce a concurrent maintainer configuration update after the first passing suite.
            write_file(rule, "FREE_FROM = 6500\n")
            new = digest(rule.read_text(encoding="utf-8-sig"))
            affected = repair_plan(state["plan"], {"rules.py": {"value": old}}, {"rules.py": {"value": new}}, strategy="version")
            state["changed"] = True
            state["invalidated"] = affected["affected_steps"]
            for node in state["plan"]:
                if set(node.get("dependencies", ())) & set(state["invalidated"]) and node["step_id"] not in state["invalidated"]:
                    state["invalidated"].append(node["step_id"])
            state["events"].append({"action": "world_change", "path": "rules.py", "before": old, "after": new,
                                    "invalidated": state["invalidated"]})
        save(path,state)
    if state["status"] != "running":
        return
    if sum(event.get("usage", {}).get("total_tokens", 0) for event in state["events"]) >= 30000 or len(state["events"]) >= 55:
        state["status"] = "budget_exhausted"
        save(path,state)
        return
    action, usage = decide(state)
    start = time.perf_counter()
    try:
        allowed = permitted(state)
        event = execute_step(root, state, action) if action.get("action") in allowed else {
            "action": action.get("action", "invalid"), "result": "ACTION_OUTSIDE_CURRENT_PHASE",
            "allowed": sorted(allowed)}
    except (KeyError, ValueError, TypeError, SyntaxError) as exc:
        event = {"action": action.get("action","invalid"), "result": "INVALID_TOOL_ARGUMENTS", "error": str(exc)}
    state["events"].append({**event, "usage": usage, "tool_seconds": time.perf_counter() - start})
    state["elapsed_seconds"] = time.time() - state["started"]
    save(path, state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    from epochgate_runtime.cli import load_project_env
    load_project_env()
    advance(args.directory)
