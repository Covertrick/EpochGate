"""宿主机上的一轮 DSH 会话。结束以 session/wait 为准，不要求一定发布了文件。"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from epochgate_runtime.plugin_patch import dsh_binary, research_profile


def api_key() -> str:
    return os.environ.get("DS_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("EPOCHGATE_API_KEY") or ""


def redact(text: str) -> str:
    cleaned = text
    for name in ("DS_API_KEY", "DEEPSEEK_API_KEY", "EPOCHGATE_API_KEY", "EG_CONTROL_TOKEN"):
        value = os.environ.get(name) or ""
        if value:
            cleaned = cleaned.replace(value, "[redacted]")
    secret = api_key()
    if secret:
        cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


def run_agent_turn(
    *,
    repo: Path,
    patch: Path,
    control_url: str,
    run_id: str,
    container: str,
    task: str,
    token: str = "",
    model: str = "deepseek-flash",
    command: list[str] | None = None,
    timeout: float = 180,
    on_process: Any | None = None,
) -> dict[str, Any]:
    """跑一轮 initialize、session/prompt、session/wait。command 只在测试里替换 node。"""
    binary = dsh_binary(repo)
    argv = command or ["node", str(binary), "--profile", "sdk", "--patch", str(patch)]
    env = os.environ.copy()
    env["EG_CONTROL_URL"] = control_url
    env["EG_RUN_ID"] = run_id
    env["EG_CONTAINER"] = container
    env["EG_REQUIRE_CONTROL"] = "1"
    if token:
        env["EG_CONTROL_TOKEN"] = token
    env["DEEPSEEK_API_KEY"] = api_key()
    env.pop("EPOCHGATE_API_KEY", None)
    env.pop("DS_API_KEY", None)
    process = subprocess.Popen(
        argv,
        cwd=str(research_profile(repo)) if command is None else None,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if on_process is not None:
        on_process(process)
    frames: list[dict[str, Any]] = []
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
                frame = {"unparsed": redact(line)}
            with lock:
                frames.append(frame)

    def read_stderr() -> None:
        assert process.stderr is not None
        for raw in process.stderr:
            stderr.append(redact(raw.decode("utf-8", errors="replace")))

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()
    prompt = (
        task.strip()
        + "\n\nUse only eg_read, eg_write, and eg_bash. "
        + "Writes inside the container are published by the control plane."
    )
    try:
        init = _rpc(process, frames, lock, "initialize", {"cwd": str(repo), "provider": "deepseek-official", "model": model, "maxTokens": 2048}, 1, timeout)
        prompted = _rpc(process, frames, lock, "session/prompt", {"sessionId": run_id, "contentBlocks": [{"type": "text", "text": prompt}]}, 2, timeout)
        waited = _rpc(process, frames, lock, "session/wait", {"sessionId": run_id}, 3, timeout)
        _rpc(process, frames, lock, "shutdown", {}, 4, timeout)
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    return {
        "run_id": run_id,
        "prompt": prompt,
        "initialize_error": init.get("error"),
        "prompt_error": prompted.get("error"),
        "wait_error": waited.get("error"),
        "stderr": redact("".join(stderr)),
    }


def _rpc(process: subprocess.Popen[bytes], frames: list[dict[str, Any]], lock: threading.Lock, method: str, params: dict[str, Any] | None, request_id: int, timeout: float) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    assert process.stdin is not None
    process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    process.stdin.flush()
    deadline = threading.Event()
    timer = threading.Timer(timeout, deadline.set)
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
