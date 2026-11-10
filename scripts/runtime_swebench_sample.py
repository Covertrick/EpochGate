"""官方 SWE-bench 有界尝试。没有 harness 或 gold 节点时不把它叫做官方结果。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from epochgate_runtime.f2p import recorded_12907, score_local_discriminator
from epochgate_runtime.measurements import _container_ready
from epochgate_runtime.sandbox import start_container, stop_container, value_passes, write_file

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "swebench_attempt.json"
CHECKOUT = ROOT / "datasets" / "cache" / "m0-repos" / "astropy__astropy" / "d16bfe05a744"


def _status(url: str) -> str:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "epochgate-measure"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return f"http_{response.status}"
    except urllib.error.HTTPError as exc:
        return f"http_{exc.code}"
    except Exception as exc:  # noqa: BLE001 - record the failure class only
        return type(exc).__name__


def _closest_container() -> dict:
    if not _container_ready():
        return {"docker": False, "error": "WSL Docker engine is not available"}
    name = "eg-rt-closest-task"
    buggy = "def value():\n    return 0\n"
    gold = "def value():\n    return 1\n"
    try:
        start_container(name)
        write_file(name, "bug.py", buggy)
        base_failed = not value_passes(name)
        write_file(name, "bug.py", gold)
        gold_passed = value_passes(name)
        write_file(name, "bug.py", buggy)
        write_file(name, "probe.txt", "hello\n")
        probe_passed = value_passes(name)
        return {
            "docker": True,
            "image": "python:3.10-slim",
            "network": "none",
            "official_swebench": False,
            "assertion": "bug.value() == 1",
            "base_fail_to_pass_failed": base_failed,
            "gold_fail_to_pass_passed": gold_passed,
            "probe_marked_repaired": probe_passed,
            "discriminating": base_failed and gold_passed and not probe_passed,
        }
    except (OSError, RuntimeError, TimeoutError) as exc:
        return {"docker": True, "official_swebench": False, "error": str(exc)[-400:]}
    finally:
        try:
            stop_container(name)
        except (OSError, RuntimeError, TimeoutError):
            pass


def main() -> None:
    fetches = {
        "huggingface_search": _status(
            "https://datasets-server.huggingface.co/search?dataset=princeton-nlp/SWE-bench_Verified&config=default&split=test&query=astropy__astropy-12907&offset=0&length=1"
        ),
        "huggingface_rows": _status(
            "https://datasets-server.huggingface.co/rows?dataset=princeton-nlp/SWE-bench_Verified&config=default&split=test&offset=0&length=1"
        ),
    }
    local_dir = ROOT / "results" / "runtime" / "swebench_closest_local"
    local = score_local_discriminator(local_dir)
    local["official_swebench"] = False
    local["assertion"] = "bug.value() == 1"
    report = {
        "official_swebench": False,
        "official_harness_installed": False,
        "gold_fail_to_pass_nodes_present": False,
        "sample_size": 0,
        "instance_attempted": "astropy__astropy-12907",
        "local_checkout_present": CHECKOUT.is_dir(),
        "dataset_fetch": fetches,
        "reason": "The swebench package is not installed, the official FAIL_TO_PASS node list was not retrieved, and this repository has no gold patch file. The runs below are the closest local task, not official SWE-bench.",
        "prior_12907_recorded": recorded_12907(),
        "prior_12907_note": "Earlier whole-file pytest of test_separable.py was 11 passed. That record is not a new official node run.",
        "closest_real_task_host": local,
        "closest_real_task_container": _closest_container(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    container = report["closest_real_task_container"]
    print(
        "official_swebench=false fetch="
        + str(fetches)
        + " host_discriminating="
        + str(local.get("discriminating"))
        + " container_discriminating="
        + str(container.get("discriminating"))
    )


if __name__ == "__main__":
    main()
