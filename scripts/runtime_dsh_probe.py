"""一次真实 DSH 会话。工具调用必须先经过控制服务。不打印密钥。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

from epochgate_runtime.service import ControlServer
from epochgate_runtime.sink import EffectSink
from epochgate_runtime.ledger import RunLedger

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "infrastructure" / "dsh-profiles" / "research"
DSH_BIN = RESEARCH / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
PATCH = RESEARCH / "cordis.patch.yml"
DSH_HOME = ROOT / "infrastructure" / "dsh-home"
OUT = ROOT / "results" / "runtime" / "dsh_probe.json"


def load_env() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def redact(text: str, key: str) -> str:
    return text.replace(key, "[redacted]") if key and key in text else text


def main() -> None:
    load_env()
    key = os.environ.get("DS_API_KEY") or os.environ.get("EPOCHGATE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key:
        OUT.write_text(json.dumps({"status": "missing_key"}) + "\n", encoding="utf-8")
        raise SystemExit(1)
    workspace = ROOT / "results" / "runtime" / "dsh-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ledger = RunLedger(ROOT / "results" / "runtime" / "dsh_probe.sqlite")
    sink = EffectSink(ROOT / "results" / "runtime" / "dsh_probe_sink.sqlite")
    server = ControlServer(ledger, sink, workspace)
    server.start()
    run_id = "dsh-" + uuid.uuid4().hex[:12]
    session_id = "eg-rt-" + uuid.uuid4().hex[:12]
    from urllib.request import Request, urlopen
    request = Request(
        server.base_url + "/runs",
        data=json.dumps({"run_id": run_id, "session_id": session_id}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request) as response:
        created = json.loads(response.read().decode("utf-8"))
    child_env = os.environ.copy()
    child_env["DEEPSEEK_API_KEY"] = key
    child_env.pop("EPOCHGATE_API_KEY", None)
    child_env.pop("DS_API_KEY", None)
    child_env["DSH_HOME"] = str(DSH_HOME)
    child_env["EG_CONTROL_URL"] = server.base_url
    child_env["EG_RUN_ID"] = run_id
    child_env["EG_EXECUTOR"] = "process"
    child_env["EG_WORKSPACE"] = str(workspace)
    child_env["EG_REQUIRE_CONTROL"] = "1"
    DSH_HOME.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        ["node", str(DSH_BIN), "--profile", "sdk", "--patch", str(PATCH)],
        cwd=str(RESEARCH),
        env=child_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    frames: list[dict] = []
    stderr: list[str] = []
    lock = threading.Lock()

    def read_stdout() -> None:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                frame = {"unparsed": redact(line, key)}
            with lock:
                frames.append(frame)

    def read_stderr() -> None:
        assert process.stderr is not None
        for raw in process.stderr:
            stderr.append(redact(raw.decode("utf-8", errors="replace"), key))

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    def rpc(method: str, params: dict | None, request_id: int) -> dict:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        assert process.stdin is not None
        process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        process.stdin.flush()
        deadline = threading.Event()
        timer = threading.Timer(180.0, deadline.set)
        timer.start()
        while not deadline.is_set():
            with lock:
                for frame in frames:
                    if frame.get("id") == request_id:
                        timer.cancel()
                        return frame
            deadline.wait(0.05)
        timer.cancel()
        raise TimeoutError(method)

    init: dict = {}
    waited: dict = {}
    try:
        init = rpc("initialize", {"cwd": str(workspace), "provider": "deepseek-official", "model": "deepseek-flash", "maxTokens": 1024}, 1)
        rpc(
            "session/prompt",
            {
                "sessionId": session_id,
                "contentBlocks": [{
                    "type": "text",
                    "text": "Call eg_write exactly once. path is note.txt. content is the single word ok. Do not call any other tool.",
                }],
            },
            2,
        )
        waited = rpc("session/wait", {"sessionId": session_id}, 3)
        rpc("shutdown", {}, 4)
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=30)
        server.stop()
    note = workspace / "note.txt"
    report = {
        "status": "tool_controlled" if note.is_file() and ledger.effect_row is not None else "incomplete",
        "run_id": run_id,
        "session_id": session_id,
        "initialize_error": init.get("error"),
        "wait_error": waited.get("error") if "waited" in dir() else "not_reached",
        "note_written": note.is_file(),
        "note_sha256": hashlib.sha256(note.read_bytes()).hexdigest() if note.is_file() else None,
        "eligible": ledger.get_run(run_id).eligible,
        "event_types": [item["event_type"] for item in ledger.events(run_id)],
        "key_printed": False,
    }
    effects = ledger._conn.execute("SELECT idempotency_key, effect_state FROM effects").fetchall()
    report["effect_count"] = len(effects)
    report["effect_states"] = [row[1] for row in effects]
    report["status"] = "tool_controlled" if report["note_written"] and report["effect_count"] > 0 else "incomplete"
    text = redact(json.dumps(report, ensure_ascii=False, indent=2) + "\n", key)
    OUT.write_text(text, encoding="utf-8")
    (ROOT / "results" / "runtime" / "dsh_probe_stderr.txt").write_text("".join(stderr), encoding="utf-8")
    print(json.dumps({"status": report["status"], "run_id": run_id, "note_written": report["note_written"], "effect_count": report["effect_count"]}))
    ledger.close()
    sink.close()
    if report["status"] != "tool_controlled":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
