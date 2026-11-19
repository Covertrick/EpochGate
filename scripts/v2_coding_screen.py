"""在固定仓库窗口里找真实的失败测试。不调用模型，不改写失败基线。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

POOL = Path(r"C:\Project\epochgate-pool")
REPOS = ("click", "h11", "itsdangerous", "markupsafe", "more-itertools", "packaging")
OUT = Path(__file__).resolve().parents[1] / "results" / "runtime" / "correctness" / "v2-coding-pool" / "screen.json"
DOCKER = r"C:\Users\金瑞翀\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"
IMAGE = "python:3.10-slim"
EXPECTED_ID = "sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d"
WINDOW = 12


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=timeout, check=False,
    )


def image_id() -> str:
    completed = run([DOCKER, "image", "inspect", IMAGE, "--format", "{{.Id}}"])
    return completed.stdout.strip()


def dual_commits(repo: Path) -> list[tuple[str, str]]:
    log = run(["git", "log", "-n", "80", "--pretty=%H"], repo)
    found = []
    for sha in log.stdout.split():
        parent = run(["git", "rev-parse", "--verify", f"{sha}^"], repo)
        if parent.returncode != 0:
            continue
        base = parent.stdout.strip()
        names = run(["git", "diff", "--name-only", base, sha], repo).stdout.split()
        tests = [name for name in names if name.startswith("tests/") and name.endswith(".py")]
        sources = [name for name in names if name.endswith(".py") and name not in tests and not name.startswith("tests/")]
        if tests and sources:
            found.append((base, sha))
        if len(found) >= WINDOW:
            break
    return found


def export_tree(repo: Path, sha: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "archive", sha], cwd=repo, capture_output=True, check=False)
    if archive.returncode != 0:
        raise RuntimeError(archive.stderr.decode("utf-8", "replace")[-400:])
    unpack = subprocess.run(["tar", "-x", "-C", str(destination)], input=archive.stdout, capture_output=True, check=False)
    if unpack.returncode != 0:
        raise RuntimeError(unpack.stderr.decode("utf-8", "replace")[-400:])


def apply_diff(repo: Path, base: str, sha: str, paths: list[str], destination: Path) -> str | None:
    diff = run(["git", "diff", base, sha, "--", *paths], repo)
    if diff.returncode != 0:
        return (diff.stderr or "")[-300:]
    if not (diff.stdout or "").strip():
        return "empty diff"
    applied = subprocess.run(
        ["git", "apply", "--unsafe-paths", "-"], cwd=destination, input=diff.stdout,
        text=True, encoding="utf-8", errors="replace", capture_output=True, check=False,
    )
    if applied.returncode != 0:
        return (applied.stderr or applied.stdout)[-300:]
    return None


def pytest_in_container(workdir: Path, tests: list[str]) -> dict:
    wheels = Path(r"C:\Project\epochgate-pool\_wheels")
    quoted = " ".join(tests)
    script = (
        "pip install -q --no-index --find-links /wheels pytest || { echo INSTALL_FAILED; exit 3; }; "
        "PYTHONPATH=src:. timeout 90 python -m pytest " + quoted + " -q --tb=line -p no:cacheprovider"
    )
    completed = run([
        DOCKER, "run", "--rm",
        "-v", f"{wheels}:/wheels",
        "-v", f"{workdir}:/src", "-w", "/src", IMAGE, "bash", "-lc", script,
    ], timeout=180)
    text = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    tail = "\n".join(text.splitlines()[-8:])
    if "INSTALL_FAILED" in text or completed.returncode == 3:
        return {"returncode": completed.returncode, "verdict": "install_failed", "failed": False, "passed": False, "tail": tail[-800:]}
    failed = completed.returncode != 0 and " failed" in f" {text.lower()}"
    passed = completed.returncode == 0 and " passed" in f" {text.lower()}"
    verdict = "failed" if failed else "passed" if passed else "unreadable"
    return {"returncode": completed.returncode, "verdict": verdict, "failed": failed, "passed": passed, "tail": tail[-800:]}


def screen_repo(name: str) -> list[dict]:
    repo = POOL / name
    rows = []
    if not (repo / ".git").exists():
        return [{"repo": name, "feasible": False, "reason": "clone_missing"}]
    for base, sha in dual_commits(repo):
        names = run(["git", "diff", "--name-only", base, sha], repo).stdout.split()
        tests = [item for item in names if item.startswith("tests/") and item.endswith(".py")]
        sources = [item for item in names if item.endswith(".py") and item not in tests and not item.startswith("tests/")]
        record = {"repo": name, "base": base, "fix": sha, "tests": tests, "sources": sources, "feasible": False}
        print(json.dumps({"start": name, "base": base[:10]}, ensure_ascii=False), flush=True)
        try:
            root = POOL / "_work" / f"{name}-{base[:8]}"
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
            broken = root / "broken"
            fixed = root / "fixed"
            export_tree(repo, base, broken)
            error = apply_diff(repo, base, sha, tests, broken)
            if error:
                record["reason"] = f"test_patch: {error}"
                rows.append(record)
                continue
            before = pytest_in_container(broken, tests)
            record["before"] = before
            if before.get("verdict") == "install_failed":
                record["reason"] = "container_pytest_unavailable"
                rows.append(record)
                continue
            if not before["failed"]:
                record["reason"] = "hidden_test_did_not_fail" if before.get("verdict") == "passed" else "container_result_unreadable"
                rows.append(record)
                continue
            export_tree(repo, base, fixed)
            apply_diff(repo, base, sha, tests, fixed)
            error = apply_diff(repo, base, sha, sources, fixed)
            if error:
                record["reason"] = f"source_patch: {error}"
                rows.append(record)
                continue
            after = pytest_in_container(fixed, tests)
            record["after"] = after
            if after["passed"]:
                record["feasible"] = True
                record["reason"] = "base_fails_fix_passes"
            else:
                record["reason"] = "fix_did_not_pass"
        except (subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
            record["reason"] = type(exc).__name__
        rows.append(record)
        print(json.dumps({"repo": name, "base": base[:10], "feasible": record["feasible"], "reason": record.get("reason")}, ensure_ascii=False), flush=True)
    return rows


def main() -> None:
    lock = POOL / "_screen.lock"
    if lock.exists():
        raise SystemExit("screen already running")
    lock.write_text("running", encoding="utf-8")
    try:
        _main()
    finally:
        lock.unlink(missing_ok=True)


def _main() -> None:
    current = image_id()
    report = {"image": IMAGE, "image_id": current, "image_matches_protocol": current == EXPECTED_ID, "window": WINDOW, "repos": list(REPOS), "candidates": []}
    if current != EXPECTED_ID:
        report["status"] = "ENVIRONMENT_BLOCKED"
        report["feasible"] = []
    else:
        for name in REPOS:
            report["candidates"].extend(screen_repo(name))
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        feasible = [row for row in report["candidates"] if row.get("feasible")]
        feasible.sort(key=lambda row: (row["repo"], row["base"]))
        unique = []
        seen = set()
        for row in feasible:
            key = (row["repo"], row["base"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        report["feasible"] = unique
        report["duplicate_bases_removed"] = len(feasible) - len(unique)
        report["status"] = "READY" if len(unique) >= 6 else "TASK_POOL_INSUFFICIENT"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "feasible": len(report["feasible"]), "checked": len(report["candidates"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
