"""取官方 SWE-bench Verified 里 astropy__astropy-12907 的节点列表。不把补丁正文打到终端。"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "swebench_12907_row.json"
PATCH = ROOT / "results" / "runtime" / "swebench_12907_gold.patch"
TEST_PATCH = ROOT / "results" / "runtime" / "swebench_12907_test.patch"


def _get(url: str) -> tuple[int, dict | str]:
    request = urllib.request.Request(url, headers={"User-Agent": "epochgate-measure"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        return exc.code, body


def _save(row: dict, source: str) -> None:
    patch = row.get("patch") or ""
    test_patch = row.get("test_patch") or ""
    PATCH.write_bytes(patch.encode("utf-8"))
    TEST_PATCH.write_bytes(test_patch.encode("utf-8"))
    fail = json.loads(row["FAIL_TO_PASS"]) if isinstance(row.get("FAIL_TO_PASS"), str) else row.get("FAIL_TO_PASS")
    passed = json.loads(row["PASS_TO_PASS"]) if isinstance(row.get("PASS_TO_PASS"), str) else row.get("PASS_TO_PASS")
    report = {
        "instance_id": row.get("instance_id"),
        "base_commit": row.get("base_commit"),
        "fail_to_pass": fail,
        "pass_to_pass_count": len(passed or []),
        "gold_patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        "gold_patch_bytes": len(patch.encode("utf-8")),
        "test_patch_sha256": hashlib.sha256(test_patch.encode("utf-8")).hexdigest(),
        "test_patch_bytes": len(test_patch.encode("utf-8")),
        "source": source,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"instance_id": report["instance_id"], "fail_to_pass": fail, "base_commit": report["base_commit"], "source": source}, ensure_ascii=False))


def main() -> None:
    where = "instance_id='astropy__astropy-12907'"
    query = urllib.parse.urlencode(
        {
            "dataset": "princeton-nlp/SWE-bench_Verified",
            "config": "default",
            "split": "test",
            "where": where,
            "offset": "0",
            "length": "1",
        }
    )
    status, payload = _get("https://datasets-server.huggingface.co/filter?" + query)
    if status == 200 and isinstance(payload, dict) and payload.get("rows"):
        row = payload["rows"][0].get("row") or payload["rows"][0]
        if row.get("instance_id") == "astropy__astropy-12907":
            _save(row, "datasets-server filter")
            return
    print(f"filter_status={status}")
    for offset in range(0, 40, 5):
        search = urllib.parse.urlencode(
            {
                "dataset": "princeton-nlp/SWE-bench_Verified",
                "config": "default",
                "split": "test",
                "query": "astropy__astropy-12907",
                "offset": str(offset),
                "length": "5",
            }
        )
        status, payload = _get("https://datasets-server.huggingface.co/search?" + search)
        if status != 200 or not isinstance(payload, dict):
            print(f"search_offset={offset} status={status}")
            continue
        rows = payload.get("rows") or []
        ids = []
        for item in rows:
            row = item.get("row") or item
            ids.append(row.get("instance_id"))
            if row.get("instance_id") == "astropy__astropy-12907":
                _save(row, f"datasets-server search offset {offset}")
                return
        print("search_ids", offset, ids)
    raise SystemExit("instance_not_found")


if __name__ == "__main__":
    main()
