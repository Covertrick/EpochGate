"""M0-R 探索池。不改写 results/semantic-state/m0_screen.json。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from epochgate.eg2_m0r import engineering_gate, research_suitability

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "results" / "semantic-state" / "m0_screen.json"
OUT = ROOT / "results" / "semantic-state" / "recovery"
ROWS = "https://datasets-server.huggingface.co/rows?dataset=princeton-nlp/SWE-bench_Verified&config=default&split=test&offset={offset}&length=20"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    old = json.loads(OLD.read_text(encoding="utf-8"))
    old_reports = [item for item in old["reports"] if item.get("admission", {}).get("admitted")]
    suitability = [research_suitability(item) for item in old_reports]
    (OUT / "m0r_annotation_audit.json").write_text(
        json.dumps({"cases": suitability, "old_screen_unchanged": True}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rows = _load_rows()
    selected = _stratify(rows)
    records = [_existing(item) for item in old_reports]
    seen = {item["case_id"] for item in records}
    blocked = False
    block_reason = ""
    for instance in selected:
        if instance["instance_id"] in seen:
            continue
        if len(records) >= 36:
            break
        if blocked:
            records.append(_status(instance, "AccessBlocked", block_reason))
            continue
        try:
            records.append(_history(instance))
        except urllib.error.HTTPError as exc:
            reason = f"github_http_{exc.code}"
            if exc.code in {403, 429}:
                blocked = True
                block_reason = reason
            records.append(_status(instance, "AccessBlocked", reason))
        except Exception as exc:
            records.append(_status(instance, "AccessBlocked", type(exc).__name__))
    while len(records) < 32 and selected:
        break
    coverage: dict[str, int] = defaultdict(int)
    for item in records:
        coverage[item["repo"]] += 1
    gate = engineering_gate(records)
    (OUT / "m0r_candidate_manifest.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    (OUT / "m0r_admission_report.json").write_text(
        json.dumps({"records": len(records), "gate": gate}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUT / "m0r_repository_coverage.json").write_text(
        json.dumps({"repositories": dict(coverage), "repository_count": len(coverage)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUT / "m0r_gate_decision.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"data_gate": gate["data_gate"], "pool": len(records), "repos": len(coverage)}, ensure_ascii=False))


def _existing(report: dict) -> dict:
    return {
        "case_id": report["case_id"],
        "repo": report.get("repo", ""),
        "base_commit": report.get("base_commit", ""),
        "change_commit": report.get("change_commit", ""),
        "m0r_status": "limited_existing",
        "reason": "旧六项准入保留。研究适用性见 annotation audit，四格尚未齐。",
    }


def _status(instance: dict, status: str, reason: str) -> dict:
    return {
        "case_id": instance.get("instance_id", ""),
        "repo": instance.get("repo", ""),
        "base_commit": instance.get("base_commit", ""),
        "m0r_status": status,
        "reason": reason,
    }


def _history(instance: dict) -> dict:
    repo = instance["repo"]
    base = instance["base_commit"]
    commit = _github(f"/repos/{repo}/commits/{base}")
    base_time = commit["commit"]["committer"]["date"]
    nearby = _nearby(repo, base_time)
    later = next((item for item in reversed(nearby) if item.get("sha") != base), None)
    if later is None:
        return _status(instance, "Unsatisfiable", "no_nearby_commit")
    comparison = _github(f"/repos/{repo}/compare/{base}...{later['sha']}")
    files = comparison.get("files") or []
    if len(files) >= 300 or int(comparison.get("ahead_by") or 0) > 40:
        return _status(instance, "Unsatisfiable", "unbounded_history")
    return {
        "case_id": instance["instance_id"],
        "repo": repo,
        "base_commit": base,
        "change_commit": later["sha"],
        "change_file_count": len(files),
        "m0r_status": "Unscorable",
        "reason": "找到了附近提交，但这一轮没有在对应仓库的 Linux 环境里跑完四格测试。",
    }


def _nearby(repo: str, base_time: str) -> list:
    start = datetime.fromisoformat(base_time.replace("Z", "+00:00"))
    for days in (14, 60):
        until = (start + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = urllib.parse.urlencode({"since": base_time, "until": until, "per_page": 20})
        page = _github(f"/repos/{repo}/commits?{query}")
        if page:
            return page
    return []


def _github(path: str):
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"User-Agent": "epochgate-m0r", "Accept": "application/vnd.github+json"},
    )
    last = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError:
            raise
        except Exception as exc:
            last = exc
    raise last if last is not None else RuntimeError("github_failed")


def _load_rows() -> list[dict]:
    rows = []
    seen = set()
    for offset in (0, 80, 160, 240, 320, 400, 460):
        for _ in range(2):
            try:
                with urllib.request.urlopen(ROWS.format(offset=offset), timeout=120) as response:
                    body = json.load(response)
                for item in body["rows"]:
                    row = item["row"]
                    if row["instance_id"] not in seen:
                        seen.add(row["instance_id"])
                        rows.append(row)
                break
            except Exception:
                continue
        repos = {row["repo"] for row in rows}
        if len(repos) >= 4 and len(rows) >= 32:
            break
    return rows


def _stratify(rows: list[dict]) -> list[dict]:
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_repo[row["repo"]].append(row)
    chosen = []
    for repo in sorted(by_repo, key=lambda name: (-len(by_repo[name]), name))[:4]:
        chosen.extend(by_repo[repo][:8])
    return chosen


if __name__ == "__main__":
    main()
