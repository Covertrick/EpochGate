"""M1-R：一条 Clean episode。结果写到 recovery/m1，不改 m1_clean.json。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import threading
import urllib.request
import uuid
from pathlib import Path

from epochgate.eg2_boundary import agent_process_env
from epochgate.eg2_events import append_event, index_event, make_event, next_seq
from epochgate.eg2_patch import classify_trees, with_apply_result

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "infrastructure" / "dsh-profiles" / "research"
PATCH = RESEARCH / "cordis.patch.yml"
DSH_BIN = RESEARCH / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
DSH_HOME = ROOT / "infrastructure" / "dsh-home"
SOURCE = ROOT / "datasets" / "cache" / "m0-repos" / "astropy__astropy" / "d16bfe05a744"
PRISTINE = ROOT / "datasets" / "cache" / "m1r-12907-pristine"
WORK = ROOT / "datasets" / "cache" / "m1r-12907-work"
OUT = ROOT / "results" / "semantic-state" / "recovery" / "m1"
IMAGE = "epochgate/astropy-task:m1r"
BASE = "d16bfe05a744909de4b27f5875fe0d4ed41ce607"
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


def _wsl(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["wsl.exe", "-d", "eg-alpine", "-u", "root", "--", *args], capture_output=True, timeout=timeout)


def _remove_tree(path: Path) -> None:
    def onerror(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onexc=onerror)


def _prepare_pristine() -> None:
    if PRISTINE.exists():
        _remove_tree(PRISTINE)
    shutil.copytree(SOURCE, PRISTINE)
    subprocess.run(["git", "checkout", "--", "."], cwd=PRISTINE, check=True, capture_output=True)
    subprocess.run(["git", "clean", "-fd", "-e", ".git"], cwd=PRISTINE, check=False, capture_output=True)


def _copy_tree(container: str) -> None:
    archive = ROOT / "datasets" / "cache" / f"{container}.tar"
    if archive.exists():
        archive.unlink()
    subprocess.run(["tar", "-cf", str(archive), "--exclude", ".git", "-C", str(PRISTINE), "."], check=True)
    copied = _wsl(["docker", "cp", _mnt(archive), f"{container}:/tmp/tree.tar"], timeout=300)
    if copied.returncode != 0:
        raise RuntimeError(copied.stderr.decode("utf-8", "replace")[-400:])
    extracted = _wsl(
        ["docker", "exec", "-u", "root", "-w", "/", container, "sh", "-c",
         "rm -rf /workspace && mkdir -p /workspace && tar -xf /tmp/tree.tar -C /workspace && chown -R 1000:1000 /workspace"],
        timeout=300,
    )
    if extracted.returncode != 0:
        raise RuntimeError(extracted.stderr.decode("utf-8", "replace")[-400:])


def _mnt(path: Path) -> str:
    text = str(path.resolve()).replace("\\", "/")
    drive, rest = text.split(":", 1)
    return f"/mnt/{drive.lower()}{rest}"


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


def _pytest(container: str) -> dict:
    command = (
        "/opt/venv/bin/python -W ignore -m pytest astropy/modeling/tests/test_separable.py "
        "-q --tb=line -o addopts= -W ignore::UserWarning"
    )
    result = _wsl(["docker", "exec", "-u", "1000:1000", "-w", "/workspace", container, "sh", "-c", command], timeout=180)
    output = (result.stdout + result.stderr).decode("utf-8", "replace")[-2000:]
    started = "passed" in output or "failed" in output or "error" in output
    return {"exit_code": result.returncode, "pytest_started": started, "output_tail": output}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _load_env(ROOT / ".env")
    key = os.environ.get("DS_API_KEY") or os.environ.get("EPOCHGATE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    inspect = _wsl(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"])
    if inspect.returncode != 0 or not key:
        decision = {
            "engineering_loop": False,
            "repair_example": False,
            "reason": "missing_image" if inspect.returncode != 0 else "missing_key",
        }
        (OUT / "m1r_gate_decision.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(decision, ensure_ascii=False))
        raise SystemExit(1)
    image_id = inspect.stdout.decode().strip()
    _prepare_pristine()
    for name in ("eg-m1r-agent", "eg-m1r-scorer"):
        _wsl(["docker", "rm", "-f", name])
        created = _wsl(["docker", "run", "-d", "--name", name, "--network", "host", IMAGE, "sleep", "infinity"])
        if created.returncode != 0:
            raise SystemExit(created.stderr.decode("utf-8", "replace")[-400:])
        _copy_tree(name)
        _wsl(["docker", "exec", "-u", "root", name, "chown", "-R", "eg:eg", "/opt/venv"])
    agent_install = _wsl(["docker", "exec", "-u", "1000:1000", "-w", "/workspace", "eg-m1r-agent", "install-task"], timeout=900)
    scorer_install = _wsl(["docker", "exec", "-u", "1000:1000", "-w", "/workspace", "eg-m1r-scorer", "install-task"], timeout=900)
    snapshot = ROOT / "datasets" / "cache" / "m1r-12907-post-install"
    if snapshot.exists():
        _remove_tree(snapshot)
    snapshot.mkdir(parents=True)
    _wsl(["docker", "cp", "eg-m1r-agent:/workspace/.", _mnt(snapshot)], timeout=300)
    (OUT / "install.json").write_text(json.dumps({
        "agent_exit": agent_install.returncode,
        "scorer_exit": scorer_install.returncode,
        "agent_tail": (agent_install.stdout + agent_install.stderr).decode("utf-8", "replace")[-1500:],
        "scorer_tail": (scorer_install.stdout + scorer_install.stderr).decode("utf-8", "replace")[-1500:],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with urllib.request.urlopen(ROW_URL, timeout=120) as response:
        row = json.load(response)["rows"][0]["row"]
    problem = str(row.get("problem_statement") or "")[:2500]
    session_id = "eg-m1r-" + uuid.uuid4().hex[:12]
    episode_id = "ep-" + session_id
    child_env = agent_process_env(os.environ.copy())
    child_env["DEEPSEEK_API_KEY"] = key
    child_env["DSH_HOME"] = str(DSH_HOME)
    child_env["EG_CONTAINER"] = "eg-m1r-agent"
    child_env["EG_EPISODE_ID"] = episode_id
    child_env["EG_SESSION_ID"] = session_id
    child_env["EG_OBS_LOG"] = str(OUT / "tool_events.jsonl")
    child_env["EG_OBS_DIR"] = str(OUT / "artifacts")
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
        "Use only eg_read, eg_write, and eg_bash inside /workspace. "
        "Do not look for secrets or a gold patch.\n\n"
        + problem
        + "\n\nRead astropy/modeling/separable.py, make the smallest source change, and run the relevant test with eg_bash."
    )
    init: dict = {}
    asked: dict = {}
    waited: dict = {}
    try:
        init = _request(process, frames, lock, "initialize", {
            "cwd": str(WORK),
            "provider": "deepseek-official",
            "model": "deepseek-flash",
            "maxTokens": 2048,
        }, 1, 90)
        asked = _request(process, frames, lock, "session/prompt", {
            "sessionId": session_id,
            "contentBlocks": [{"type": "text", "text": prompt}],
        }, 2, 60)
        waited = _request(process, frames, lock, "session/wait", {"sessionId": session_id}, 3, 420)
        _request(process, frames, lock, "shutdown", {}, 4, 30)
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
    with lock:
        saved = list(frames)
    (OUT / "frames.jsonl").write_text(
        _redact("".join(json.dumps(frame, ensure_ascii=False) + "\n" for frame in saved), key),
        encoding="utf-8",
    )
    (OUT / "stderr.txt").write_text("".join(stderr_lines), encoding="utf-8")
    if WORK.exists():
        _remove_tree(WORK)
    WORK.mkdir(parents=True)
    copied = _wsl(["docker", "cp", "eg-m1r-agent:/workspace/.", _mnt(WORK)], timeout=300)
    baseline = snapshot if snapshot.exists() else PRISTINE
    classified = classify_trees(baseline, WORK) if copied.returncode == 0 else {
        "kind": "EXPORT_ERROR",
        "files": [],
        "reason": copied.stderr.decode("utf-8", "replace")[-300:],
    }
    if classified["kind"] == "VALID_PATCH":
        applied = subprocess.run(
            ["git", "apply", "--check", "--whitespace=nowarn"],
            cwd=PRISTINE,
            input=classified["diff"].encode("utf-8"),
            capture_output=True,
        )
        classified = with_apply_result(classified, applied.returncode == 0, applied.stderr.decode("utf-8", "replace")[-300:])
        if classified["kind"] == "VALID_PATCH":
            _wsl(["docker", "exec", "-u", "1000:1000", "-w", "/workspace", "eg-m1r-scorer", "sh", "-c", "git apply --whitespace=nowarn"], timeout=60)
    (OUT / "candidate.diff").write_text(classified.get("diff") or "", encoding="utf-8")
    grade = _pytest("eg-m1r-scorer") if scorer_install.returncode == 0 else {"exit_code": scorer_install.returncode, "pytest_started": False, "output_tail": "install failed"}
    (OUT / "grade.json").write_text(json.dumps(grade, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    blob = json.dumps(saved)
    tool_names = [name for name in ("eg_read", "eg_write", "eg_bash", "bash", "pwsh") if f'"name": "{name}"' in blob or f'"name":"{name}"' in blob]
    usage = {"input_tokens": 0, "output_tokens": 0}
    reported = []
    for frame in saved:
        event = frame.get("params", {}).get("event", {})
        if event.get("type") == "assistant/message":
            tokens = event.get("data", {}).get("usage", {})
            usage["input_tokens"] += int(tokens.get("inputTokens") or 0)
            usage["output_tokens"] += int(tokens.get("outputTokens") or 0)
            model = event.get("data", {}).get("model") or event.get("data", {}).get("message", {}).get("model")
            if model:
                reported.append(model)
    jsonl = OUT / "episode.jsonl"
    db = OUT / "episode.sqlite"
    for event_type, status in (("session.started", "started"), ("session.finished", "success")):
        event = make_event(
            episode_id=episode_id,
            session_id=session_id,
            event_seq=next_seq(jsonl),
            event_type=event_type,
            status=status,
            payload={"patch_kind": classified["kind"]},
        )
        append_event(jsonl, event)
        index_event(db, event)
    engineering = bool((init.get("result") or {}).get("serverInfo")) and classified["kind"] in {"VALID_PATCH", "EMPTY_PATCH", "INVALID_PATCH"} and grade["pytest_started"]
    repair = (
        classified["kind"] == "VALID_PATCH"
        and "astropy/modeling/separable.py" in classified.get("files", [])
        and grade.get("exit_code") == 0
        and grade["pytest_started"]
    )
    manifest = {
        "episode_id": episode_id,
        "session_id": session_id,
        "instance_id": "astropy__astropy-12907",
        "base_commit": BASE,
        "provider": "deepseek-official",
        "model_route": "deepseek-flash",
        "reported_models": reported,
        "image": IMAGE,
        "image_id": image_id,
        "agent_container": "eg-m1r-agent",
        "scorer_container": "eg-m1r-scorer",
        "container_user": "1000:1000",
        "patch_kind": classified["kind"],
        "patch_files": classified.get("files", []),
        "usage": usage,
        "tool_names_seen": tool_names,
        "host_shell_executed": any(name in {"bash", "pwsh"} for name in tool_names),
        "initialize_error": init.get("error"),
        "prompt_error": asked.get("error"),
        "wait_error": waited.get("error"),
        "candidate_sha256": hashlib.sha256((classified.get("diff") or "").encode("utf-8")).hexdigest(),
        "key_printed": False,
        "server_info": (init.get("result") or {}).get("serverInfo"),
    }
    (OUT / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    decision = {
        "engineering_loop": engineering,
        "repair_example": repair,
        "patch_kind": classified["kind"],
        "pytest_started": grade["pytest_started"],
        "reason": "工程闭环看会话、patch 分类和外部评分是否都发生。真实修复样例还要求合法源码 patch 通过评分。",
    }
    (OUT / "m1r_gate_decision.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(decision, ensure_ascii=False))


if __name__ == "__main__":
    main()
