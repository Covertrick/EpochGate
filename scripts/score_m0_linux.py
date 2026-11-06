"""在 Linux 容器里给已经重放的样本跑变更后测试。跑不出 pytest 就不记为可评分。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCREEN = ROOT / "results" / "semantic-state" / "m0_screen.json"
IMAGE = "python:3.10-slim"
CASE_ID = "astropy__astropy-12907"


def main() -> None:
    payload = json.loads(SCREEN.read_text(encoding="utf-8"))
    report = next(item for item in payload["reports"] if item["case_id"] == CASE_ID)
    existing = report.get("linux_post_state") or {}
    if existing.get("pytest_started") is True:
        print(json.dumps({"pytest_started": True, "kept": True, "data_gate": payload["data_gate"]}, ensure_ascii=False))
        return
    source = ROOT / "datasets" / "cache" / "m0-repos" / "astropy__astropy" / report["base_commit"][:12]
    tree = ROOT / "datasets" / "cache" / "m0-score-12907"
    if tree.exists():
        shutil.rmtree(tree)
    shutil.copytree(source, tree, ignore=shutil.ignore_patterns(".git"))
    mount = "/mnt/d/" + str(tree).replace("\\", "/").split(":", 1)[1].lstrip("/")
    command = (
        "python -m venv /tmp/venv && /tmp/venv/bin/pip install --disable-pip-version-check -q -i https://mirrors.aliyun.com/pypi/simple 'setuptools<70' extension-helpers cython numpy pytest && "
        "/tmp/venv/bin/pip install --disable-pip-version-check -q -i https://mirrors.aliyun.com/pypi/simple --no-build-isolation -e . && "
        "/tmp/venv/bin/python -m pytest astropy/modeling/tests/test_separable.py -q --tb=line"
    )
    try:
        result = subprocess.run(
            ["wsl.exe", "-d", "eg-alpine", "-u", "root", "--", "docker", "run", "--rm", "--network", "host", "--user", "1000:1000",
             "-v", f"{mount}:/workspace", "-w", "/workspace", IMAGE, "sh", "-c", command],
            capture_output=True,
            timeout=600,
        )
        output = (result.stdout + result.stderr).decode("utf-8", "replace")[-2000:]
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        output = "timeout"
        exit_code = None
    pytest_started = "pytest" in output.lower() and ("passed" in output.lower() or "failed" in output.lower() or "error" in output.lower())
    report["linux_post_state"] = {
        "image": IMAGE,
        "exit_code": exit_code,
        "pytest_started": pytest_started,
        "output_tail": output,
    }
    if pytest_started:
        checks = report["admission"]["checks"]
        checks["independently_scorable"] = True
        report["admission"]["reasons"].pop("independently_scorable", None)
        report["admission"]["admitted"] = all(checks.values())
    admitted = [item["case_id"] for item in payload["reports"] if item["admission"]["admitted"]]
    payload["admitted"] = len(admitted)
    payload["data_gate"] = "pass" if len(admitted) >= 5 else "stop"
    payload["linux_replay"] = pytest_started
    SCREEN.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pytest_started": pytest_started, "exit_code": exit_code, "data_gate": payload["data_gate"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
