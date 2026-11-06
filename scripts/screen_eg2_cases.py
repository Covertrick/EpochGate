"""M0：对少量 SWE-bench Verified 任务寻找更晚的真实提交，并尝试在基线上重放。

Linux 容器还不能评分时，不把样本记成六项都通过。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from epochgate.eg2_admission import evaluate_case

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "semantic-state" / "m0_screen.json"
WORK = ROOT / "datasets" / "cache" / "m0-repos"
ROWS = "https://datasets-server.huggingface.co/rows?dataset=princeton-nlp/SWE-bench_Verified&config=default&split=test&offset=0&length=20"
ATTEMPTS = 15


def main() -> None:
    instances = _rows()
    previous = json.loads(OUT.read_text(encoding="utf-8"))["reports"] if OUT.exists() else []
    by_id = {item["case_id"]: item for item in previous}
    reports = []
    for instance in instances[:ATTEMPTS]:
        case_id = instance["instance_id"]
        kept = by_id.get(case_id)
        detail = str(kept.get("apply_detail", "")) if kept is not None else ""
        retry = detail.startswith("github_http_") or detail in {"IncompleteRead", "RemoteDisconnected"}
        if kept is not None and not retry:
            reports.append(kept)
            continue
        try:
            reports.append(_attempt(instance))
        except Exception as exc:
            reports.append(_blocked(case_id, instance.get("repo", ""), instance.get("base_commit", ""), type(exc).__name__))
        _write(reports)
    print(_write(reports)["data_gate"], len(reports), sum(1 for item in reports if str(item.get("apply_detail", "")).startswith("github_http_")))


def _write(reports: list[dict]) -> dict:
    admitted = [item["case_id"] for item in reports if item["admission"]["admitted"]]
    payload = {
        "data_gate": "pass" if len(admitted) >= 5 else "stop",
        "candidates_searched": len(reports),
        "admitted": len(admitted),
        "linux_replay": False,
        "reason": "宿主 Git 重放不能代替 Linux 上的变更后测试。不足五个完整事件对时停止 benchmark 扩展。",
        "reports": reports,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _nearby_commits(repo: str, base_time: str) -> list:
    start = datetime.fromisoformat(base_time.replace("Z", "+00:00"))
    for days in (2, 14, 60):
        until = (start + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = urllib.parse.urlencode({"since": base_time, "until": until, "per_page": 100})
        page = _github(f"/repos/{repo}/commits?{query}")
        if any(item["sha"] for item in page):
            return page
    return []


def _rows() -> list[dict]:
    with urllib.request.urlopen(ROWS, timeout=120) as response:
        body = json.load(response)
    return [item["row"] for item in body["rows"]]


def _attempt(instance: dict) -> dict:
    case_id = instance["instance_id"]
    repo = instance["repo"]
    base = instance["base_commit"]
    try:
        base_commit = _github(f"/repos/{repo}/commits/{base}")
        base_time = base_commit["commit"]["committer"]["date"]
        later_list = _nearby_commits(repo, base_time)
    except urllib.error.HTTPError as exc:
        return _blocked(case_id, repo, base, f"github_http_{exc.code}")
    later = next((item for item in reversed(later_list) if item["sha"] != base), None)
    if later is None:
        return _blocked(case_id, repo, base, "no_nearby_commit")
    change = later["sha"]
    try:
        comparison = _github(f"/repos/{repo}/compare/{base}...{change}")
    except urllib.error.HTTPError as exc:
        return _blocked(case_id, repo, base, f"compare_http_{exc.code}", change)
    ancestor = comparison.get("status") in {"ahead", "diverged"} and comparison.get("ahead_by", 0) > 0
    change_files = [item.get("filename") for item in comparison.get("files") or []]
    ahead_by = int(comparison.get("ahead_by") or 0)
    if len(change_files) >= 300 or ahead_by > 40:
        apply_status, apply_detail = "not_attempted", f"unbounded_history files={len(change_files)} ahead={ahead_by}"
    else:
        apply_status, apply_detail = _replay(repo, base, change)
    gold_files = _diff_files(instance.get("patch") or "")
    witness = apply_status == "applied" and _gold_still_applies(repo, base, instance.get("patch") or "")
    pre_solved = apply_status == "applied" and _gold_already_present(repo, base, instance.get("patch") or "")
    case = {
        "case_id": case_id,
        "issue_time": instance.get("created_at") or base_time,
        "base_time": base_time,
        "change_time": later["commit"]["committer"]["date"],
        "base_is_ancestor": ancestor,
        "change_url": later.get("html_url") or "",
        "change_sha": change,
        "apply_status": apply_status,
        "contains_target_fix": True if pre_solved else False if apply_status == "applied" else None,
        "leaks_target_fix": False if apply_status == "applied" else None,
        "recovery_witness_present": witness and not pre_solved,
        "post_state_test_present": False,
    }
    return {
        "case_id": case_id,
        "repo": repo,
        "base_commit": base,
        "change_commit": change,
        "ancestor": ancestor,
        "gold_file_count": len(gold_files),
        "change_file_count": len(change_files),
        "shared_files": sorted(set(gold_files) & set(change_files)),
        "apply_detail": apply_detail,
        "admission": evaluate_case(case),
    }


def _replay(repo: str, base: str, change: str) -> tuple[str, str]:
    if shutil.which("git") is None:
        return "not_attempted", "git_missing"
    root = WORK / repo.replace("/", "__") / base[:12]
    _reset_dir(root)
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    commands = [
        ["git", "init"],
        ["git", "config", "core.autocrlf", "false"],
        ["git", "remote", "add", "origin", f"https://github.com/{repo}.git"],
        ["git", "fetch", "--depth", "1", "origin", base],
        ["git", "fetch", "--depth", "1", "origin", change],
        ["git", "checkout", "--detach", base],
    ]
    for command in commands:
        timeout = 600 if command[1] == "fetch" else 180
        result = subprocess.run(
            command,
            cwd=root,
            env=env,
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip().splitlines()
            return "not_attempted", detail[-1] if detail else "git_failed"
    diff = subprocess.run(
        ["git", "diff", "--binary", base, change],
        cwd=root,
        env=env,
        capture_output=True,
        timeout=180,
    )
    if diff.returncode != 0 or not diff.stdout.strip():
        return "conflict", "diff_unavailable"
    applied = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn"],
        cwd=root,
        env=env,
        input=diff.stdout,
        capture_output=True,
        timeout=180,
    )
    if applied.returncode != 0:
        return "conflict", "diff_does_not_apply"
    subprocess.run(
        ["git", "apply", "--whitespace=nowarn"],
        cwd=root,
        env=env,
        input=diff.stdout,
        capture_output=True,
        timeout=180,
        check=False,
    )
    return "applied", "host_git_tree_diff"


def _reset_dir(root: Path) -> None:
    if root.exists():
        def onerror(func, path, _exc):
            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(root, onerror=onerror)
    root.mkdir(parents=True)


def _gold_still_applies(repo: str, base: str, patch: str) -> bool:
    root = WORK / repo.replace("/", "__") / base[:12]
    if not patch.strip():
        return False
    result = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn"],
        cwd=root,
        input=patch.encode("utf-8"),
        capture_output=True,
        timeout=60,
    )
    return result.returncode == 0


def _gold_already_present(repo: str, base: str, patch: str) -> bool:
    root = WORK / repo.replace("/", "__") / base[:12]
    if not patch.strip():
        return False
    result = subprocess.run(
        ["git", "apply", "--reverse", "--check", "--whitespace=nowarn"],
        cwd=root,
        input=patch.encode("utf-8"),
        capture_output=True,
        timeout=60,
    )
    return result.returncode == 0


def _diff_files(patch: str) -> list[str]:
    files = []
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" ")
            if len(parts) >= 4:
                files.append(parts[3][2:] if parts[3].startswith("b/") else parts[3])
    return files


def _blocked(case_id: str, repo: str, base: str, reason: str, change: str = "") -> dict:
    case = {
        "case_id": case_id,
        "issue_time": "",
        "base_time": "",
        "change_time": "",
        "base_is_ancestor": False,
        "change_url": "",
        "change_sha": change,
        "apply_status": "not_attempted",
        "contains_target_fix": None,
        "leaks_target_fix": None,
        "recovery_witness_present": False,
        "post_state_test_present": False,
    }
    return {"case_id": case_id, "repo": repo, "base_commit": base, "apply_detail": reason, "admission": evaluate_case(case)}


def _github(path: str) -> dict | list:
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"User-Agent": "epochgate-m0", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


if __name__ == "__main__":
    main()
