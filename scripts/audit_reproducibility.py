"""核对一次运行是否写明版本、镜像、原始响应和样本种类。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from epochgate.eg2_audit import audit_reproducibility


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    manifest = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else _sample()
    errors = audit_reproducibility(manifest)
    if errors:
        print("\n".join(errors))
        sys.exit(1)
    print("reproducibility_ok")


def _sample() -> dict:
    return {
        "dsh_version": "0.2.2-rc.1",
        "image_digest": "sha256:" + "a" * 64,
        "base_commit": "b" * 40,
        "change_commit": "c" * 40,
        "model_alias": "deepseek-flash",
        "reported_model": "deepseek-v4-flash",
        "run_date": "2026-11-06",
        "raw_response_sha256": "d" * 64,
        "score_sha256": "e" * 64,
        "sample_kind": "trace_replay",
        "counts_as_new_model_sample": False,
    }


if __name__ == "__main__":
    main()
