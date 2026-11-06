import json

from epochgate.rrm_audit import (
    PROTOCOL_PATH,
    SCHEMA_PATH,
    analyze_async_llm_source,
    load_json,
    path_verdict,
    protocol_sha256,
    validate_event,
    validate_protocol,
)


def test_frozen_protocol_has_distinct_seed_ranges():
    payload = load_json(PROTOCOL_PATH)
    assert validate_protocol(payload) == []
    assert protocol_sha256(payload)


def test_cancelled_generate_and_explicit_abort_share_engine_core():
    source = """
    except (asyncio.CancelledError, GeneratorExit):
        await self.abort(q.request_id, internal=True)
    async def abort(self, request_id):
        await self.engine_core.abort_requests_async(all_request_ids)
    """
    flags = analyze_async_llm_source(source)
    assert path_verdict(flags, {"openai_completion_route_has_abort_endpoint": False}) == "same_engine_abort_path"


def test_event_schema_rejects_invented_e3_and_explicit_client_stop():
    schema = load_json(SCHEMA_PATH)
    base = {
        "run_id": "pilot-0001",
        "request_id": "req-0001",
        "role": "target",
        "arm": "PredicateAbort",
        "event": "engine_scheduler_removed",
        "timestamp_monotonic_ns": 1,
        "source": "num_requests_running",
        "evidence_level": "E3",
        "observation_status": "observed",
        "plan_epoch": 1,
        "config_hash": "abc",
    }
    assert validate_event(base, schema)
    missing = dict(base, observation_status="missing", timestamp_monotonic_ns=5, evidence_level="E2", source="engine_scheduler")
    assert any("invent" in item for item in validate_event(missing, schema))
    client = dict(base, arm="ClientStopOnly", event="rrm_explicit_abort", evidence_level="E1", source="rrm")
    assert any("explicit" in item for item in validate_event(client, schema))


def test_path_check_record_does_not_authorize_reclamation_contrast():
    report = json.loads((PROTOCOL_PATH.parent / "path_check.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "same_engine_abort_path"
    assert report["proceed_to_1b_as_distinct_reclamation"] is False
    assert report["e2_observed"] is False
