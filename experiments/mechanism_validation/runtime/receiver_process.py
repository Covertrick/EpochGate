"""Start and stop the local receiver process."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from mechanism_validation.runtime.receiver_client import ControlReceiverClient, PathReceiverClient

REPO = Path(__file__).resolve().parents[2]


class ReceiverProcess:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.db_path = self.directory / "receiver.sqlite"
        self.portfile = self.directory / "port.txt"
        self.proc: subprocess.Popen[bytes] | None = None
        self.port: int | None = None

    def start(self) -> int:
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.portfile.exists():
            self.portfile.unlink()
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mechanism_validation.runtime.receiver_server",
                "--db",
                str(self.db_path),
                "--portfile",
                str(self.portfile),
            ],
            cwd=str(REPO),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.portfile.exists():
                text = self.portfile.read_text(encoding="utf-8").strip()
                if text:
                    self.port = int(text)
                    return self.port
            if self.proc.poll() is not None:
                raise RuntimeError("receiver process exited before binding a port")
            time.sleep(0.05)
        raise RuntimeError("receiver port file was not written")

    def path_client(self) -> PathReceiverClient:
        if self.port is None:
            raise RuntimeError("receiver is not started")
        return PathReceiverClient("127.0.0.1", self.port)

    def control_client(self) -> ControlReceiverClient:
        if self.port is None:
            raise RuntimeError("receiver is not started")
        return ControlReceiverClient("127.0.0.1", self.port)

    def stop(self) -> None:
        if self.port is not None:
            ControlReceiverClient("127.0.0.1", self.port, timeout=2.0).shutdown()
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(timeout=3)
