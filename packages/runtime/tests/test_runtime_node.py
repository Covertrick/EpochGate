import json
import os
import subprocess
from pathlib import Path

from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.service import ControlServer
from epochgate_runtime.sink import EffectSink


def test_node_client_reserves_and_sees_revoke(tmp_path: Path):
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    sink = EffectSink(tmp_path / "sink.sqlite")
    server = ControlServer(ledger, sink, tmp_path)
    server.start()
    env = os.environ.copy()
    env["EG_CONTROL_URL"] = server.base_url
    env["EG_RUN_ID"] = "node-run"
    try:
        completed = subprocess.run(
            ["node", "--test", "packages/eg-workspace/tests/control-client.test.js"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    finally:
        server.stop()
    assert completed.returncode == 0, completed.stdout + completed.stderr
    fresh = RunLedger(tmp_path / "ledger.sqlite")
    row = fresh.effect_row("node-effect")
    assert row is not None
    assert fresh.get_run("node-run").eligible is False
    fresh.close()
    ledger.close()
    sink.close()
    assert json.loads(json.dumps(row))["phase"] == "started"
