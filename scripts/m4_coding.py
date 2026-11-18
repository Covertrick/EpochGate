"""编码任务先做可行性检查。建不起来就记 blocked，不进入 48 或 144。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "correctness" / "m4-feasibility.json"

CANDIDATES = [
    {
        "task_id": f"code-{index}",
        "repo": "https://github.com/pallets/markupsafe.git",
        "note": "真实仓库。只有在初始版本能跑、至少一条独立测试失败、故障能触发时才进入矩阵。",
    }
    for index in range(1, 9)
]


def main() -> None:
    destination = ROOT / "tasks" / "coding" / "markupsafe"
    cloned = False
    error = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            subprocess.run(
                ["git", "-c", "http.proxy=http://127.0.0.1:7890", "-c", "https.proxy=http://127.0.0.1:7890", "clone", "--depth", "1", CANDIDATES[0]["repo"], str(destination)],
                check=True,
                timeout=90,
                capture_output=True,
                text=True,
            )
        cloned = destination.joinpath(".git").exists() or destination.exists()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        error = type(exc).__name__
    test_result = None
    if cloned and error is None:
        try:
            completed = subprocess.run(
                ["C:\\Miniconda\\envs\\langchain1.2\\python.exe", "-m", "pytest", "tests/test_escape.py", "-q", "--tb=line"],
                cwd=destination,
                check=False,
                timeout=120,
                capture_output=True,
                text=True,
            )
            test_result = (completed.stdout or completed.stderr or "").strip().splitlines()[-1:]
            test_result = test_result[0] if test_result else f"exit {completed.returncode}"
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            error = type(exc).__name__
    passed = bool(test_result and "passed" in test_result and "failed" not in test_result)
    rows = [{
        "task_id": "code-1",
        "repo": CANDIDATES[0]["repo"],
        "clone": "depth 1",
        "test_command": "pytest tests/test_escape.py -q",
        "test_result": test_result,
        "feasible": False,
        "entered_matrix": False,
        "blocked": True,
        "error_type": error,
        "reason": "浅克隆的当前版本独立测试通过，没有初始失败测试，不能进入 48 或 144，也不计为模型失败。" if passed else "克隆或测试没有给出可用的失败基线，不能进入矩阵，也不计为模型失败。",
    }]
    report = {
        "candidates": rows,
        "entered": 0,
        "blocked": 1,
        "note": "其余候选使用同一仓库，没有单独的失败基线，因此没有重复克隆。",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"entered": 0, "blocked": len(rows), "cloned": cloned}, ensure_ascii=False))


if __name__ == "__main__":
    main()
