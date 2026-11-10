"""??????????????????????"""

from __future__ import annotations

import argparse

from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.sink import EffectSink


def run_fault(db: str, sink_path: str, fault: str, run_id: str, key: str, token: int, version: int) -> None:
    ledger = RunLedger(db)
    sink = EffectSink(sink_path)
    try:
        reserved = ledger.record_intent(
            run_id,
            key,
            token,
            version,
            effect_id=f"effect-{key}",
            arguments_hash="payload",
        )
        if not reserved["allowed"]:
            raise SystemExit(2)
        if fault == "before_dispatch":
            raise SystemExit(86)
        ledger.mark_effect_state(key, "DISPATCHED")
        if fault == "before_sink":
            raise SystemExit(86)
        decision = sink.accept(key, expected_version=version, payload="payload")
        if decision["status"] not in {"accepted", "replayed"}:
            raise SystemExit(3)
        if fault == "after_sink":
            raise SystemExit(86)
        ledger.store_receipt(run_id, key, token, decision["receipt"])
        if fault == "after_receipt":
            raise SystemExit(86)
    finally:
        ledger.close()
        sink.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--sink", required=True)
    parser.add_argument("--fault", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--token", type=int, required=True)
    parser.add_argument("--version", type=int, required=True)
    args = parser.parse_args()
    run_fault(args.db, args.sink, args.fault, args.run, args.key, args.token, args.version)


if __name__ == "__main__":
    main()
