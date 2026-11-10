import json
import os
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.sandbox import (
    container_env_has_secret,
    docker_available,
    exec_in,
    lock_workspace,
    read_file,
    start_container,
    stop_container,
    term_then_kill,
    value_passes,
    workspace_writable_by_tool,
    write_file,
)
from epochgate_runtime.service import ControlServer
from epochgate_runtime.sink import EffectSink


BUGGY = "def value():\n    return 0\n"
GOLD = "def value():\n    return 1\n"


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "packages" / "eg-workspace" / "package.json").is_file():
            return parent
    raise RuntimeError("repository root not found")


def test_container_stage_argv_has_no_shell_redirect():
    completed = subprocess.run(
        ["node", "--test", "packages/eg-workspace/tests/container-stage.test.js"],
        cwd=repo_root(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


needs_linux = pytest.mark.skipif(not docker_available(), reason="Docker engine is not available")


@needs_linux
def test_linux_scorer_separates_base_gold_and_probe_and_publish_is_locked():
    agent = "eg-rt-score-agent"
    scorer = "eg-rt-score-scorer"
    start_container(agent)
    start_container(scorer)
    ledger = RunLedger()
    sink = EffectSink()
    server = ControlServer(ledger, sink, workspace=__import__("pathlib").Path("."))
    server.start()
    try:
        write_file(scorer, "bug.py", BUGGY)
        base_failed = not value_passes(scorer)
        write_file(scorer, "bug.py", GOLD)
        gold_passed = value_passes(scorer)
        write_file(scorer, "bug.py", BUGGY)
        write_file(scorer, "probe.txt", "hello\n")
        probe_passed = value_passes(scorer)
        assert base_failed and gold_passed and not probe_passed
        assert container_env_has_secret(agent) is False
        assert container_env_has_secret(scorer) is False

        lock_workspace(agent)
        assert workspace_writable_by_tool(agent) is False
        created = _post(server.base_url + "/runs", {"run_id": "linux-run", "session_id": "linux-session"})
        staging = "/scratch/.eg-staging/effect-linux"
        stage_with_plugin(agent, staging, GOLD)
        published = _post(server.base_url + "/effects/publish-container", {
            "container": agent,
            "run_id": "linux-run",
            "idempotency_key": "effect-linux",
            "fencing_token": created["fencing_token"],
            "relative_path": "bug.py",
            "staging_path": staging,
        })
        assert published["published"] is True
        assert published["sha256"] == __import__("hashlib").sha256(GOLD.encode("utf-8")).hexdigest()
        assert value_passes(agent) is True
        try:
            read_file(scorer, "bug.py")
            scorer_has_agent_file = read_file(scorer, "bug.py") == GOLD
        except RuntimeError:
            scorer_has_agent_file = False
        assert scorer_has_agent_file is False

        again = _post(server.base_url + "/effects/publish-container", {
            "container": agent,
            "run_id": "linux-run",
            "idempotency_key": "effect-linux",
            "fencing_token": created["fencing_token"],
            "relative_path": "bug.py",
            "staging_path": staging,
        })
        assert again["published"] is False
        assert again["status"] == "replayed"
        assert read_file(agent, "bug.py") == GOLD

        refused = _post(server.base_url + "/effects/publish-container", {
            "container": agent,
            "run_id": "linux-run",
            "idempotency_key": "effect-escape",
            "fencing_token": created["fencing_token"],
            "relative_path": "../bug.py",
            "staging_path": staging,
        })
        assert refused["published"] is False

        _post(server.base_url + "/runs/linux-run/revoke", {})
        late_staging = "/scratch/.eg-staging/effect-late"
        stage_with_plugin(agent, late_staging, "def value():\n    return 2\n")
        late = _post(server.base_url + "/effects/publish-container", {
            "container": agent,
            "run_id": "linux-run",
            "idempotency_key": "effect-late",
            "fencing_token": created["fencing_token"],
            "relative_path": "bug.py",
            "staging_path": late_staging,
        })
        assert late["published"] is False
        assert read_file(agent, "bug.py") == GOLD
    finally:
        server.stop()
        ledger.close()
        sink.close()
        stop_container(agent)
        stop_container(scorer)


@needs_linux
def test_tool_user_cannot_write_workspace_and_term_then_kill_is_linux():
    name = "eg-rt-bash-lock"
    start_container(name)
    try:
        lock_workspace(name)
        assert workspace_writable_by_tool(name) is False
        note = exec_in(name, ["python", "-c", "import pathlib; pathlib.Path('/scratch/note').write_text('ok')"])
        assert note.returncode == 0
        signals = term_then_kill(name)
        assert signals["ignored_term"] is True
        assert signals["killed"] is True
    finally:
        stop_container(name)


def stage_with_plugin(container: str, relative: str, content: str) -> None:
    script = """
import { stageArgv } from "./packages/eg-workspace/lib/container-stage.js";
import { spawnSync } from "node:child_process";
const argv = stageArgv(process.env.STAGE_PATH);
const prefix = process.platform === "win32"
  ? ["wsl.exe", "-d", "eg-alpine", "-u", "root", "--", "docker"]
  : ["docker"];
const result = spawnSync(prefix[0], [...prefix.slice(1), "exec", "-i", "-u", "1000:1000", "-w", "/workspace", process.env.STAGE_CONTAINER, ...argv], { input: process.env.STAGE_BODY, encoding: "utf-8" });
if (result.status !== 0) {
  process.stderr.write(result.stderr || result.stdout || "stage failed");
  process.exit(result.status || 1);
}
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=repo_root(),
        env={**os.environ, "STAGE_CONTAINER": container, "STAGE_PATH": relative, "STAGE_BODY": content},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-500:])


def _post(url: str, body: dict) -> dict:
    request = Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))
