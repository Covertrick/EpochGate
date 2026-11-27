"""Product reporting helpers. Frozen archives are read-only."""
import csv
import hashlib
import io
import json
import subprocess
import zipfile
from datetime import datetime, timezone


def csv_bytes(rows):
    rows = list(rows)
    columns = list(dict.fromkeys(key for row in rows for key in row))
    stream = io.StringIO()
    writer = csv.DictWriter(stream, columns)
    writer.writeheader()
    for row in rows:
        cells = {}
        for name, value in row.items():
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            cells[name] = value
        writer.writerow(cells)
    return stream.getvalue().encode("utf-8-sig")


def research_metadata(view):
    return {"batch": "pilot-01", "source": "results/experiments/effect-history/pilot-01",
            "archive_commit": view["archive_commit"], "protocol_sha256": view["protocol_sha256"]}


def cell_timeline(cell):
    row = cell["row"]
    file_arm = row["arm"] == "file"
    if file_arm:
        publications = {}
        for event in cell["ledger"]["runtime_events"]:
            if event["event_type"] == "file_published":
                payload = json.loads(event["payload"])
                publications[payload["idempotency_key"]] = {"sequence": event["seq"], **payload}
    else:
        publications = {record["effect_key"]: record for record in cell["ledger"]["target_effects"]}
    timeline = []
    for sequence, event in enumerate(cell["trace"]["trace"], 1):
        key = event.get("key")
        record = publications.get(key)
        response = cell["trace"]["responses"].get(key, {})
        timeline.append({"sequence": sequence, **event, "expected": row["worlds"].get(str(event.get("version"))),
                         "target_table": "runtime_events" if file_arm else "target_effects",
                         "target_record": record, "receipt": response.get("query"),
                         "violation": bool(record and key in row["invalid_effects"])})
    return timeline


def audit_record(archive, directory):
    view = archive.overview()
    record = {**research_metadata(view), "checked_at": datetime.now(timezone.utc).isoformat(),
              "integrity": view["integrity"], "protocol_matches": view["protocol_matches"]}
    script = archive.repo / "experiments/effect_history/run.py"
    record["script_sha256"] = hashlib.sha256(script.read_bytes()).hexdigest() if script.is_file() else None
    record["report_sha256"] = view["sha256"].get("report.json")
    try:
        record.update(archive.audit())
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        record.update(status="AUDIT_FAILED", result=None, error=str(exc))
    directory.mkdir(parents=True, exist_ok=True)
    name = record["checked_at"].replace(":", "") + ".json"
    destination = directory / name
    staging = destination.with_suffix(".tmp")
    staging.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    staging.replace(destination)
    return record


def research_export(archive, kind, cell_id=None):
    view = archive.overview()
    metadata = research_metadata(view)
    report = view["report"]
    if kind == "json":
        return json.dumps({**metadata, "report": report}, ensure_ascii=False).encode(), "application/json"
    if kind == "csv":
        return csv_bytes({**metadata, **row} for row in report["rows"]), "text/csv"
    if kind == "md":
        lines = ["# Effect History Evaluation", "", *[f"- {key} `{value}`" for key, value in metadata.items()],
                 "", f"Verdict **{report['verdict']}**", "",
                 f"- Cells {report['cells']}", f"- Final mismatches {report['final_flags']}",
                 f"- Hidden counterexamples {report['hidden_counterexamples']}", "",
                 "## Equal-budget detection", "", "| Arm | Strategy | Detected |", "| --- | --- | --- |"]
        for arm, strategies in report["equal_budget_detection"].items():
            lines.extend(f"| {arm} | {strategy} | {count} |" for strategy, count in strategies.items())
        return "\n".join(lines).encode(), "text/markdown"
    if kind != "zip":
        raise FileNotFoundError("Unsupported export")
    names = view["sha256"]
    if cell_id is not None:
        archive.cell(cell_id)
        names = [name for name in names if name.startswith(cell_id + "/")]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("metadata.json", json.dumps(metadata))
        bundle.writestr("PROTOCOL.md", archive.protocol())
        bundle.writestr("sha256.json", json.dumps({name: view["sha256"][name] for name in names}))
        for name in names:
            bundle.write(archive.artifact(name), name)
    return buffer.getvalue(), "application/zip"
