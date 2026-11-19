"""进程在四个发布点被杀死后的恢复。掉电不在这里测。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from epochgate_runtime.files import publish_frozen_file, recover_frozen_publish
from epochgate_runtime.ledger import RunLedger


def _child(root: Path) -> None:
    workspace = root / "ws"
    workspace.mkdir()
    staging = workspace / "stage.bin"
    staging.write_bytes(b"gold")
    ledger = RunLedger(root / "ledger.sqlite")
    ledger.create_run("run")
    ledger.advance("run", "running")
    token = ledger.get_run("run").fencing_token
    publish_frozen_file(
        ledger,
        workspace=workspace,
        run_id="run",
        idempotency_key="k",
        fencing_token=token,
        relative_path="order.txt",
        staging_path=staging,
    )
    ledger.close()


def test_process_kill_at_four_publish_points(tmp_path: Path) -> None:
    expected = {
        "after_private_create": "unknown",
        "after_private_write": "completed",
        "after_replace": "completed",
        "after_receipt": "replayed",
    }
    for point, status in expected.items():
        root = tmp_path / point
        root.mkdir()
        env = os.environ.copy()
        env["EPOCHGATE_PAUSE_AT"] = point
        process = subprocess.Popen([sys.executable, __file__, str(root)], env=env)
        time.sleep(0.6)
        process.kill()
        process.wait(timeout=10)
        ledger = RunLedger(root / "ledger.sqlite")
        recovered = recover_frozen_publish(
            ledger,
            workspace=root / "ws",
            run_id="run",
            idempotency_key="k",
            relative_path="order.txt",
        )
        assert recovered["status"] == status, point
        if status == "completed":
            assert (root / "ws" / "order.txt").read_bytes() == b"gold"
        if status == "unknown":
            assert not (root / "ws" / "order.txt").exists()
        ledger.close()


if __name__ == "__main__":
    _child(Path(sys.argv[1]))
