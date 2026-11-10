"""一条多步 DSH 会话：读库存、在发布前被通知打断、旧 token 被拒、新 token 再发布。

EG_PUBLISH_GATE 让 eg_write 在 reserve 之后、publish 之前停下。
控制服务在这个窗口里改库存，并按场景决定是否调用 /supersede。
不打印密钥。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.service import ControlServer
from epochgate_runtime.sink import EffectSink

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "infrastructure" / "dsh-profiles" / "research"
DSH_BIN = RESEARCH / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
PATCH = RESEARCH / "cordis.patch.yml"
DSH_HOME = ROOT / "infrastructure" / "dsh-home"
OUT_DIR = ROOT / "results" / "runtime" / "paper"
OUT = OUT_DIR / "dsh_revocation.json"

PROMPT = (
    "stock.txt contains one integer. Use only eg_read and eg_write. Do not call eg_bash. "
    "First call eg_read with path stock.txt. "
    "Then call eg_write with path order.txt and content exactly 'order ' plus that integer, nothing else. "
    "If the eg_write result has published set to false, call eg_read on stock.txt again and "
    "call eg_write on order.txt again using 'order ' plus the integer from that second read. "
    "Stop after the first eg_write whose published value is true."
)
FOLLOWUP = (
    "The previous eg_write was withheld. Call eg_read on stock.txt now. "
    "Then call eg_write on order.txt with content exactly 'order ' plus the integer you just read. "
    "Stop when published is true."
)


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
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def tool_names(frames: list[dict]) -> list[str]:
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") in {"tool_use", "tool-call", "tool_call"} and isinstance(node.get("name"), str):
                found.append(node["name"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for frame in frames:
        walk(frame)
    return found


def run_case(name: str, *, aligned: bool, key: str) -> dict:
    workspace = OUT_DIR / "dsh-revocation" / name
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    (workspace / "stock.txt").write_text("3\n", encoding="utf-8")
    gate = OUT_DIR / "dsh-revocation" / f"{name}.gate"
    gate.with_name(gate.name + ".ready").unlink(missing_ok=True)
    gate.with_name(gate.name + ".release").unlink(missing_ok=True)
    ledger = RunLedger(OUT_DIR / "dsh-revocation" / f"{name}.sqlite")
    sink = EffectSink(OUT_DIR / "dsh-revocation" / f"{name}-sink.sqlite")
    server = ControlServer(ledger, sink, workspace)
    server.start()
    run_id = "dsh-" + uuid.uuid4().hex[:12]
    session_id = "eg-rev-" + uuid.uuid4().hex[:12]
    created = post(server.base_url + "/runs", {"run_id": run_id, "session_id": session_id})
    notice: dict = {"fired": False}
    error: list[str] = []

    def watch() -> None:
        ready = Path(str(gate) + ".ready")
        deadline = time.time() + 150
        while time.time() < deadline:
            if ready.is_file():
                try:
                    (workspace / "stock.txt").write_text("0\n", encoding="utf-8")
                    if aligned:
                        notice["response"] = post(server.base_url + f"/runs/{run_id}/supersede", {})
                    notice["fired"] = True
                    notice["stock_after"] = (workspace / "stock.txt").read_text(encoding="utf-8").strip()
                except Exception as exc:  # noqa: BLE001
                    error.append(type(exc).__name__)
                Path(str(gate) + ".release").write_text("go\n", encoding="utf-8")
                return
            time.sleep(0.05)
        error.append("gate_timeout")

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
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
    child_env["EG_PUBLISH_GATE"] = str(gate)
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
    request_id = 0

    def rpc(method: str, params: dict | None, timeout: float = 180) -> dict:
        nonlocal request_id
        request_id += 1
        current = request_id
        payload = {"jsonrpc": "2.0", "id": current, "method": method}
        if params is not None:
            payload["params"] = params
        assert process.stdin is not None
        process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        process.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with lock:
                for frame in frames:
                    if frame.get("id") == current:
                        return frame
            time.sleep(0.05)
        raise TimeoutError(method)

    init: dict = {}
    waited: dict = {}
    follow: dict = {}
    try:
        init = rpc(
            "initialize",
            {"cwd": str(workspace), "provider": "deepseek-official", "model": "deepseek-flash", "maxTokens": 1024},
        )
        rpc("session/prompt", {"sessionId": session_id, "contentBlocks": [{"type": "text", "text": PROMPT}]}, 4)
        waited = rpc("session/wait", {"sessionId": session_id}, timeout=240)
        order = workspace / "order.txt"
        if aligned and (not order.is_file()):
            rpc("session/prompt", {"sessionId": session_id, "contentBlocks": [{"type": "text", "text": FOLLOWUP}]})
            follow = rpc("session/wait", {"sessionId": session_id}, timeout=240)
        rpc("shutdown", {})
    except Exception as exc:  # noqa: BLE001
        error.append(type(exc).__name__ + ":" + str(exc)[:120])
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=30)
        watcher.join(timeout=5)
        server.stop()
    order = workspace / "order.txt"
    effects = ledger.list_effects(run_id) if hasattr(ledger, "list_effects") else []
    if not effects:
        rows = ledger._conn.execute(
            "SELECT idempotency_key, phase, effect_state, receipt, fencing_token FROM effects WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        effects = [
            {
                "idempotency_key": row[0][:12],
                "phase": row[1],
                "effect_state": row[2],
                "receipt": row[3],
                "fencing_token": row[4],
            }
            for row in rows
        ]
    events = [item["event_type"] for item in ledger.events(run_id)]
    run = ledger.get_run(run_id)
    report = {
        "name": name,
        "aligned": aligned,
        "run_id": run_id,
        "session_id": session_id,
        "granted_token": created.get("fencing_token"),
        "final_token": run.fencing_token,
        "final_state_version": run.state_version,
        "eligible": run.eligible,
        "notice": notice,
        "initialize_error": init.get("error"),
        "wait_error": waited.get("error"),
        "followup_sent": bool(follow),
        "followup_error": follow.get("error"),
        "tool_names": tool_names(frames),
        "event_types": events,
        "effects": effects,
        "stock_final": (workspace / "stock.txt").read_text(encoding="utf-8").strip(),
        "order_text": order.read_text(encoding="utf-8") if order.is_file() else None,
        "errors": error,
        "key_printed": False,
    }
    frame_path = OUT_DIR / "dsh-revocation" / f"{name}-frames.jsonl"
    frame_path.write_text(
        redact("".join(json.dumps(frame, ensure_ascii=False) + "\n" for frame in frames), key),
        encoding="utf-8",
    )
    (OUT_DIR / "dsh-revocation" / f"{name}-stderr.txt").write_text("".join(stderr), encoding="utf-8")
    ledger.close()
    sink.close()
    return report


def main() -> None:
    load_env()
    key = os.environ.get("DS_API_KEY") or os.environ.get("EPOCHGATE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key:
        OUT.write_text(json.dumps({"status": "missing_key"}) + "\n", encoding="utf-8")
        raise SystemExit(1)
    cases = [
        run_case("no-notice", aligned=False, key=key),
        run_case("aligned", aligned=True, key=key),
    ]
    text = redact(json.dumps({"model": "deepseek-flash", "cases": cases}, ensure_ascii=False, indent=2) + "\n", key)
    OUT.write_text(text, encoding="utf-8")
    print(json.dumps({
        "out": str(OUT),
        "cases": [
            {
                "name": item["name"],
                "tools": item["tool_names"],
                "events": item["event_types"],
                "order": item["order_text"],
                "notice": item["notice"].get("fired"),
                "errors": item["errors"],
                "final_token": item["final_token"],
            }
            for item in cases
        ],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
