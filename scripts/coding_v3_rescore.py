"""v3 冻结补丁的容器补测。不调用模型，不改 patch.diff 和 trace.json。"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from coding_v3 import OUT, POOL, export_base, gold_source_patch, load_tasks
from reliability_contract import sha256_text

EXPECTED_IMAGE = "sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d"
TASKS = (
    ("dev", "code-click-2247b35e", True),
    ("dev", "code-click-8abb77a8", True),
    ("confirm", "code-click-ac0e297e", False),
    ("confirm", "code-click-dd149eb3", True),
    ("confirm", "code-more-itertools-08dca284", True),
    ("confirm", "code-more-itertools-0fcdde9c", True),
)
ARMS = ("B0", "B1", "B2", "B3")


def docker_bin() -> str:
    found = shutil.which("docker")
    if not found:
        raise SystemExit("docker is not on PATH")
    return found


def require_image() -> str:
    binary = docker_bin()
    pulled = subprocess.run(
        [binary, "pull", f"python:3.10-slim@{EXPECTED_IMAGE}"],
        text=True, capture_output=True, check=False,
    )
    if pulled.returncode != 0:
        raise SystemExit((pulled.stderr or pulled.stdout)[-400:])
    inspected = subprocess.run(
        [binary, "image", "inspect", EXPECTED_IMAGE, "--format", "{{.Id}}"],
        text=True, capture_output=True, check=False,
    )
    image_id = inspected.stdout.strip()
    if image_id != EXPECTED_IMAGE:
        raise SystemExit(f"image id {image_id} does not match {EXPECTED_IMAGE}")
    return image_id


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative or relative.endswith(".pyc"):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def score(task: dict[str, Any], patch: str | None, concurrent: str | None, cache: dict[tuple, dict[str, Any]]) -> dict[str, Any]:
    key = (task["task_id"], sha256_text(concurrent or ""), sha256_text(patch or ""))
    if key in cache:
        return dict(cache[key])
    binary = docker_bin()
    work = POOL / "_score" / "v3-rescore" / task["task_id"] / key[1][:12] / (key[2][:12] or "none")
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(export_base(task), work)
    if concurrent:
        applied = subprocess.run(["git", "apply", "--unsafe-paths", "-"], cwd=work, input=concurrent, text=True, capture_output=True, check=False)
        if applied.returncode != 0:
            result = {"ok": True, "passed": False, "image_id": EXPECTED_IMAGE, "apply_error": (applied.stderr or "")[-300:], "tail": "concurrent_rejected", "tree_sha256": None}
            cache[key] = result
            return dict(result)
    if patch:
        applied = subprocess.run(["git", "apply", "--unsafe-paths", "-"], cwd=work, input=patch, text=True, capture_output=True, check=False)
        if applied.returncode != 0:
            result = {"ok": True, "passed": False, "image_id": EXPECTED_IMAGE, "apply_error": (applied.stderr or "")[-300:], "tail": "patch_rejected", "tree_sha256": tree_digest(work)}
            cache[key] = result
            return dict(result)
    hidden = work / "_hidden.patch"
    hidden.write_text(task["test_patch"], encoding="utf-8")
    applied_tests = subprocess.run(["git", "apply", "--unsafe-paths", str(hidden)], cwd=work, text=True, capture_output=True, check=False)
    hidden.unlink(missing_ok=True)
    if applied_tests.returncode != 0:
        result = {"ok": False, "passed": False, "image_id": EXPECTED_IMAGE, "error": "hidden_test_apply_failed", "tail": (applied_tests.stderr or "")[-300:], "tree_sha256": tree_digest(work)}
        cache[key] = result
        return dict(result)
    digest = tree_digest(work)
    script = (
        "pip install -q pytest >/dev/null 2>&1 || { echo INSTALL_FAILED; exit 3; }; "
        "PYTHONPATH=src:. python -m pytest " + " ".join(task["tests"]) + " -q --tb=line -p no:cacheprovider"
    )
    try:
        completed = subprocess.run(
            [binary, "run", "--rm", "-v", f"{work}:/src", "-w", "/src", EXPECTED_IMAGE, "bash", "-lc", script],
            text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=240, check=False,
        )
    except subprocess.TimeoutExpired:
        result = {"ok": False, "passed": False, "image_id": EXPECTED_IMAGE, "error": "container_timeout", "tree_sha256": digest}
        cache[key] = result
        return dict(result)
    text = (completed.stdout + "\n" + completed.stderr).strip()
    result = {
        "ok": True,
        "passed": completed.returncode == 0,
        "image_id": EXPECTED_IMAGE,
        "returncode": completed.returncode,
        "tail": "\n".join(text.splitlines()[-8:])[-800:],
        "tree_sha256": digest,
    }
    cache[key] = result
    shutil.rmtree(work, ignore_errors=True)
    return dict(result)


def phase_of(task_id: str) -> str:
    for phase, identifier, _has_patch in TASKS:
        if identifier == task_id:
            return phase
    raise KeyError(task_id)


def frozen_patch(task_id: str) -> tuple[str, str]:
    path = OUT / phase_of(task_id) / task_id / "patch.diff"
    text = path.read_text(encoding="utf-8")
    return text, sha256_text(text)


def main() -> None:
    image_id = require_image()
    by_id = {}
    for _phase, task_id, _has in TASKS:
        by_id[task_id] = load_tasks((task_id,))[0]
    cache: dict[tuple, dict[str, Any]] = {}
    rows = []
    for phase, task_id, has_patch in TASKS:
        task = by_id[task_id]
        print(f"base {task_id}", flush=True)
        base = score(task, None, None, cache)
        print(f"gold {task_id}", flush=True)
        gold = gold_source_patch(task)
        gold_score = score(task, gold, None, cache)
        row: dict[str, Any] = {
            "task_id": task_id,
            "phase": phase,
            "has_model_patch": has_patch,
            "base_hidden_passed": base.get("passed"),
            "base": base,
            "gold_sha256": sha256_text(gold),
            "gold_hidden_passed": gold_score.get("passed"),
            "gold": gold_score,
        }
        if has_patch:
            patch, digest = frozen_patch(task_id)
            trace = json.loads((OUT / phase / task_id / "trace.json").read_text(encoding="utf-8"))
            row["patch_sha256"] = digest
            row["trace_sha256"] = trace.get("diff_sha256")
            row["sha_matches_trace"] = digest == trace.get("diff_sha256")
            if not row["sha_matches_trace"]:
                row["model_patch"] = {"ok": False, "passed": False, "error": "sha_mismatch"}
                row["s1"] = []
            else:
                print(f"patch {task_id}", flush=True)
                row["model_patch"] = score(task, patch, None, cache)
                concurrent = (OUT / phase / task_id / "arms" / "concurrent.diff").read_text(encoding="utf-8")
                s1 = []
                for arm in ARMS:
                    evidence = json.loads((OUT / phase / task_id / "arms" / f"{task_id}__{arm}__S1" / "evidence.json").read_text(encoding="utf-8"))
                    accepted = bool(evidence.get("verifiable_effect"))
                    print(f"s1 {task_id} {arm}", flush=True)
                    scored = score(task, patch if accepted else None, concurrent, cache)
                    s1.append({
                        "arm": arm,
                        "accepted": accepted,
                        "stale_credential_accepted": evidence.get("stale_credential_accepted"),
                        "hidden_passed": scored.get("passed"),
                        "tree_sha256": scored.get("tree_sha256"),
                        "tail": scored.get("tail"),
                        "apply_error": scored.get("apply_error"),
                    })
                row["s1"] = s1
            row["model_hidden_passed"] = row["model_patch"].get("passed") if row["sha_matches_trace"] else None
            evidence = json.loads((OUT / phase / task_id / "arms" / f"{task_id}__B3__S3" / "evidence.json").read_text(encoding="utf-8"))
            claim = evidence.get("claim") if isinstance(evidence.get("claim"), dict) else {}
            payload_sha = claim.get("payload_sha256")
            matches = payload_sha == digest and bool(evidence.get("interrupted_recovered"))
            s3 = {
                "payload_sha256": payload_sha,
                "frozen_patch_sha256": digest,
                "payload_matches_patch": payload_sha == digest,
                "interrupted_recovered": evidence.get("interrupted_recovered"),
                "accepted_count": (evidence.get("receiver") or {}).get("accepted_count"),
            }
            if matches:
                s3["score"] = row["model_patch"]
                s3["hidden_passed"] = row["model_patch"].get("passed")
            else:
                s3["hidden_passed"] = None
                s3["score"] = {"ok": False, "error": "recovered_payload_mismatch"}
            row["s3"] = s3
        rows.append(row)
        print(json.dumps({"task_id": task_id, "base": row["base_hidden_passed"], "gold": row["gold_hidden_passed"], "patch": row.get("model_hidden_passed")}, ensure_ascii=False), flush=True)
    passed = [row["task_id"] for row in rows if row.get("model_hidden_passed") is True]
    failed = [row["task_id"] for row in rows if row.get("model_hidden_passed") is False]
    summary = {
        "image_id": image_id,
        "model_called": False,
        "assigned_confirm": 4,
        "budget_exceeded": ["code-click-ac0e297e"],
        "model_patches_scored": 5,
        "model_hidden_passed": passed,
        "model_hidden_failed": failed,
        "passed_count": len(passed),
        "rows": rows,
    }
    destination = OUT / "rescore"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "failed": failed, "count": f"{len(passed)}/5"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
