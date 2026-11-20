"""Controlled operation attribution using real local Git ref updates."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import subprocess
from pathlib import Path

REF = "refs/heads/publish"
MARKER = "refs/epochgate/operations/original"
HISTORIES = ("original", "other_writer", "reverted", "not_dispatched")
PROFILES = ("retained", "disabled", "expired")


def git(repo, *args, input_text=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Experiment", "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Experiment", "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_AUTHOR_DATE": "2026-11-21T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-11-21T00:00:00+00:00", "LC_ALL": "C",
    })
    payload = input_text.encode("utf-8") if input_text is not None else None
    result = subprocess.run(["git", *args], cwd=repo, input=payload, env=env,
                            capture_output=True, check=True, timeout=15)
    return result.stdout.decode("utf-8").strip()


def save(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")


def scenario(repo, history, profile, marker):
    repo.mkdir()
    git(repo, "init", "--bare", "--initial-branch=publish")
    git(repo, "config", "core.logAllRefUpdates", str(profile != "disabled").lower())
    tree = git(repo, "mktree", input_text="")
    old = git(repo, "commit-tree", tree, "-m", "initial")
    new = git(repo, "commit-tree", tree, "-p", old, "-m", "publish")
    git(repo, "update-ref", "-m", "initialize", REF, old)
    operations = []

    def update(operation, before, after):
        reason = f"operation:{operation}"
        if operation == "original" and marker:
            script = f"start\nupdate {REF} {after} {before}\ncreate {MARKER} {after}\nprepare\ncommit\n"
            git(repo, "update-ref", "-m", reason, "--stdin", input_text=script)
        else:
            git(repo, "update-ref", "-m", reason, REF, after, before)
        operations.append({"operation": operation, "old": before, "new": after})

    if history in {"original", "reverted"}:
        update("original", old, new)
    if history == "other_writer":
        update("other", old, new)
    if history == "reverted":
        update("rollback", new, old)
    log_path = repo / "logs" / REF
    before_expiry = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    if profile == "expired":
        git(repo, "reflog", "expire", "--expire=all", REF)
    available = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    marker_oid = git(repo, "for-each-ref", "--format=%(objectname)", MARKER) or None
    advertised = git(repo, "ls-remote", "--refs", repo.as_uri(), REF, MARKER)
    git(repo, "config", "uploadpack.hideRefs", "refs/epochgate/")
    advertised_hidden = git(repo, "ls-remote", "--refs", repo.as_uri(), REF, MARKER)
    target = git(repo, "rev-parse", REF)
    expected_ref = f"{target}\t{REF}"
    if advertised_hidden != expected_ref or expected_ref not in advertised.splitlines():
        raise ValueError("Unexpected Git transport visibility")
    return {
        "history": history, "profile": profile, "marker_enabled": marker,
        "planned": {"old": old, "new": new}, "target": target,
        "operations_withheld_from_recovery": operations, "reflog_before_expiry": before_expiry,
        "reflog_available": available, "marker_oid": marker_oid,
        "transport_advertised": advertised.splitlines(), "transport_marker_hidden": advertised_hidden.splitlines(),
    }


def decide(row):
    old, new = row["planned"]["old"], row["planned"]["new"]
    original = any(e["operation"] == "original" for e in row["operations_withheld_from_recovery"])
    entries = []
    for entry in row["reflog_available"]:
        metadata, message = entry.split("\t", 1)
        before, after, _ = metadata.split(" ", 2)
        if (before, after) == (old, new):
            entries.append(message)
    return original, {
        "tip_equality": row["target"] == new,
        "reflog_oid_pair": bool(entries),
        "reflog_operation_id": "operation:original" in entries,
        "transaction_marker": row["marker_oid"] == new,
        "transport_marker_exposed": f"{new}\t{MARKER}" in row["transport_advertised"],
        "transport_marker_hidden": f"{new}\t{MARKER}" in row["transport_marker_hidden"],
    }


def summarize(rows):
    methods = {}
    for row in rows:
        truth, predictions = decide(row)
        for method, confirmed in predictions.items():
            key = f"{row['profile']}:{'marker' if row['marker_enabled'] else 'no_marker'}:{method}"
            counts = methods.setdefault(key, dict(true_confirmations=0, false_confirmations=0,
                                                 unknown=0, missed_historical_effects=0))
            counts["true_confirmations"] += confirmed and truth
            counts["false_confirmations"] += confirmed and not truth
            counts["unknown"] += not confirmed
            counts["missed_historical_effects"] += not confirmed and truth
    return {"cases": len(rows), "method_counts": methods}


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "audit"))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "run":
        output.mkdir(parents=True, exist_ok=False)
        with subprocess.Popen(["git", "--version"], stdout=subprocess.PIPE, text=True) as process:
            version = process.communicate(timeout=10)[0].strip()
            if process.returncode:
                raise RuntimeError("git --version failed")
        rows = [scenario(output / f"{h}-{p}-{int(m)}.git", h, p, m)
                for h, p, m in itertools.product(HISTORIES, PROFILES, (False, True))]
        save(output / "evidence.json", {"git": version, "rows": rows})
        save(output / "report.json", summarize(rows))
        save(output / "sha256.json", {n: checksum(output / n) for n in ("evidence.json", "report.json")})
    else:
        hashes = json.loads((output / "sha256.json").read_bytes())
        for name, expected in hashes.items():
            if checksum(output / name) != expected:
                raise ValueError(f"Evidence hash mismatch: {name}")
        rows = json.loads((output / "evidence.json").read_bytes())["rows"]
        if summarize(rows) != json.loads((output / "report.json").read_bytes()):
            raise ValueError("Recomputed report differs")
    result = summarize(rows)
    print(f"{result['cases']} Git cases; JSON report recomputed")
    for key, counts in result["method_counts"].items():
        if ":marker:" in key:
            print(key, counts)


if __name__ == "__main__":
    main()
