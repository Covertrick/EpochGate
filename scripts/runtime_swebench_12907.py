"""对 astropy__astropy-12907 跑官方 FAIL_TO_PASS 节点。不是 500 题排行榜。"""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime"
CHECKOUT = ROOT / "datasets" / "cache" / "m0-repos" / "astropy__astropy" / "d16bfe05a744"
PARQUET = OUT / "swebench_verified_test.parquet"
PARQUET_URL = "https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/resolve/main/data/test-00000-of-00001.parquet"
IMAGE = "epochgate/astropy-task:m1r"
NAME = "eg-swe-12907"


def _wsl(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["wsl.exe", "-d", "eg-alpine", "-u", "root", "--", *args],
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _mnt(path: Path) -> str:
    text = str(path.resolve()).replace("\\", "/")
    drive, rest = text.split(":", 1)
    return f"/mnt/{drive.lower()}{rest}"


def _remove(path: Path) -> None:
    def onerror(func, target, _exc):
        os_chmod = getattr(os, "chmod")
        os_chmod(target, stat.S_IWRITE)
        func(target)

    import os

    if path.exists():
        shutil.rmtree(path, onexc=onerror)


def _load_row() -> dict:
    import pyarrow.parquet as pq

    if not PARQUET.is_file():
        request = urllib.request.Request(PARQUET_URL, headers={"User-Agent": "epochgate-measure"})
        with urllib.request.urlopen(request, timeout=120) as response:
            PARQUET.write_bytes(response.read())
    table = pq.read_table(PARQUET, columns=["instance_id", "base_commit", "FAIL_TO_PASS", "PASS_TO_PASS", "patch", "test_patch"])
    frame = table.to_pylist()
    PARQUET.unlink(missing_ok=True)
    matches = [row for row in frame if row.get("instance_id") == "astropy__astropy-12907"]
    if len(matches) != 1:
        raise SystemExit(f"row_count={len(matches)}")
    return matches[0]


def _apply(work: Path, patch_path: Path) -> dict:
    relative = work.resolve().relative_to(ROOT.resolve()).as_posix()
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "apply", "--whitespace=nowarn", f"--directory={relative}", str(patch_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stderr_tail": (completed.stderr or completed.stdout)[-400:],
    }


def _sync(work: Path) -> dict:
    archive = OUT / "swebench_12907_tree.tar"
    if archive.exists():
        archive.unlink()
    subprocess.run(["tar", "-cf", str(archive), "-C", str(work), "."], check=True)
    copied = _wsl(["docker", "cp", _mnt(archive), f"{NAME}:/tmp/tree.tar"], timeout=300)
    if copied.returncode != 0:
        return {"ok": False, "error": copied.stderr.decode("utf-8", "replace")[-400:]}
    extracted = _wsl(
        [
            "docker", "exec", "-u", "root", NAME, "sh", "-c",
            "rm -rf /workspace && mkdir -p /workspace && tar -xf /tmp/tree.tar -C /workspace && chown -R 1000:1000 /workspace",
        ],
        timeout=300,
    )
    archive.unlink(missing_ok=True)
    if extracted.returncode != 0:
        return {"ok": False, "error": extracted.stderr.decode("utf-8", "replace")[-400:]}
    return {"ok": True}


def _pytest(nodes: list[str]) -> dict:
    import base64

    encoded = base64.b64encode(json.dumps(nodes).encode("utf-8")).decode("ascii")
    code = (
        "import base64,json,pytest,sys;"
        "nodes=json.loads(base64.b64decode(sys.argv[1]));"
        "raise SystemExit(pytest.main(nodes+['-q','--tb=line','-o','addopts=','-p','no:cacheprovider','-W','ignore::UserWarning']))"
    )
    result = _wsl(
        ["docker", "exec", "-u", "1000:1000", "-w", "/workspace", NAME, "/opt/venv/bin/python", "-c", code, encoded],
        timeout=300,
    )
    output = (result.stdout + result.stderr).decode("utf-8", "replace")
    return {"exit_code": result.returncode, "output_tail": output[-2000:]}


def main() -> None:
    row = _load_row()
    fail = json.loads(row["FAIL_TO_PASS"]) if isinstance(row["FAIL_TO_PASS"], str) else row["FAIL_TO_PASS"]
    passed = json.loads(row["PASS_TO_PASS"]) if isinstance(row["PASS_TO_PASS"], str) else row["PASS_TO_PASS"]
    gold = row.get("patch") or ""
    test_patch = row.get("test_patch") or ""
    gold_path = OUT / "swebench_12907_gold.patch"
    test_path = OUT / "swebench_12907_test.patch"
    gold_path.write_bytes(gold.encode("utf-8"))
    test_path.write_bytes(test_patch.encode("utf-8"))
    head = subprocess.run(["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    report: dict = {
        "official_harness": False,
        "official_node_list": True,
        "dataset": "princeton-nlp/SWE-bench_Verified",
        "instance_id": row["instance_id"],
        "sample_size": 1,
        "base_commit_dataset": row["base_commit"],
        "base_commit_checkout": head.stdout.strip(),
        "checkout_matches_dataset": head.stdout.strip() == row["base_commit"],
        "fail_to_pass": fail,
        "pass_to_pass_count": len(passed or []),
        "pass_to_pass_ran": False,
        "gold_patch_sha256": hashlib.sha256(gold.encode("utf-8")).hexdigest(),
        "test_patch_sha256": hashlib.sha256(test_patch.encode("utf-8")).hexdigest(),
        "agent_patch_ran": False,
        "image": IMAGE,
    }
    if not report["checkout_matches_dataset"]:
        report["status"] = "checkout_mismatch"
        (OUT / "swebench_12907.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("checkout_mismatch")
        return
    work = OUT / "swebench_12907_work"
    _remove(work)
    work.mkdir(parents=True)
    archived = subprocess.run(["git", "-C", str(CHECKOUT), "archive", "HEAD"], capture_output=True, check=False)
    if archived.returncode != 0:
        report["status"] = "archive_failed"
        report["error"] = archived.stderr.decode("utf-8", "replace")[-400:]
        (OUT / "swebench_12907.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    subprocess.run(["tar", "-xf", "-"], input=archived.stdout, cwd=work, check=True)
    report["test_patch_apply"] = _apply(work, test_path)
    if report["test_patch_apply"]["returncode"] != 0:
        report["status"] = "test_patch_failed"
        (OUT / "swebench_12907.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("test_patch_failed")
        return
    created = _wsl(["docker", "rm", "-f", NAME], timeout=30)
    started = _wsl(["docker", "run", "-d", "--name", NAME, IMAGE, "sleep", "infinity"], timeout=60)
    report["container_started"] = started.returncode == 0
    if started.returncode != 0:
        report["status"] = "container_failed"
        report["error"] = started.stderr.decode("utf-8", "replace")[-400:]
        (OUT / "swebench_12907.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    try:
        synced = _sync(work)
        report["sync_base"] = synced
        if not synced.get("ok"):
            report["status"] = "sync_failed"
            return
        install = _wsl(["docker", "exec", "-u", "1000:1000", "-w", "/workspace", NAME, "install-task"], timeout=900)
        report["install"] = {
            "exit_code": install.returncode,
            "output_tail": (install.stdout + install.stderr).decode("utf-8", "replace")[-800:],
            "network": "bridge, required by pip install -e",
        }
        if install.returncode != 0:
            report["status"] = "install_failed"
            return
        collect = _wsl(
            [
                "docker", "exec", "-u", "1000:1000", "-w", "/workspace", NAME, "/opt/venv/bin/python", "-c",
                "import pytest; raise SystemExit(pytest.main(['astropy/modeling/tests/test_separable.py::test_separable','--collect-only','-q','-o','addopts=','-p','no:cacheprovider']))",
            ],
            timeout=180,
        )
        report["collect_only_tail"] = (collect.stdout + collect.stderr).decode("utf-8", "replace")[-1500:]
        report["baseline"] = _pytest(fail)
        report["gold_patch_apply"] = _apply(work, gold_path)
        if report["gold_patch_apply"]["returncode"] != 0:
            report["status"] = "gold_patch_failed"
            return
        synced = _sync(work)
        report["sync_gold"] = synced
        if not synced.get("ok"):
            report["status"] = "sync_gold_failed"
            return
        reinstall = _wsl(["docker", "exec", "-u", "1000:1000", "-w", "/workspace", NAME, "install-task"], timeout=900)
        report["reinstall_after_gold"] = {
            "exit_code": reinstall.returncode,
            "output_tail": (reinstall.stdout + reinstall.stderr).decode("utf-8", "replace")[-400:],
        }
        if reinstall.returncode != 0:
            report["status"] = "reinstall_failed"
            return
        report["gold"] = _pytest(fail)

        def _collected(item: dict) -> bool:
            tail = item.get("output_tail") or ""
            return "no tests ran" not in tail and "not found:" not in tail and "ImportError" not in tail

        base_collected = _collected(report["baseline"])
        gold_collected = _collected(report["gold"])
        report["baseline_collected"] = base_collected
        report["gold_collected"] = gold_collected
        report["base_fail_to_pass_failed"] = (report["baseline"]["exit_code"] != 0) if base_collected else None
        report["gold_fail_to_pass_passed"] = (report["gold"]["exit_code"] == 0) if gold_collected else None
        report["protocol_discriminating"] = bool(report["base_fail_to_pass_failed"] and report["gold_fail_to_pass_passed"])
        report["status"] = "scored"
        report["node_transport"] = "base64 argv, no shell"
        report["apply_method"] = "git apply --directory=<work>"
        report["previous_attempts"] = "sh -c expanded brackets; a later git apply inside the EpochGate repo skipped the patch and still returned 0, so those runs collected only the six base parameters and did not execute compound_model6 or compound_model9."
        report["repair_demonstrated"] = False
        report["repair_note"] = "No agent patch was generated or scored. This sample checks the official nodes on the base tree and the dataset gold patch."
    finally:
        _wsl(["docker", "rm", "-f", NAME], timeout=30)
        destination = OUT / "swebench_12907.json"
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            "status="
            + str(report.get("status"))
            + " base_exit="
            + str((report.get("baseline") or {}).get("exit_code"))
            + " gold_exit="
            + str((report.get("gold") or {}).get("exit_code"))
        )


if __name__ == "__main__":
    main()
