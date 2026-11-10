"""写出 v5.1 DS 审计，并在硬门槛通过后冻结复现协议。"""

from __future__ import annotations

import json
from pathlib import Path

from epochgate.v51_audit import (
    PROTOCOL_PATH,
    PUBLISHED_PATH,
    SEALED,
    audit_records,
    build_protocol,
    load_jsonl,
    render_markdown,
)
from epochgate.v51_analysis import sha256_file

AUDIT_MD = Path("docs/档案/v5.1/弃用-DS审计.md")
AUDIT_JSON = Path("results/experiments/v5_1/audit/v51_ds_audit.json")
PROTOCOL_OUT = Path("results/experiments/v5_1/protocol/v51_protocol.json")


def main() -> None:
    before = sha256_file(SEALED)
    rows = load_jsonl(SEALED)
    after = sha256_file(SEALED)
    if before != after:
        raise SystemExit("sealed confirm JSONL changed while the audit was reading it")
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    published = json.loads(PUBLISHED_PATH.read_text(encoding="utf-8"))
    report = audit_records(rows, protocol=protocol, published=published)
    report["sealed_sha256"] = before
    AUDIT_JSON.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    AUDIT_MD.write_text(render_markdown(report, sealed_sha256=before), encoding="utf-8")
    if report["hard_passed"]:
        PROTOCOL_OUT.parent.mkdir(parents=True, exist_ok=True)
        PROTOCOL_OUT.write_text(
            json.dumps(build_protocol(report, sealed_sha256=before), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"audit hard gate passed; n={report['sample_size_plan']['n']}")
    else:
        failed = [item["name"] for item in report["checks"] if not item["passed"]]
        print("audit hard gate failed:", ", ".join(failed))


if __name__ == "__main__":
    main()
