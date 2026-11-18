"""文件库 supersede 的批次测量。统计复现单位是批，不是单次操作。

不修改 journal_mode 或 synchronous，只把连接上读到的值记下来。
延迟从连接已经打开之后的 supersede 调用算起。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "runtime" / "correctness" / "e4-audit"
WORK = OUT / "e7-work"


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return round(ordered[index], 6)


def run_batch(concurrency: int, directory: Path) -> dict:
    from epochgate_runtime.ledger import RunLedger
    from epochgate_runtime.sink import EffectSink

    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "ledger.sqlite"
    sink_database = directory / "sink.sqlite"
    setup = RunLedger(database)
    journal_mode = setup._conn.execute("PRAGMA journal_mode").fetchone()[0]
    synchronous = setup._conn.execute("PRAGMA synchronous").fetchone()[0]
    setup.create_run("run")
    setup.advance("run", "running")
    setup.close()
    EffectSink(sink_database).close()
    pairs = [(RunLedger(database), EffectSink(sink_database)) for _ in range(concurrency)]
    barrier = threading.Barrier(concurrency)
    samples: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()

    def once(pair) -> None:
        local_ledger, local_sink = pair
        barrier.wait(30)
        started = time.perf_counter()
        try:
            local_ledger.supersede("run", local_sink)
            elapsed = time.perf_counter() - started
            with lock:
                samples.append(elapsed)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            local_ledger.close()
            local_sink.close()

    wall_started = time.perf_counter()
    threads = [threading.Thread(target=once, args=(pair,)) for pair in pairs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = time.perf_counter() - wall_started
    return {
        "concurrency": concurrency,
        "journal_mode": journal_mode,
        "synchronous": synchronous,
        "pragma_changed": False,
        "latency_includes_process_start": False,
        "latency_includes_connection_open": False,
        "operations": samples,
        "failures": errors,
        "completed": len(samples),
        "batch_wall_s": wall,
        "throughput_per_s": (len(samples) / wall) if wall else None,
        "operation_p50_s": _percentile(samples, 0.5),
        "batch_median_s": _percentile(samples, 0.5),
    }


def _invoke(concurrency: int, directory: Path) -> dict:
    process_started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--batch", str(concurrency), str(directory)],
        check=True,
        capture_output=True,
        text=True,
    )
    process_wall = time.perf_counter() - process_started
    payload = json.loads(completed.stdout)
    payload["process_wall_s"] = round(process_wall, 6)
    return payload


def summarize(level: str, batches: list[dict]) -> dict:
    medians = [float(item["batch_median_s"]) for item in batches if item.get("batch_median_s") is not None]
    operations = [float(sample) for item in batches for sample in item.get("operations") or []]
    throughput = [float(item["throughput_per_s"]) for item in batches if item.get("throughput_per_s")]
    return {
        "concurrency": int(level),
        "batch_count": len(batches),
        "statistical_unit": "batch",
        "operations_per_batch": int(level),
        "journal_mode": batches[0]["journal_mode"] if batches else None,
        "synchronous": batches[0]["synchronous"] if batches else None,
        "pragma_changed": False,
        "warmup_excluded": True,
        "latency_starts": "supersede call after the connection is open",
        "latency_excludes": ["process start", "connection open"],
        "batch_median_s": {
            "min": _percentile(medians, 0),
            "p05": _percentile(medians, 0.05),
            "p50": _percentile(medians, 0.5),
            "p95": _percentile(medians, 0.95),
            "max": _percentile(medians, 1),
            "percentile_interval_p05_p95": [_percentile(medians, 0.05), _percentile(medians, 0.95)],
            "label": "分布的单位是批次中位数",
        },
        "operation_latency_s": {
            "n": len(operations),
            "p50": _percentile(operations, 0.5),
            "p99": _percentile(operations, 0.99),
            "label": "操作分布。这些样本不是互相独立的确认实验。",
        },
        "throughput_per_s_p50": _percentile(throughput, 0.5),
        "failures": sum(len(item.get("failures") or []) for item in batches),
        "process_wall_s_p50": _percentile([float(item["process_wall_s"]) for item in batches], 0.5),
    }


def main() -> None:
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    measured: dict[str, list[dict]] = {}
    for concurrency in (1, 16):
        warmup = WORK / f"warmup-{concurrency}"
        _invoke(concurrency, warmup)
        batches = []
        for index in range(30):
            payload = _invoke(concurrency, WORK / f"{concurrency}-{index}")
            batches.append(payload)
            print(json.dumps({"concurrency": concurrency, "batch": index, "median_s": payload.get("batch_median_s"), "failures": payload.get("failures")}, ensure_ascii=False), flush=True)
        measured[str(concurrency)] = batches
    report = {
        "host": "one machine, one new sqlite file and one new process per batch",
        "not_cross_machine": True,
        "not_480_independent_trials": True,
        "levels": {level: summarize(level, batches) for level, batches in measured.items()},
        "batches": measured,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / "e7_batches.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(WORK, ignore_errors=True)
    print(json.dumps({"wrote": str(destination), "levels": {key: value["batch_median_s"] for key, value in report["levels"].items()}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--batch":
        print(json.dumps(run_batch(int(sys.argv[2]), Path(sys.argv[3]))))
    else:
        main()
