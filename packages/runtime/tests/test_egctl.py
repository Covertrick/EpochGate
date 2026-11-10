import json
from pathlib import Path
from urllib.request import Request, urlopen

from typer.testing import CliRunner

from epochgate_runtime.api import Runtime
from epochgate_runtime.demo import run_crash_recovery, run_stale_write
from epochgate_runtime.egctl import app
from epochgate_runtime.service import ControlServer

runner = CliRunner()


def test_offline_demos_can_repeat_without_deleting_the_database(tmp_path: Path):
    first = run_stale_write(tmp_path)
    second = run_stale_write(tmp_path)
    crash = run_crash_recovery(tmp_path)
    again = run_crash_recovery(tmp_path)
    assert first["verified"] is True
    assert second["verified"] is True
    assert first["run_id"] != second["run_id"]
    assert first["duplicate_accepts"] == 0
    assert crash["verified"] is True
    assert again["verified"] is True
    runtime = Runtime.open(tmp_path / "epochgate.db", data_dir=tmp_path)
    try:
        rows = {item["run_id"] for item in runtime.list_runs()}
        assert first["run_id"] in rows
        assert crash["recovery"] == "confirmed_from_sink"
        assert crash["second_recovery"] == "replay"
    finally:
        runtime.close()
    html_path = Path(first["report_html"])
    assert "eligibility_superseded" in html_path.read_text(encoding="utf-8") or "result_withheld" in html_path.read_text(encoding="utf-8")


def test_cli_demo_prints_the_control_result(tmp_path: Path):
    result = runner.invoke(app, ["demo", "--scenario", "stale-write", "--offline", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Attempt #1           REVOKED" in result.output
    assert "Attempt #2           COMMITTED" in result.output
    assert "Effect receipt       VERIFIED" in result.output
    assert "Duplicate accepts    0" in result.output
    listed = runner.invoke(app, ["runs", "list", "--data-dir", str(tmp_path)])
    assert listed.exit_code == 0, listed.output
    run_id = next(line.split()[1] for line in result.output.splitlines() if line.startswith("Run "))
    inspected = runner.invoke(app, ["runs", "inspect", run_id, "--data-dir", str(tmp_path)])
    assert inspected.exit_code == 0, inspected.output
    assert "eligibility_superseded" in inspected.output
    verified = runner.invoke(app, ["effects", "verify", run_id, "--data-dir", str(tmp_path)])
    assert verified.exit_code == 0, verified.output
    assert "verified=true" in verified.output


def test_init_does_not_overwrite_config(tmp_path: Path):
    config = tmp_path / "epochgate.yaml"
    data = tmp_path / "state"
    config.write_text(f"data_dir: {data.as_posix()}\nport: 8765\n", encoding="utf-8")
    result = runner.invoke(app, ["init", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "config exists" in result.output
    assert "schema_version=1" in result.output
    assert config.read_text(encoding="utf-8").startswith("data_dir:")


def test_restart_keeps_the_run(tmp_path: Path):
    runtime = Runtime.open(tmp_path / "epochgate.db", data_dir=tmp_path)
    server = ControlServer(runtime.ledger, runtime.sink, runtime.workspace)
    server.start()
    try:
        request = Request(
            server.base_url + "/runs",
            data=json.dumps({"run_id": "kept", "session_id": "s"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            created = json.loads(response.read().decode())
        with urlopen(server.base_url + "/health") as response:
            assert json.loads(response.read().decode())["ok"] is True
    finally:
        server.stop()
        runtime.close()
    reopened = Runtime.open(tmp_path / "epochgate.db", data_dir=tmp_path)
    again = ControlServer(reopened.ledger, reopened.sink, reopened.workspace)
    again.start()
    try:
        with urlopen(again.base_url + "/runs/kept") as response:
            body = json.loads(response.read().decode())
        assert body["fencing_token"] == created["fencing_token"]
        assert body["phase"] == "running"
    finally:
        again.stop()
        reopened.close()
