"""启动一次 dsh --profile sdk 会话。不打印密钥，不把密钥写进结果。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "infrastructure" / "dsh-profiles" / "research"
PATCH = RESEARCH / "cordis.patch.yml"
DSH_BIN = RESEARCH / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
DSH_HOME = ROOT / "infrastructure" / "dsh-home"
EMPTY_CWD = ROOT / "datasets" / "cache" / "sdk-empty-cwd"
OUT = ROOT / "results" / "semantic-state" / "sdk_session.json"
FRAMES = ROOT / "results" / "semantic-state" / "sdk_session_frames.jsonl"
STDERR_OUT = ROOT / "results" / "semantic-state" / "sdk_session_stderr.txt"


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def _redact(text: str, key: str) -> str:
    if key and key in text:
        return text.replace(key, "[redacted]")
    return text


def main() -> None:
    _load_env(ROOT / ".env")
    key = os.environ.get("DS_API_KEY") or os.environ.get("EPOCHGATE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key:
        print(json.dumps({"status": "missing_key"}))
        raise SystemExit(1)
    EMPTY_CWD.mkdir(parents=True, exist_ok=True)
    DSH_HOME.mkdir(parents=True, exist_ok=True)
    session_id = "eg-sdk-" + uuid.uuid4().hex[:12]
    child_env = os.environ.copy()
    child_env["DEEPSEEK_API_KEY"] = key
    child_env.pop("EPOCHGATE_API_KEY", None)
    child_env.pop("DS_API_KEY", None)
    child_env["DSH_HOME"] = str(DSH_HOME)
    process = subprocess.Popen(
        [
            "node",
            str(DSH_BIN),
            "--profile",
            "sdk",
            "--patch",
            str(PATCH),
        ],
        cwd=str(RESEARCH),
        env=child_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    frames: list[dict] = []
    stderr_lines: list[str] = []
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
                frame = {"unparsed": _redact(line, key)}
            with lock:
                frames.append(frame)

    def read_stderr() -> None:
        assert process.stderr is not None
        for raw in process.stderr:
            stderr_lines.append(_redact(raw.decode("utf-8", errors="replace"), key))

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    def request(method: str, params: dict | None, request_id: int) -> dict:
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
        init = request(
            "initialize",
            {
                "cwd": str(EMPTY_CWD),
                "provider": "deepseek-official",
                "model": "deepseek-flash",
                "maxTokens": 64,
            },
            1,
        )
        prompt = request(
            "session/prompt",
            {
                "sessionId": session_id,
                "contentBlocks": [
                    {
                        "type": "text",
                        "text": "Reply with exactly the word pong. Do not call tools.",
                    }
                ],
            },
            2,
        )
        waited = request("session/wait", {"sessionId": session_id}, 3)
        shutdown = request("shutdown", {}, 4)
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=30)

    models: list[str] = []
    tool_names: list[str] = []
    offered_tools: list[str] = []
    assistant_parts: list[str] = []

    def walk(node: object, parent_type: str | None = None) -> None:
        if isinstance(node, dict):
            event_type = node.get("type") if isinstance(node.get("type"), str) else parent_type
            if event_type == "assistant/message":
                message = node.get("data", {}).get("message", {}) if isinstance(node.get("data"), dict) else {}
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                            assistant_parts.append(block["text"])
            if isinstance(node.get("model"), str) and parent_type in {None, "request/context", "assistant/message"}:
                models.append(node["model"])
            if node.get("type") == "request/header":
                header_tools = node.get("data", {}).get("header", {}).get("tools", [])
                if isinstance(header_tools, list):
                    offered_tools.extend(item.get("name") for item in header_tools if isinstance(item, dict) and isinstance(item.get("name"), str))
            if node.get("type") in {"tool_use", "tool-call", "tool_call"} and isinstance(node.get("name"), str):
                tool_names.append(node["name"])
            for value in node.values():
                walk(value, event_type if isinstance(event_type, str) else parent_type)
        elif isinstance(node, list):
            for item in node:
                walk(item, parent_type)

    with lock:
        saved = list(frames)
    for frame in saved:
        walk(frame)
    assistant = "\n".join(assistant_parts)
    raw_hash = hashlib.sha256(assistant.encode("utf-8")).hexdigest()
    host_shells = [name for name in tool_names if name.lower() in {"bash", "pwsh", "powershell", "shell"}]
    report = {
        "status": "sdk_session_ok" if assistant.strip() and not host_shells and "error" not in init else "sdk_session_incomplete",
        "session_id": session_id,
        "server_info": (init.get("result") or {}).get("serverInfo"),
        "initialize_error": init.get("error"),
        "prompt_result": prompt.get("result"),
        "prompt_error": prompt.get("error"),
        "wait_error": waited.get("error"),
        "shutdown_result": shutdown.get("result"),
        "reported_models": sorted(set(models)),
        "assistant_text_sha256": raw_hash,
        "assistant_chars": len(assistant),
        "offered_tools": offered_tools,
        "host_shell_offered": any(name in {"bash", "pwsh", "powershell"} for name in offered_tools),
        "host_fs_offered": any(name in {"read", "write", "edit", "glob", "grep"} for name in offered_tools),
        "tool_names": tool_names,
        "host_shell_executed": bool(host_shells),
        "session_log_enabled": False,
        "key_printed": False,
        "frames": str(FRAMES.relative_to(ROOT)).replace("\\", "/"),
    }
    frame_text = _redact(
        "".join(json.dumps(frame, ensure_ascii=False) + "\n" for frame in saved),
        key,
    )
    FRAMES.write_text(frame_text, encoding="utf-8")
    report["key_redacted_from_frames"] = key in "".join(json.dumps(frame, ensure_ascii=False) for frame in saved)
    STDERR_OUT.write_text("".join(stderr_lines), encoding="utf-8")
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "session_id", "reported_models", "assistant_text_sha256", "host_shell_executed", "host_shell_offered")}, ensure_ascii=False))
    if report["status"] != "sdk_session_ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
