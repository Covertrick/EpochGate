"""检查 RRM-1 协议哈希、缺字段和事件序列。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from epochgate.rrm_audit import (
    HASH_PATH,
    PROTOCOL_PATH,
    SCHEMA_PATH,
    load_json,
    protocol_sha256,
    validate_events,
    validate_protocol,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, default=None)
    parser.add_argument("--write-hash", action="store_true")
    args = parser.parse_args()
    payload = load_json(PROTOCOL_PATH)
    schema = load_json(SCHEMA_PATH)
    errors = validate_protocol(payload)
    digest = protocol_sha256(payload)
    if args.write_hash:
        HASH_PATH.write_text(digest + "\n", encoding="utf-8")
    elif not HASH_PATH.exists() or HASH_PATH.read_text(encoding="utf-8").strip() != digest:
        errors.append("protocol.sha256 does not match protocol.json")
    if args.events is not None:
        rows = [json.loads(line) for line in args.events.read_text(encoding="utf-8").splitlines() if line.strip()]
        errors.extend(validate_events(rows, schema))
    if errors:
        print("\n".join(errors))
        sys.exit(1)
    print(digest)


if __name__ == "__main__":
    main()
