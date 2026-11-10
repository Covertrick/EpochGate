"""解释 FAIL_TO_PASS。测试全绿本身不等于补丁修好了缺陷。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def assess_f2p(
    *,
    tests_ran: bool,
    base_fail_to_pass_failed: bool | None,
    patched_fail_to_pass_passed: bool | None,
    patch_changes_task_source: bool,
) -> dict[str, Any]:
    discriminating = base_fail_to_pass_failed is True and patched_fail_to_pass_passed is True
    repair = discriminating and patch_changes_task_source
    if not tests_ran:
        reason = "评分器没有跑完测试。"
    elif base_fail_to_pass_failed is not True:
        reason = "基线上的 FAIL_TO_PASS 没有失败，这组测试还不能区分错误实现和修复。"
    elif not patch_changes_task_source:
        reason = "补丁没有改任务源码。"
    elif patched_fail_to_pass_passed is not True:
        reason = "补丁没有让 FAIL_TO_PASS 通过。"
    else:
        reason = "基线失败，补丁改了任务源码，并且 FAIL_TO_PASS 通过。"
    return {
        "scoring_chain_present": tests_ran,
        "discriminating": discriminating,
        "repair_demonstrated": repair,
        "reason": reason,
    }


def recorded_12907() -> dict[str, Any]:
    """12907 的已记录结果：整文件 pytest 在未改 separable.py 时是 11 passed。"""
    assessed = assess_f2p(
        tests_ran=True,
        base_fail_to_pass_failed=False,
        patched_fail_to_pass_passed=True,
        patch_changes_task_source=False,
    )
    assessed.update(
        {
            "instance_id": "astropy__astropy-12907",
            "session_id": "eg-m1r-651c9e378414",
            "pytest_summary": "11 passed in 0.21s",
            "patch_files": ["probe.txt"],
        }
    )
    return assessed


def _prepare(root: Path, source: str) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / "bug.py").write_text(source, encoding="utf-8")


def _passes(root: Path) -> bool:
    completed = subprocess.run(
        [sys.executable, "-c", "import bug; raise SystemExit(0 if bug.value() == 1 else 1)"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def score_local_discriminator(directory: Path) -> dict[str, Any]:
    """用一个会失败的断言区分基线、参考修复和无关文件。"""
    buggy = 'def value():\n    return 0\n'
    gold = 'def value():\n    return 1\n'
    base = directory / "base"
    fixed = directory / "gold"
    probe = directory / "probe"
    _prepare(base, buggy)
    _prepare(fixed, gold)
    _prepare(probe, buggy)
    (probe / "probe.txt").write_text("hello\n", encoding="utf-8")
    base_failed = not _passes(base)
    gold_passed = _passes(fixed)
    probe_passed = _passes(probe)
    return {
        "base_fail_to_pass_failed": base_failed,
        "gold_fail_to_pass_passed": gold_passed,
        "probe_fail_to_pass_passed": probe_passed,
        "probe_marked_repaired": probe_passed,
        "discriminating": base_failed and gold_passed and not probe_passed,
        "runner": "python_subprocess",
    }
