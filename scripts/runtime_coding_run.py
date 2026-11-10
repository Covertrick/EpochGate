"""真实 DSH 会话修改 Linux 容器里的 bug.py，独立容器评分。不打印密钥。"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.sandbox import lock_workspace, read_file, start_container, stop_container, value_passes, write_file
from epochgate_runtime.verdict import evaluate_coding_run
from epochgate_runtime.service import ControlServer
from epochgate_runtime.sink import EffectSink

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "infrastructure" / "dsh-profiles" / "research"
DSH_BIN = RESEARCH / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
PATCH = RESEARCH / "cordis.patch.yml"
DSH_HOME = ROOT / "infrastructure" / "dsh-home"
OUT = ROOT / "results" / "runtime" / "coding_run.json"
BUGGY = "def value():\n    return 0\n"
GOLD = "def value():\n    return 1\n"
AGENT = "eg-rt-coding-agent"
SCORER = "eg-rt-coding-scorer"


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


def post(url: str, body: dict) -> dict:
    request = Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def score_pair() -> dict[str, bool]:
    write_file(SCORER, "bug.py", BUGGY)
    base_failed = not value_passes(SCORER)
    write_file(SCORER, "bug.py", GOLD)
    gold_passed = value_passes(SCORER)
    write_file(SCORER, "bug.py", BUGGY)
    write_file(SCORER, "probe.txt", "hello\n")
    probe_passed = value_passes(SCORER)
    write_file(SCORER, "bug.py", BUGGY)
    write_file(AGENT, "bug.py", BUGGY)
    return {
        "base_fail_to_pass_failed": base_failed,
        "gold_fail_to_pass_passed": gold_passed,
        "probe_marked_repaired": probe_passed,
        "discriminating": base_failed and gold_passed and not probe_passed,
    }


def _run_view(base: str, run_id: str, ledger: RunLedger | None) -> dict:
    if ledger is not None:
        return {"events": ledger.events(run_id), "effects": ledger.effects_for_run(run_id)}
    with urlopen(base + "/runs/" + run_id) as response:
        body = json.loads(response.read().decode("utf-8"))
    return {"events": body.get("events") or [], "effects": body.get("effects") or []}


def _run_events(base: str, run_id: str, ledger: RunLedger | None) -> list:
    return _run_view(base, run_id, ledger)["events"]


def main() -> None:
    global OUT
    load_env()
    out_dir = Path(os.environ["EG_CODING_OUT"]) if os.environ.get("EG_CODING_OUT") else (ROOT / "results" / "runtime")
    out_dir.mkdir(parents=True, exist_ok=True)
    OUT = out_dir / "coding_run.json"
    key = os.environ.get("DS_API_KEY") or os.environ.get("EPOCHGATE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    external = os.environ.get("EG_USE_EXISTING_CONTROL") == "1"
    start_container(AGENT)
    start_container(SCORER)
    ledger = None
    sink = None
    server = None
    if external:
        base = os.environ.get("EG_CONTROL_URL", "")
    else:
        ledger = RunLedger(out_dir / "coding_run.sqlite")
        sink = EffectSink(out_dir / "coding_run_sink.sqlite")
        server = ControlServer(ledger, sink, out_dir)
        server.start()
        base = server.base_url
    run_id = "code-" + uuid.uuid4().hex[:12]
    session_id = "eg-code-" + uuid.uuid4().hex[:12]
    report: dict = {"run_id": run_id, "session_id": session_id, "key_printed": False}
    try:
        report["scorer"] = score_pair()
        lock_workspace(AGENT)
        if external and not base:
            report["status"] = "missing_control"
            return
        post(base + "/runs", {"run_id": run_id, "session_id": session_id})
        if not key:
            report["status"] = "missing_key"
            return
        child_env = os.environ.copy()
        child_env["DEEPSEEK_API_KEY"] = key
        child_env.pop("EPOCHGATE_API_KEY", None)
        child_env.pop("DS_API_KEY", None)
        child_env["DSH_HOME"] = str(DSH_HOME)
        child_env["EG_CONTROL_URL"] = base
        child_env["EG_RUN_ID"] = run_id
        child_env["EG_CONTAINER"] = AGENT
        child_env["EG_REQUIRE_CONTROL"] = "1"
        child_env.pop("EG_EXECUTOR", None)
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

        try:
            model_name = os.environ.get("EG_CODING_MODEL") or "deepseek-flash"
            init = rpc("initialize", {"cwd": str(ROOT), "provider": "deepseek-official", "model": model_name, "maxTokens": 2048}, 1)
            rpc("session/prompt", {"sessionId": session_id, "contentBlocks": [{"type": "text", "text": "Use eg_read on bug.py, then eg_write the whole file so value() returns 1. Do not write any other file and do not call host tools."}]}, 2)
            waited = rpc("session/wait", {"sessionId": session_id}, 3)
            report["prompt_rounds"] = 1
            if "file_published" not in [item["event_type"] for item in _run_events(base, run_id, ledger)]:
                rpc("session/prompt", {"sessionId": session_id, "contentBlocks": [{"type": "text", "text": "bug.py still returns 0. Call eg_write on bug.py with the complete file so value() returns 1. Do not call any other tool."}]}, 5)
                waited = rpc("session/wait", {"sessionId": session_id}, 6)
                report["prompt_rounds"] = 2
            rpc("shutdown", {}, 4)
            report["initialize_error"] = init.get("error")
            report["wait_error"] = waited.get("error")
        finally:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=30)
            (out_dir / "coding_run_stderr.txt").write_text("".join(stderr), encoding="utf-8")
        agent_source = read_file(AGENT, "bug.py")
        write_file(SCORER, "bug.py", agent_source)
        report["agent_fail_to_pass_passed"] = value_passes(SCORER)
        remote = _run_view(base, run_id, ledger)
        events = remote["events"]
        report["event_types"] = [item["event_type"] for item in events]
        report["tool_names"] = [item["payload"].get("tool_name", "") for item in events if item["event_type"] == "intent_recorded"]
        report["effect_states"] = [row["effect_state"] for row in remote["effects"]]
        judgement = evaluate_coding_run(
            events,
            run_id=run_id,
            expected_path="bug.py",
            baseline_failed=bool(report["scorer"]["base_fail_to_pass_failed"]),
            scorer_passed=bool(report["agent_fail_to_pass_passed"]),
        )
        report["repair_demonstrated"] = judgement["repair_demonstrated"]
        report["chain_complete"] = judgement["chain_completed"]
        report["status"] = "scored"
    finally:
        OUT.write_text(redact(json.dumps(report, ensure_ascii=False, indent=2) + "\n", key), encoding="utf-8")
        if server is not None:
            server.stop()
        if ledger is not None:
            ledger.close()
        if sink is not None:
            sink.close()
        stop_container(AGENT)
        stop_container(SCORER)
    print(json.dumps({k: report.get(k) for k in ("status", "run_id", "chain_complete", "repair_demonstrated", "agent_fail_to_pass_passed")}))
    if report.get("status") != "scored":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
