"""核对同一 checkpoint 分出的分支是否共享执行前缀。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from epochgate.eg2_audit import audit_checkpoint


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if path is None or not path.exists():
        records = [
            _sample("clean"),
            _sample("changed"),
        ]
    else:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    errors = audit_checkpoint(records)
    if errors:
        print("\n".join(errors))
        sys.exit(1)
    print(f"checkpoint_ok {len(records)}")


def _sample(branch: str) -> dict:
    return {
        "checkpoint_id": "cp-sample",
        "parent_episode_id": "ep-sample",
        "branch_id": branch,
        "intervention_id": None,
        "session_id": "ses-sample",
        "event_prefix_hash": "a" * 64,
        "boundary_seq": 3,
        "base_commit": "b" * 40,
        "workspace_hash": "c" * 64,
        "image_digest": "sha256:" + "d" * 64,
        "config_hash": "e" * 64,
        "evidence_read_set": [
            {
                "path": "src/app.py",
                "content_hash": "f" * 64,
                "read_at": "2026-11-06T00:00:00Z",
                "repo_version": "b" * 40,
            }
        ],
        "open_side_effects": False,
    }


if __name__ == "__main__":
    main()
