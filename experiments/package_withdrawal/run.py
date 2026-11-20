"""Probe Twine retry behavior against a minimal loopback upload state machine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from zipfile import ZipFile, ZipInfo

CONDITIONS = ("precommit", "postcommit", "yanked", "deleted")


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def wheel(directory):
    path = directory / "epochgate_fixture-1.0.0-py3-none-any.whl"
    with ZipFile(path, "x") as archive:
        archive.writestr(ZipInfo("epochgate_fixture-1.0.0.dist-info/METADATA"),
                         "Metadata-Version: 2.1\nName: epochgate-fixture\nVersion: 1.0.0\n")
        archive.writestr(ZipInfo("epochgate_fixture-1.0.0.dist-info/WHEEL"),
                         "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(ZipInfo("epochgate_fixture-1.0.0.dist-info/RECORD"), "")
    return path


def trial(condition, package, directory, python):
    requests = []
    state, writes = "absent", 0
    expected = hashlib.sha256(package.read_bytes()).hexdigest()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            nonlocal state, writes
            body = self.rfile.read(int(self.headers["Content-Length"]))
            header = f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode()
            parts = BytesParser(policy=policy.default).parsebytes(header + body)
            file = next((p for p in parts.iter_parts() if p.get_param(
                "name", header="content-disposition") == "content"), None)
            digest = hashlib.sha256(file.get_payload(decode=True)).hexdigest() if file else None
            before = state
            if self.path != "/legacy/" or digest != expected:
                code, reason = 400, "Wrong synthetic fixture"
            elif not requests:
                if condition != "precommit":
                    state, writes = "present", writes + 1
                    if condition in {"yanked", "deleted"}:
                        state = condition
                code, reason = 503, "Injected uncertainty"
            elif state == "deleted":
                code, reason = 400, "This filename was previously used by a file that has since been deleted"
            else:
                if state == "absent":
                    state, writes = "present", writes + 1
                code, reason = 200, "OK"
            requests.append({"sha256": digest, "state_before": before, "state_after": state, "http": code})
            self.send_response(code, reason)
            self.send_header("Content-Length", "0")
            self.end_headers()

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = directory / "empty-pypirc"
        config.write_text("", encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("TWINE_", "PIP_", "UV_", "ACTIONS_ID_TOKEN_"))
               and not k.upper().endswith("_PROXY")}
        env.update({"NO_PROXY": "127.0.0.1", "HTTP_PROXY": "http://127.0.0.1:1",
                    "HTTPS_PROXY": "http://127.0.0.1:1", "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring"})
        try:
            command = [str(python), "-I", "-m", "twine", "upload",
                       "--non-interactive", "--disable-progress-bar", "--config-file", str(config),
                       "--repository-url", f"http://127.0.0.1:{server.server_port}/legacy/",
                       "-u", "fixture", "-p", "fixture", str(package)]
            result = subprocess.run(command, cwd=directory, env=env, capture_output=True, timeout=30)
        finally:
            server.shutdown()
            thread.join()
    row = {"condition": condition, "requests": requests, "writes": writes, "final_state": state,
           "exit": result.returncode}
    (directory / "stdout.txt").write_bytes(result.stdout)
    (directory / "stderr.txt").write_bytes(result.stderr)
    save(directory / "result.json", row)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--client-python", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    python = args.client_python.resolve()
    version = subprocess.check_output(
        [str(python), "-I", "-c", "import importlib.metadata as m; print(m.version('twine'))"],
        timeout=15, text=True,
    ).strip()
    if version != "7.0.0":
        parser.error(f"Expected Twine 7.0.0, found {version}")
    output.mkdir(parents=True, exist_ok=False)
    package = wheel(output)
    rows = []
    for condition in CONDITIONS:
        directory = output / condition
        directory.mkdir()
        rows.append(trial(condition, package, directory, python))
    expected = {
        "precommit": ("present", 200, True), "postcommit": ("present", 200, True),
        "yanked": ("yanked", 200, True), "deleted": ("deleted", 400, False),
    }
    for row in rows:
        states = (row["final_state"], row["requests"][-1]["http"], row["exit"] == 0) if row["requests"] else None
        if (states != expected[row["condition"]] or len(row["requests"]) != 2 or row["writes"] != 1
                or row["requests"][0]["http"] != 503):
            raise RuntimeError(f"Fixture did not produce expected retry: {row}")
    save(output / "report.json", {"client": "twine==7.0.0", "cases": rows,
                                 "verdict": "NO_WITHDRAWAL_OVERRIDE_IN_MODELED_CONTRACT"})
    print("4/4 loopback controls passed; no withdrawal overridden")


if __name__ == "__main__":
    main()
