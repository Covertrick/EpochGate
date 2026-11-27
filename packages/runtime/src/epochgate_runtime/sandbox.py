"""Linux 容器里的工作区。密钥不进入容器，发布发生在账本锁内。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from epochgate_runtime.ledger import RunLedger

IMAGE = "python:3.10-slim"
PUBLISHER_UID = "1001"
_READ_STAGING = (
    "import pathlib,sys; path=pathlib.Path(sys.argv[1]); root=pathlib.Path('/scratch/.eg-staging').resolve();"
    "sys.exit(2) if path.is_symlink() else 0; resolved=path.resolve();"
    "sys.exit(3) if resolved != root and root not in resolved.parents else 0;"
    "sys.stdout.buffer.write(path.read_bytes())"
)
_WRITE_TARGET = (
    "import pathlib,sys; target=pathlib.Path(sys.argv[1]);"
    "sys.exit(4) if target.is_symlink() or not str(target).startswith('/workspace/') or '..' in target.parts else 0;"
    "target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(sys.stdin.buffer.read())"
)


_DOCKER_PREFIX: list[str] | None = None
_ENSURE_LOCK = threading.Lock()
_ENSURE_UNTIL = 0.0


def _windows_docker_exe() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    program = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates = [
        Path(local) / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe",
        Path(program) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _docker_prefixes() -> list[list[str]]:
    if os.name != "nt":
        return [["docker"]]
    prefixes: list[list[str]] = []
    exe = _windows_docker_exe()
    if exe:
        prefixes.append([exe])
    prefixes.append(["wsl.exe", "-d", "eg-alpine", "-u", "root", "--", "docker"])
    return prefixes


_LAST_DETAIL = ""


def _denied(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in ("permission denied", "access is denied", "access denied", "e_accessdenied", "拒绝访问")
    )


def _linux_engine(prefix: list[str]) -> bool:
    global _LAST_DETAIL
    try:
        probe = subprocess.run(
            [*prefix, "info", "--format", "{{.OSType}}"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _LAST_DETAIL = f"{_LAST_DETAIL}\nunavailable"
        return False
    out = probe.stdout.decode("utf-8", "replace")
    err = probe.stderr.decode("utf-8", "replace")
    _LAST_DETAIL = f"{_LAST_DETAIL}\n{err or out}"
    return probe.returncode == 0 and "linux" in out.lower()


def _desktop_running() -> bool:
    if os.name != "nt":
        return False
    try:
        probe = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Docker Desktop.exe", "/NH"],
            capture_output=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return b"Docker Desktop.exe" in probe.stdout


def _docker_prefix() -> list[str]:
    """先用已经连上的 Linux 引擎。Windows 上 Docker Desktop 的 docker.exe 不在 PATH 里时，仍去安装目录找。"""
    global _DOCKER_PREFIX
    if _DOCKER_PREFIX is not None and _linux_engine(_DOCKER_PREFIX):
        return _DOCKER_PREFIX
    for prefix in _docker_prefixes():
        if _linux_engine(prefix):
            _DOCKER_PREFIX = prefix
            return prefix
    _DOCKER_PREFIX = None
    return _docker_prefixes()[0]


def docker_command(args: list[str]) -> list[str]:
    """Windows 开发机优先用本机 Docker Desktop 的 Linux 引擎，WSL 里的 docker 仍可作为同一引擎的入口。"""
    override = os.environ.get("EPOCHGATE_DOCKER_BIN", "").strip()
    if override:
        return [override, *args]
    return [*_docker_prefix(), *args]


def docker_available() -> bool:
    global _LAST_DETAIL
    _LAST_DETAIL = ""
    _docker_prefix()
    return _DOCKER_PREFIX is not None


def docker_reason() -> str:
    """空字符串表示能用。denied 表示引擎已在，但这个进程不能再启动一次。down 表示引擎没起来。"""
    if docker_available():
        return ""
    if _denied(_LAST_DETAIL) or _desktop_running():
        return "denied"
    return "down"


def _desktop_exe() -> Path | None:
    exe = _windows_docker_exe()
    if not exe:
        return None
    desktop = Path(exe).resolve().parents[2] / "Docker Desktop.exe"
    return desktop if desktop.is_file() else None


def ensure_docker(wait_seconds: float = 45) -> bool:
    """引擎没响应时拉起 Docker Desktop，再等到 Linux 引擎可连接。"""
    global _DOCKER_PREFIX, _ENSURE_UNTIL
    if docker_available():
        return True
    if time.monotonic() < _ENSURE_UNTIL:
        return False
    with _ENSURE_LOCK:
        if docker_available():
            return True
        if time.monotonic() < _ENSURE_UNTIL:
            return False
        if docker_reason() == "denied":
            _ENSURE_UNTIL = time.monotonic() + 20
            return False
        desktop = _desktop_exe()
        if desktop is not None and not _desktop_running():
            subprocess.Popen(
                [str(desktop)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            _DOCKER_PREFIX = None
            if docker_available():
                return True
            time.sleep(2)
        _DOCKER_PREFIX = None
        ready = docker_available()
        if not ready:
            _ENSURE_UNTIL = time.monotonic() + 20
        return ready


def docker(args: list[str], data: bytes | None = None, timeout: float = 60) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        docker_command(args),
        input=data,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def start_container(name: str) -> None:
    docker(["rm", "-f", name], timeout=30)
    created = docker(
        [
            "run", "-d", "--name", name, "--network", "none",
            "--security-opt", "no-new-privileges",
            "--memory", "256m", "--pids-limit", "64",
            IMAGE, "sleep", "infinity",
        ],
        timeout=60,
    )
    if created.returncode != 0:
        raise RuntimeError(created.stderr.decode("utf-8", "replace")[-400:])
    prepared = docker(
        ["exec", "-u", "root", name, "sh", "-c", "id -u eg >/dev/null 2>&1 || useradd -u 1000 -m eg; mkdir -p /workspace; chown -R eg:eg /workspace"],
        timeout=30,
    )
    if prepared.returncode != 0:
        raise RuntimeError(prepared.stderr.decode("utf-8", "replace")[-400:])


def stop_container(name: str) -> None:
    docker(["rm", "-f", name], timeout=30)


def exec_in(name: str, args: list[str], data: bytes | None = None, timeout: float = 60) -> subprocess.CompletedProcess[bytes]:
    return docker(["exec", "-i", "-u", "1000:1000", "-w", "/workspace", name, *args], data, timeout)


def exec_root(name: str, args: list[str], data: bytes | None = None, timeout: float = 60) -> subprocess.CompletedProcess[bytes]:
    return docker(["exec", "-i", "-u", "root", "-w", "/workspace", name, *args], data, timeout)


def exec_as(name: str, uid: str, args: list[str], data: bytes | None = None, timeout: float = 60) -> subprocess.CompletedProcess[bytes]:
    return docker(["exec", "-i", "-u", f"{uid}:{uid}", "-w", "/", name, *args], data, timeout)


def lock_workspace(name: str) -> None:
    """工具用户只能写 /scratch。发布用户 1001 才能写 /workspace，不把发布动作留在 root。"""
    script = (
        "id -u eg >/dev/null 2>&1 || useradd -u 1000 -m eg; "
        "id -u publisher >/dev/null 2>&1 || useradd -u 1001 -m publisher; "
        "mkdir -p /workspace /scratch/.eg-staging && "
        "chown -R 1001:1001 /workspace && chmod 755 /workspace && "
        "chown -R 1000:1000 /scratch && chmod 755 /scratch /scratch/.eg-staging"
    )
    result = exec_root(name, ["sh", "-c", script])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-400:])


def workspace_writable_by_tool(name: str) -> bool:
    probe = exec_in(name, ["python", "-c", "import pathlib; pathlib.Path('pwned').write_text('x')"])
    return probe.returncode == 0


def term_then_kill(name: str) -> dict[str, bool]:
    """容器里的进程忽略 SIGTERM 时，接着发 SIGKILL。这不是 Windows 终止的替代证明。"""
    code = (
        "import os,signal,time,pathlib;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "pathlib.Path('/tmp/eg-pid').write_text(str(os.getpid()));"
        "time.sleep(60)"
    )
    launched = docker(["exec", "-d", "-u", "1000:1000", name, "python", "-c", code])
    if launched.returncode != 0:
        raise RuntimeError(launched.stderr.decode("utf-8", "replace")[-400:])
    pid = ""
    for _ in range(50):
        shown = exec_root(name, ["cat", "/tmp/eg-pid"])
        pid = shown.stdout.decode("utf-8", "replace").strip()
        if pid.isdigit():
            break
        time.sleep(0.05)
    if not pid.isdigit():
        raise RuntimeError("linux pid missing")
    exec_root(name, ["python", "-c", f"import os; os.kill({int(pid)}, 15)"])
    time.sleep(0.2)
    still = exec_root(name, ["python", "-c", f"import os; os.kill({int(pid)}, 0)"])
    exec_root(name, ["python", "-c", f"import os; os.kill({int(pid)}, 9)"])
    time.sleep(0.2)
    gone = exec_root(name, ["python", "-c", f"import os; os.kill({int(pid)}, 0)"])
    return {"ignored_term": still.returncode == 0, "killed": gone.returncode != 0}


def write_file(name: str, relative: str, content: str) -> None:
    if relative.startswith("/") or ".." in relative.split("/"):
        raise ValueError(relative)
    code = "import base64,pathlib,sys;pathlib.Path(sys.argv[1]).parent.mkdir(parents=True,exist_ok=True);pathlib.Path(sys.argv[1]).write_bytes(base64.b64decode(sys.argv[2]))"
    payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
    result = exec_in(name, ["python", "-c", code, relative, payload])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-500:])


def read_file(name: str, relative: str) -> str:
    result = exec_in(name, ["cat", "--", relative])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-400:])
    return result.stdout.decode("utf-8", "replace")


def value_passes(name: str) -> bool:
    code = "import shutil; shutil.rmtree('__pycache__', ignore_errors=True); import bug; raise SystemExit(0 if bug.value() == 1 else 1)"
    result = exec_in(name, ["python", "-B", "-c", code])
    return result.returncode == 0


def container_env_has_secret(name: str) -> bool:
    result = exec_in(name, ["python", "-c", "import os; keys=' '.join(os.environ); raise SystemExit(0 if ('DEEPSEEK' in keys or 'EPOCHGATE_API' in keys or 'DS_API' in keys) else 1)"])
    return result.returncode == 0


def read_staging_bytes(name: str, staging_path: str) -> bytes | None:
    result = exec_as(name, PUBLISHER_UID, ["python", "-c", _READ_STAGING, staging_path])
    if result.returncode != 0:
        return None
    return result.stdout


def write_published_bytes(name: str, relative_path: str, payload: bytes) -> bool:
    target = "/workspace/" + relative_path
    result = exec_as(name, PUBLISHER_UID, ["python", "-c", _WRITE_TARGET, target], payload)
    return result.returncode == 0


def container_boundary(name: str) -> dict[str, bool | int]:
    inspected = docker(["inspect", name])
    info = json.loads(inspected.stdout.decode("utf-8"))[0]
    host = info["HostConfig"]
    socket_probe = exec_in(
        name,
        ["python", "-c", "import pathlib; raise SystemExit(0 if pathlib.Path('/var/run/docker.sock').exists() else 1)"],
    )
    return {
        "privileged": bool(host.get("Privileged")),
        "network_none": host.get("NetworkMode") == "none",
        "mount_count": len(info.get("Mounts") or []),
        "docker_socket_visible": socket_probe.returncode == 0,
    }


def sha256_file(name: str, path: str) -> str | None:
    code = "import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())"
    result = exec_root(name, ["python", "-c", code, path])
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", "replace").strip()
    return text if len(text) == 64 else None


def publish_container(
    ledger: RunLedger,
    *,
    container: str,
    run_id: str,
    idempotency_key: str,
    fencing_token: int,
    relative_path: str,
    staging_path: str,
    after_read: Callable[[], None] | None = None,
) -> dict[str, Any]:
    staging_ok = staging_path.startswith(".eg-staging/") or staging_path.startswith("/scratch/.eg-staging/")
    if relative_path.startswith("/") or ".." in relative_path.split("/") or ".." in staging_path.split("/") or not staging_ok:
        return {"published": False, "status": "rejected", "reason": "path"}
    payload = read_staging_bytes(container, staging_path)
    if payload is None:
        existing = ledger.effect_row(idempotency_key)
        if existing is not None and existing["phase"] == "completed":
            return {"published": False, "status": "replayed", "receipt": existing["receipt"]}
        return {"published": False, "status": "rejected", "reason": "staging"}
    if after_read is not None:
        after_read()
    digest = hashlib.sha256(payload).hexdigest()
    claim = ledger.claim_publish(run_id, idempotency_key, fencing_token, digest)
    if claim["status"] == "replayed":
        return {"published": False, "status": "replayed", "receipt": claim.get("receipt"), "sha256": digest}
    if claim["status"] == "conflict":
        return {"published": False, "status": "conflict", "receipt": claim.get("receipt"), "sha256": digest}
    if claim["status"] != "claimed":
        return {"published": False, "status": "withheld", "sha256": digest}
    if not write_published_bytes(container, relative_path, payload):
        ledger.mark_effect_state(idempotency_key, "OUTCOME_UNKNOWN")
        return {"published": False, "status": "unknown", "sha256": digest}
    if sha256_file(container, "/workspace/" + relative_path) != digest:
        ledger.mark_effect_state(idempotency_key, "OUTCOME_UNKNOWN")
        return {"published": False, "status": "unknown", "sha256": digest}
    receipt = f"container:{container}:{relative_path}:{digest}"
    return ledger.complete_claimed_publish(run_id, idempotency_key, digest, receipt, path=relative_path)
