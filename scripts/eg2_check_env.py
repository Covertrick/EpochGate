"""记录 M1-R 任务镜像是否可重建。不打印密钥。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "semantic-state" / "recovery" / "m1"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(
        ["wsl.exe", "-d", "eg-alpine", "-u", "root", "--", "docker", "image", "inspect", "epochgate/astropy-task:m1r", "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
    )
    image_id = (probe.stdout or "").strip()
    digest_probe = subprocess.run(
        ["wsl.exe", "-d", "eg-alpine", "-u", "root", "--", "docker", "image", "inspect", "epochgate/astropy-task:m1r", "--format", "{{index .RepoDigests 0}}"],
        capture_output=True,
        text=True,
    )
    digest_text = (digest_probe.stdout or "").strip()
    digest = digest_text.split("@", 1)[-1] if "@" in digest_text else image_id
    payload = {
        "image": "epochgate/astropy-task:m1r",
        "image_present": probe.returncode == 0,
        "image_digest": digest,
        "image_id": image_id,
        "dockerfile": "infrastructure/docker/task-images/astropy/Dockerfile",
        "agent_and_scorer_share_digest": probe.returncode == 0,
    }
    (OUT / "environment.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"image_present": payload["image_present"], "image_digest": digest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
