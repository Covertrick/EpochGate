"""写出故障矩阵、读后替换和过期资格。不调用模型。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from epochgate_runtime.measurements import (
    measure_container_read_race,
    measure_host_read_race,
    measure_stale_world,
    run_fault_matrix,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime"


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    fault_dir = OUT / "fault_matrix"
    if fault_dir.exists():
        shutil.rmtree(fault_dir)
    fault = run_fault_matrix(fault_dir)
    _write(OUT / "fault_matrix.json", fault)
    print(f"fault_matrix passed={fault['passed']} rows={len(fault['rows'])}")

    host = measure_host_read_race(OUT / "read_race_host")
    container = measure_container_read_race()
    read_race = {"host_publish_file": host, "container_publisher": container}
    _write(OUT / "read_race.json", read_race)
    print(
        "read_race host="
        + ",".join(f"{item['mutation']}:{item['status']}" for item in host["cases"])
        + " container_docker="
        + str(container.get("docker"))
    )

    stale = measure_stale_world(OUT / "stale_world")
    _write(OUT / "stale_world.json", stale)
    advanced = stale["with_epoch_advance"]
    print(
        f"stale expired={advanced['expired_status']} valid_published={advanced['valid_published']} bytes={advanced['bytes']!r}"
    )


if __name__ == "__main__":
    main()
