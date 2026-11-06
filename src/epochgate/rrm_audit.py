"""RRM-1 的协议校验和取消路径核对。不测量 P95。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RRM = ROOT / "results" / "experiments" / "rrm"
PROTOCOL_PATH = RRM / "protocol.json"
SCHEMA_PATH = RRM / "event_schema.json"
HASH_PATH = RRM / "protocol.sha256"

EVIDENCE = {"E0", "E1", "E2", "E3"}
STATUS = {"observed", "missing", "censored"}


def canonical_protocol_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def protocol_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_protocol_bytes(payload)).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_protocol(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = (
        "protocol_id",
        "version",
        "arms",
        "evidence_levels",
        "primary_comparison",
        "mechanism_comparison",
        "seeds",
        "stop_rules",
    )
    for key in required:
        if key not in payload:
            errors.append(f"protocol missing {key}")
    arms = payload.get("arms", {})
    if "ClientStopOnly" not in arms:
        errors.append("ClientStopOnly missing")
    elif "不预设" not in arms["ClientStopOnly"]:
        errors.append("ClientStopOnly must not presume the server stays running")
    if payload.get("evidence_levels", {}).keys() != EVIDENCE and set(payload.get("evidence_levels", {})) != EVIDENCE:
        errors.append("evidence levels must be E0-E3")
    seeds = payload.get("seeds", {})
    ranges = [seeds.get(name) for name in ("pilot", "development", "confirmatory") if isinstance(seeds.get(name), dict)]
    spans = [(item["start"], item["end"]) for item in ranges]
    for index, left in enumerate(spans):
        for right in spans[index + 1 :]:
            if not (left[1] < right[0] or right[1] < left[0]):
                errors.append(f"seed ranges overlap: {left} {right}")
    return errors


def validate_event(event: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in schema["required"]:
        if key not in event:
            errors.append(f"event missing {key}")
    if event.get("evidence_level") not in schema["evidence_levels"]:
        errors.append("bad evidence_level")
    status = event.get("observation_status")
    if status not in schema["observation_status"]:
        errors.append("bad observation_status")
    if status in {"missing", "censored"} and event.get("timestamp_monotonic_ns") is not None:
        errors.append("missing or censored event must not invent a timestamp")
    if status == "observed" and not isinstance(event.get("timestamp_monotonic_ns"), int):
        errors.append("observed event needs an integer timestamp")
    if event.get("evidence_level") == "E3" and event.get("source") in schema["forbidden_sources_for_e3"]:
        errors.append("E3 cannot be inferred from a global gauge")
    if event.get("arm") == "ClientStopOnly" and event.get("event") == "rrm_explicit_abort":
        errors.append("ClientStopOnly cannot record an RRM explicit abort")
    if event.get("arm") not in schema["arms"]:
        errors.append("unknown arm")
    return errors


def validate_events(events: list[dict[str, Any]], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    for event in events:
        errors.extend(validate_event(event, schema))
        identity = (
            event.get("run_id"),
            event.get("request_id"),
            event.get("event"),
            event.get("timestamp_monotonic_ns"),
            event.get("source"),
        )
        if identity in seen:
            errors.append(f"duplicate event {identity}")
        seen.add(identity)
    return errors


def analyze_async_llm_source(source: str) -> dict[str, bool]:
    generate_abort = "except (asyncio.CancelledError, GeneratorExit)" in source and "await self.abort(" in source
    engine_abort = "engine_core.abort_requests_async" in source
    return {
        "generate_catches_cancel_and_generator_exit": generate_abort,
        "abort_reaches_engine_core": engine_abort,
    }


def analyze_http_surface(router_source: str, serving_source: str, merge_source: str) -> dict[str, bool]:
    return {
        "openai_completion_route_has_abort_endpoint": "/abort" in router_source,
        "completion_route_uses_with_cancellation": "with_cancellation" in router_source,
        "streaming_returns_streaming_response": "StreamingResponse" in router_source,
        "merge_aclose_on_exit": "await iterator.aclose()" in merge_source,
        "per_request_metrics_only_on_final_usage": "enable_per_request_metrics" in serving_source,
    }


def path_verdict(async_llm: dict[str, bool], http_surface: dict[str, bool]) -> str:
    same_engine = async_llm["generate_catches_cancel_and_generator_exit"] and async_llm["abort_reaches_engine_core"]
    http_abort = http_surface["openai_completion_route_has_abort_endpoint"]
    if same_engine and not http_abort:
        return "same_engine_abort_path"
    if same_engine and http_abort:
        return "same_engine_abort_path"
    if async_llm["abort_reaches_engine_core"] and not async_llm["generate_catches_cancel_and_generator_exit"]:
        return "explicit_abort_only"
    return "request_path_unresolved"
