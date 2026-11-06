"""阶段 1A。只核对安装包里的取消路径，不计算 P95。"""

from __future__ import annotations

import json
from pathlib import Path

from epochgate.rrm_audit import analyze_async_llm_source, analyze_http_surface, path_verdict

OUT = Path("results/experiments/rrm/path_check.json")
SITE = Path(r"D:\vllm-win\venv\Lib\site-packages\vllm")


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"missing installed file: {path}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    version = "unknown"
    for relative in ("_version.py", "version.py"):
        version_file = SITE / relative
        if not version_file.exists():
            continue
        for line in version_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__") and "'" in line:
                version = line.split("'")[-2]
                break
        if version != "unknown":
            break
    async_source = _read(SITE / "v1" / "engine" / "async_llm.py")
    router = _read(SITE / "entrypoints" / "openai" / "completion" / "api_router.py")
    serving = _read(SITE / "entrypoints" / "openai" / "completion" / "serving.py")
    merge = _read(SITE / "utils" / "async_utils.py")
    async_flags = analyze_async_llm_source(async_source)
    http_flags = analyze_http_surface(router, serving, merge)
    verdict = path_verdict(async_flags, http_flags)
    report = {
        "stage": "1A",
        "installed_version": version,
        "version_matches_protocol": version == "0.32.0",
        "async_llm": async_flags,
        "http": http_flags,
        "request_id_visible_to_client": "id=request_id" in serving,
        "explicit_abort_is_python_api": "async def abort(" in async_source,
        "openai_http_has_per_request_abort": http_flags["openai_completion_route_has_abort_endpoint"],
        "verdict": verdict,
        "client_stop_is_uncancelled_control": False if verdict == "same_engine_abort_path" else None,
        "e2_observed": False,
        "proceed_to_1b_as_distinct_reclamation": verdict == "explicit_abort_only",
        "natural_completion_is_not_cancel_success": True,
        "evidence": "installed_source",
        "notes": [
            "AsyncLLM.generate 捕获 CancelledError 和 GeneratorExit 后调用 abort。",
            "abort 调用 engine_core.abort_requests_async。",
            "OpenAI /v1/completions 没有单独的逐请求 abort 路由。",
            "流式响应断开时，merge_async_iterators 会 aclose 内层 generate。",
            "这次没有把全局 num_requests_running 写成该请求已停止。",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(verdict, version)


if __name__ == "__main__":
    main()
