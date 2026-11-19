"""记录这次测量使用的解释器、镜像和包版本。不读取 .env。"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "paper" / "environment-lock.json"
IMAGE = "python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d"


def run(cmd: list[str]) -> str:
    completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    return (completed.stdout or completed.stderr or "").strip()


def main() -> None:
    uv = shutil.which("uv") or "uv"
    docker = shutil.which("docker") or "docker"
    freeze = run([uv, "pip", "freeze", "--python", sys.executable])
    record = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "uv": run([uv, "--version"]),
        "image": IMAGE,
        "image_id": run([docker, "image", "inspect", IMAGE, "--format", "{{.Id}}"]),
        "pip_freeze_sha256": hashlib.sha256(freeze.encode("utf-8")).hexdigest(),
        "pip_freeze_lines": len([line for line in freeze.splitlines() if line.strip() and not line.startswith("ERROR")]),
        "note": "The freeze text is not copied here. Re-run this script in the same environment to compare the hash. API keys are not recorded.",
    }
    OUT.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: record[key] for key in ("python", "image_id", "pip_freeze_sha256")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
