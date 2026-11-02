"""阶段 C：静态 / 失效 / 可恢复探针。结果写入 results/real/phase_c/。

默认跑真实模型小样本。``--deterministic`` 用 ScriptedValidityBackend 写确定性 JSONL
与 ``acceptance_report.json``（正确性主证据）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from epochgate.cli import http_settings  # noqa: E402
from epochgate.validity_run import (  # noqa: E402
    run_deterministic_scenario,
    run_static_probe,
    run_stream_scenario,
    summarize_acceptance,
)

OUT = ROOT / "results" / "real" / "phase_c"
DET_OUT = OUT / "deterministic"


def code_version() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


async def run_deterministic(kinds: set[str], version: str) -> list[dict]:
    DET_OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    seeds = {"static": (801, 802), "invalidate": (811, 812), "recoverable": (821, 822)}
    for kind in ("static", "invalidate", "recoverable"):
        if kind not in kinds:
            continue
        path = DET_OUT / f"{kind}.jsonl"
        if path.exists():
            path.unlink()
        for seed in seeds[kind]:
            row = await run_deterministic_scenario(task_kind=kind, seed=seed, code_version=version)
            append_row(path, row)
            rows.append(row)
            print(
                f"det {kind} seed={seed} success={row['success']} "
                f"commits={row.get('action_commits')} stock={row['final_stock']} "
                f"old={row.get('old_attempt_committed')}"
            )
    return rows


def write_acceptance_report(det_rows: list[dict]) -> Path:
    real_rows: list[dict] = []
    for name in ("static", "invalidate", "recoverable"):
        for row in load_jsonl(OUT / f"{name}.jsonl"):
            row = dict(row)
            row.setdefault("task_kind", name)
            real_rows.append(row)
    report = summarize_acceptance(det_rows, real_rows)
    report["paths"] = {
        "deterministic_dir": str(DET_OUT.relative_to(ROOT)).replace("\\", "/"),
        "acceptance_report": "results/real/phase_c/acceptance_report.json",
        "real_static": "results/real/phase_c/static.jsonl",
        "real_invalidate": "results/real/phase_c/invalidate.jsonl",
        "real_recoverable": "results/real/phase_c/recoverable.jsonl",
    }
    out = OUT / "acceptance_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"acceptance_report verdict={report['verdict']} -> {out}")
    return out


async def main_async(kinds: set[str], *, deterministic: bool) -> None:
    version = code_version()
    OUT.mkdir(parents=True, exist_ok=True)

    if deterministic:
        det_rows = await run_deterministic(kinds, version)
        write_acceptance_report(det_rows)
        return

    settings = http_settings()
    if settings is None:
        raise SystemExit("缺少 EPOCHGATE_BASE_URL、EPOCHGATE_API_KEY 或 EPOCHGATE_MODEL")
    base, key, model = settings

    if "static" in kinds:
        path = OUT / "static.jsonl"
        for seed in (501, 502):
            row = await run_static_probe(
                base_url=base, api_key=key, model=model, seed=seed, code_version=version
            )
            append_row(path, row)
            print(f"static seed={seed} success={row['success']} stock={row['final_stock']} commits={row['valid_commits']}")

    if "invalidate" in kinds:
        path = OUT / "invalidate.jsonl"
        for seed in (601, 602):
            for arm in ("A", "B"):
                row = await run_stream_scenario(
                    base_url=base,
                    api_key=key,
                    model=model,
                    seed=seed,
                    arm=arm,
                    words=120,
                    delay_s=0.5,
                    code_version=version,
                    task_kind="invalidate",
                )
                append_row(path, row)
                print(
                    f"invalidate seed={seed} arm={arm} success={row['success']} "
                    f"post_text={row['post_revocation_delta_events']} "
                    f"post_tool={row['post_revocation_tool_call_delta_events']} "
                    f"tokens={row['post_revocation_observed_text_tokens']} "
                    f"commits={row['action_commits']} stock={row['final_stock']}"
                )

    if "recoverable" in kinds:
        path = OUT / "recoverable.jsonl"
        for seed in (701, 702):
            for arm in ("A", "B"):
                row = await run_stream_scenario(
                    base_url=base,
                    api_key=key,
                    model=model,
                    seed=seed,
                    arm=arm,
                    words=120,
                    delay_s=0.5,
                    code_version=version,
                    task_kind="recoverable",
                    restore_after_s=1.0,
                    restore_delta=1,
                )
                append_row(path, row)
                print(
                    f"recoverable seed={seed} arm={arm} success={row['success']} "
                    f"commits={row['action_commits']} old={row['old_attempt_committed']} "
                    f"stock={row['final_stock']} once={row['side_effect_once']}"
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kinds", default="static,invalidate,recoverable")
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="跑 ScriptedValidityBackend 并写 deterministic/ 与 acceptance_report.json",
    )
    args = parser.parse_args()
    kinds = {item.strip() for item in args.kinds.split(",") if item.strip()}
    asyncio.run(main_async(kinds, deterministic=args.deterministic))


if __name__ == "__main__":
    main()
