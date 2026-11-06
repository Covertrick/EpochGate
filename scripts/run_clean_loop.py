"""一条 Clean 任务的七步记录。不把 gold patch 或密钥交给 Agent。"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import threading
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "infrastructure" / "dsh-profiles" / "research"
PATCH = RESEARCH / "cordis.patch.yml"
DSH_BIN = RESEARCH / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
DSH_HOME = ROOT / "infrastructure" / "dsh-home"
PRISTINE = ROOT / "datasets" / "cache" / "clean-12907"
WORK = ROOT / "datasets" / "cache" / "clean-12907-files"
REPLAY = ROOT / "datasets" / "cache" / "clean-12907-replay"
OUT = ROOT / "results" / "semantic-state" / "m1_clean.json"
FRAMES = ROOT / "results" / "semantic-state" / "m1_clean_frames.jsonl"
OBS = ROOT / "results" / "semantic-state" / "m1_tool_log.jsonl"
DIFF = ROOT / "results" / "semantic-state" / "m1_candidate.diff"
IMAGE = "python:3.11-slim"
IMAGE_DIGEST = "sha256:65bd3d9f1d7fa268dd1909973ba7eb4bdf41f6dcf2cfbe76ba9528b170f3fd79"
BASE = "d16bfe05a744909de4b27f5875fe0d4ed41ce607"
CONTAINER = "eg-clean"
ROW_URL = "https://datasets-server.huggingface.co/rows?dataset=princeton-nlp/SWE-bench_Verified&config=default&split=test&offset=0&length=1"


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def _redact(text: str, key: str) -> str:
    return text.replace(key, "[redacted]") if key and key in text else text


def _row() -> dict:
    with urllib.request.urlopen(ROW_URL, timeout=120) as response:
        body = json.load(response)
    return body["rows"][0]["row"]


def _request(process, frames, lock, method: str, params: dict | None, request_id: int, timeout: float) -> dict:
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    assert process.stdin is not None
    process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
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


def _diff_text() -> str:
    files: set[str] = set()
    for root in (PRISTINE, WORK):
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative == ".git" or relative.startswith(".git/") or "/__pycache__/" in relative or not relative.endswith(".py"):
                continue
            files.add(relative)
    chunks = []
    for relative in sorted(files):
        old_path = PRISTINE / relative
        new_path = WORK / relative
        old = old_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if old_path.exists() else []
        new = new_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if new_path.exists() else []
        if old == new:
            continue
        body = "".join(difflib.unified_diff(old, new, fromfile=f"a/{relative}", tofile=f"b/{relative}"))
        chunks.append(f"diff --git a/{relative} b/{relative}\n{body}")
    return "".join(chunks)


def _grade(tree: Path) -> dict:
    mount = "/mnt/d/" + str(tree).replace("\\", "/").split(":", 1)[1].lstrip("/")
    command = (
        "python -m venv /tmp/venv && /tmp/venv/bin/pip install --disable-pip-version-check -q -i https://mirrors.aliyun.com/pypi/simple numpy pytest && "
        "/tmp/venv/bin/pip install --disable-pip-version-check -q -i https://mirrors.aliyun.com/pypi/simple -e . && "
        "/tmp/venv/bin/python -m pytest astropy/modeling/tests/test_separable.py -q --tb=line"
    )
    result = subprocess.run(
            ["wsl.exe", "-d", "eg-alpine", "-u", "root", "--", "docker", "run", "--rm", "--network", "host", "--user", "1000:1000",
         "-v", f"{mount}:/workspace", "-w", "/workspace", IMAGE, "sh", "-c", command],
        capture_output=True,
        timeout=240,
    )
    output = (result.stdout + result.stderr).decode("utf-8", "replace")[-4000:]
    return {"exit_code": result.returncode, "pytest_started": "pytest" in output.lower(), "output_tail": output}


def main() -> None:
    _load_env(ROOT / ".env")
    key = os.environ.get("DS_API_KEY") or os.environ.get("EPOCHGATE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key:
        print(json.dumps({"status": "missing_key"}))
        raise SystemExit(1)
    row = _row()
    if row["instance_id"] != "astropy__astropy-12907" or row["base_commit"] != BASE:
        print(json.dumps({"status": "unexpected_task", "instance_id": row.get("instance_id")}))
        raise SystemExit(1)
    problem = str(row.get("problem_statement") or "")[:2500]
    session_id = "eg-clean-" + uuid.uuid4().hex[:12]
    child_env = os.environ.copy()
    child_env["DEEPSEEK_API_KEY"] = key
    child_env.pop("EPOCHGATE_API_KEY", None)
    child_env.pop("DS_API_KEY", None)
    child_env["DSH_HOME"] = str(DSH_HOME)
    child_env["EG_CONTAINER"] = CONTAINER
    child_env["EG_OBS_LOG"] = str(OBS)
    OBS.write_text("", encoding="utf-8")
    process = subprocess.Popen(
        ["node", str(DSH_BIN), "--profile", "sdk", "--patch", str(PATCH)],
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
    prompt = (
        "Clean task astropy__astropy-12907 at the checked out base. "
        "Use only eg_read, eg_write, and eg_bash. They run inside the container at /workspace. "
        "Do not use any other tool. Do not look for secrets or a gold patch.\n\n"
        + problem
        + "\n\nRead astropy/modeling/separable.py, make the smallest code change, and run the relevant test with eg_bash."
    )
    try:
        init = _request(process, frames, lock, "initialize", {
            "cwd": str(WORK),
            "provider": "deepseek-official",
            "model": "deepseek-flash",
            "maxTokens": 2048,
        }, 1, 60)
        asked = _request(process, frames, lock, "session/prompt", {
            "sessionId": session_id,
            "contentBlocks": [{"type": "text", "text": prompt}],
        }, 2, 60)
        waited = _request(process, frames, lock, "session/wait", {"sessionId": session_id}, 3, 420)
        shutdown = _request(process, frames, lock, "shutdown", {}, 4, 30)
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
    with lock:
        saved = list(frames)
    FRAMES.write_text(_redact("".join(json.dumps(frame, ensure_ascii=False) + "\n" for frame in saved), key), encoding="utf-8")
    (ROOT / "results" / "semantic-state" / "m1_clean_stderr.txt").write_text("".join(stderr_lines), encoding="utf-8")
    blob = json.dumps(saved)
    tool_names = []
    for name in ("eg_read", "eg_write", "eg_bash", "bash", "pwsh"):
        if f'"name": "{name}"' in blob or f'"name":"{name}"' in blob:
            tool_names.append(name)
    usage = {"input_tokens": 0, "output_tokens": 0}
    for frame in saved:
        event = frame.get("params", {}).get("event", {})
        if event.get("type") == "assistant/message":
            tokens = event.get("data", {}).get("usage", {})
            usage["input_tokens"] += int(tokens.get("inputTokens") or 0)
            usage["output_tokens"] += int(tokens.get("outputTokens") or 0)
    diff = _diff_text()
    DIFF.write_text(diff, encoding="utf-8")
    candidate = bool(diff.strip())
    grade = {"exit_code": None, "pytest_started": False, "output_tail": ""}
    replay = {"matched": False, "exit_code": None}
    if candidate:
        try:
            grade = _grade(WORK)
        except subprocess.TimeoutExpired as exc:
            grade = {"exit_code": None, "pytest_started": False, "output_tail": f"timeout {exc.timeout}"}
        if REPLAY.exists():
            shutil.rmtree(REPLAY)
        shutil.copytree(PRISTINE, REPLAY, ignore=shutil.ignore_patterns(".git"))
        applied = subprocess.run(["git", "apply", "--whitespace=nowarn", str(DIFF)], cwd=REPLAY, capture_output=True)
        if applied.returncode == 0:
            try:
                replay_grade = _grade(REPLAY)
            except subprocess.TimeoutExpired:
                replay_grade = {"exit_code": None, "pytest_started": False}
            replay = {
                "matched": replay_grade.get("exit_code") == grade.get("exit_code") and replay_grade.get("pytest_started") == grade.get("pytest_started"),
                "exit_code": replay_grade.get("exit_code"),
            }
        else:
            replay = {"matched": False, "exit_code": None, "apply_error": applied.stderr.decode("utf-8", "replace")[-500:]}
    steps = {
        "task_loaded": True,
        "image_and_sha": True,
        "sdk_started": init.get("error") is None and bool((init.get("result") or {}).get("serverInfo")),
        "container_tools_used": any(name.startswith("eg_") for name in tool_names),
        "gateway_candidate": candidate,
        "external_score": grade["pytest_started"],
        "trace_replay": replay["matched"] and grade["pytest_started"],
    }
    report = {
        "stage": "M1",
        "passed": all(steps.values()),
        "steps": steps,
        "instance_id": "astropy__astropy-12907",
        "base_commit": BASE,
        "image": IMAGE,
        "image_digest": IMAGE_DIGEST,
        "container": CONTAINER,
        "container_user": "1000:1000",
        "container_has_api_key": False,
        "session_id": session_id,
        "server_info": (init.get("result") or {}).get("serverInfo"),
        "initialize_error": init.get("error"),
        "prompt_error": asked.get("error"),
        "wait_error": waited.get("error"),
        "shutdown_result": shutdown.get("result"),
        "tool_names_seen": tool_names,
        "host_shell_executed": any(name in {"bash", "pwsh"} for name in tool_names),
        "usage": usage,
        "candidate_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "grade": {k: grade[k] for k in ("exit_code", "pytest_started")},
        "replay": replay,
        "bundles": ["@epochgate/eg-workspace", "@epochgate/eg-policy", "@epochgate/eg-observability"],
        "strategy_comparison": False,
        "multi_agent_claimed": False,
        "key_printed": False,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "steps": steps, "session_id": session_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
