"""容器发布的一次测量。只读现有函数，不改账本协议。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from epochgate_runtime.measurements import measure_container_read_race, measure_host_read_race
from epochgate_runtime.sandbox import (
    container_boundary,
    docker,
    exec_in,
    lock_workspace,
    start_container,
    stop_container,
    term_then_kill,
    value_passes,
    workspace_writable_by_tool,
    write_file,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "correctness" / "e4-audit" / "container_measurements.json"
BUGGY = "def value():\n    return 0\n"
GOLD = "def value():\n    return 1\n"


def _text(completed) -> str:
    return completed.stdout.decode("utf-8", "replace").strip()


def image_record() -> dict:
    inspected = docker(["image", "inspect", "python:3.10-slim"])
    if inspected.returncode != 0:
        return {"error": inspected.stderr.decode("utf-8", "replace")[-300:]}
    info = json.loads(inspected.stdout.decode("utf-8"))[0]
    version = docker(["version", "--format", "{{.Server.Version}} {{.Server.Os}}"])
    return {
        "image": "python:3.10-slim",
        "image_id": info.get("Id"),
        "repo_digests": info.get("RepoDigests") or [],
        "engine": _text(version),
    }


def boundary_record() -> dict:
    name = "eg-rt-probe-boundary"
    start_container(name)
    try:
        write_file(name, "bug.py", BUGGY)
        base_passes = value_passes(name)
        write_file(name, "bug.py", GOLD)
        gold_passes = value_passes(name)
        lock_workspace(name)
        facts = container_boundary(name)
        note = exec_in(name, ["python", "-c", "import pathlib; pathlib.Path('/scratch/note').write_text('ok')"])
        signals = term_then_kill(name)
        return {
            "boundary": facts,
            "tool_can_write_workspace": workspace_writable_by_tool(name),
            "tool_can_write_scratch": note.returncode == 0,
            "signals": signals,
            "base_value_passes": base_passes,
            "gold_value_passes": gold_passes,
        }
    finally:
        stop_container(name)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        host = measure_host_read_race(Path(directory))
    report = {
        "wsl_distro": "eg-alpine",
        "engine_host": "Docker Desktop, Linux containers",
        "image": image_record(),
        "boundary": boundary_record(),
        "container_read_race": measure_container_read_race(),
        "host_read_race": host,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"wrote": str(OUT), "container_cases": len(report["container_read_race"].get("cases") or [])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
