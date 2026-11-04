"""v5.1 四组 vLLM 对照。残余只记台架上看到的，不把脚本注入写成机制。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8000"
OUT = Path("results/experiments/v5_1/vllm/groups.json")
MD = Path("docs/测试记录/v5.1-vLLM四组.md")
GAUGES = (
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
    "vllm:gpu_cache_usage_perc",
)


def _parse_prometheus(text: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, rest = line.partition("{")
        body = rest if rest else line
        if rest:
            body = body.split("}", 1)[-1]
            key = line.split(" ", 1)[0]
        else:
            key = name.split(" ", 1)[0]
            body = line
        parts = body.split()
        if not parts:
            continue
        try:
            value = float(parts[-1])
        except ValueError:
            continue
        if key.startswith("vllm:"):
            found[key] = value
        bare = key.split("{", 1)[0]
        if bare in GAUGES and bare not in found:
            found[bare] = value
    return found


async def _sample(client: httpx.AsyncClient, started: float, bucket: list[dict[str, Any]], stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            response = await client.get(f"{BASE}/metrics", timeout=2.0)
            parsed = _parse_prometheus(response.text) if response.status_code == 200 else {}
        except httpx.HTTPError:
            parsed = {}
        bucket.append({"t_s": round(time.monotonic() - started, 3), **{k: parsed.get(k) for k in GAUGES}})
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.15)
        except TimeoutError:
            pass


async def _stream(
    client: httpx.AsyncClient,
    *,
    model: str,
    prompt: str,
    max_tokens: int,
    stop_after_chunks: int | None,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": True,
    }
    chunks = 0
    closed_early = False
    error = ""
    ttft = None
    done = None
    t0 = time.monotonic()
    try:
        async with client.stream("POST", f"{BASE}/v1/chat/completions", json=payload, timeout=120.0) as response:
            if response.status_code != 200:
                body = (await response.aread())[:200].decode(errors="replace")
                return {"ok": False, "error": f"http_{response.status_code}:{body}", "chunks": 0, "closed_early": False}
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    done = time.monotonic()
                    break
                chunks += 1
                if ttft is None:
                    ttft = time.monotonic()
                if stop_after_chunks is not None and chunks >= stop_after_chunks:
                    closed_early = True
                    await response.aclose()
                    done = time.monotonic()
                    break
    except Exception as exc:  # noqa: BLE001 — transport is the observation
        error = f"{type(exc).__name__}: {exc}"[:240]
        if stop_after_chunks is not None:
            closed_early = True
    return {
        "ok": not error,
        "error": error,
        "chunks": chunks,
        "closed_early": closed_early,
        "ttft_s": None if ttft is None else round(ttft - t0, 3),
        "read_s": None if done is None else round(done - t0, 3),
    }


async def _occupy(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    role: str,
    arrival_s: float,
    *,
    model: str,
    prompt: str,
    max_tokens: int,
    stop_after_chunks: int | None,
    hold_after_stop_s: float,
    origin: float,
    events: list[dict[str, Any]],
    released: asyncio.Event | None,
) -> None:
    events.append({"t_s": round(time.monotonic() - origin, 3), "event": "enqueued", "role": role})
    if arrival_s > 0:
        await asyncio.sleep(arrival_s)
    events.append({"t_s": round(time.monotonic() - origin, 3), "event": "arrival", "role": role})
    await sem.acquire()
    events.append({"t_s": round(time.monotonic() - origin, 3), "event": "slot_acquired", "role": role})
    events.append({"t_s": round(time.monotonic() - origin, 3), "event": "engine_enter", "role": role})
    result = await _stream(
        client,
        model=model,
        prompt=prompt,
        max_tokens=max_tokens,
        stop_after_chunks=stop_after_chunks,
    )
    events.append({"t_s": round(time.monotonic() - origin, 3), "event": "local_read_done", "role": role, **result})
    if result.get("closed_early") and hold_after_stop_s > 0:
        events.append({"t_s": round(time.monotonic() - origin, 3), "event": "slot_release_waiting", "role": role, "hold_s": hold_after_stop_s})
        await asyncio.sleep(hold_after_stop_s)
    events.append({"t_s": round(time.monotonic() - origin, 3), "event": "slot_released", "role": role})
    sem.release()
    if released is not None:
        released.set()


def _gauge_series(samples: list[dict[str, Any]], key: str) -> list[float | None]:
    return [row.get(key) for row in samples]


def _residual_s(samples: list[dict[str, Any]], disconnect_t: float | None) -> float | None:
    """断流后 running 仍大于 0 的时间。没有断流或没有指标时不填。"""
    if disconnect_t is None:
        return None
    last = None
    for row in samples:
        running = row.get("vllm:num_requests_running")
        if row["t_s"] + 1e-9 < disconnect_t or running is None:
            continue
        if float(running) > 0:
            last = float(row["t_s"])
        elif last is not None:
            return round(last - disconnect_t, 3)
    if last is None:
        return 0.0
    return round(last - disconnect_t, 3)


async def run_arm(name: str, *, model: str, stop_chunks: int | None, hold_s: float) -> dict[str, Any]:
    origin = time.monotonic()
    events: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    stop = asyncio.Event()
    sem = asyncio.Semaphore(1)
    released = asyncio.Event()
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        before = _parse_prometheus((await client.get(f"{BASE}/metrics")).text)
        sampler = asyncio.create_task(_sample(client, origin, samples, stop))
        target = _occupy(
            client,
            sem,
            "target",
            0.0,
            prompt="Count slowly from 1 to 40, one number per line.",
            max_tokens=128,
            stop_after_chunks=stop_chunks,
            hold_after_stop_s=hold_s,
            origin=origin,
            events=events,
            released=released,
            model=model,
        )
        bystanders = [
            _occupy(
                client,
                sem,
                f"bystander-{i}",
                0.05,
                model=model,
                prompt="Reply with the single word ready.",
                max_tokens=16,
                stop_after_chunks=None,
                hold_after_stop_s=0.0,
                origin=origin,
                events=events,
                released=None,
            )
            for i in range(2)
        ]

        async def successor() -> None:
            events.append({"t_s": round(time.monotonic() - origin, 3), "event": "successor_waiting", "role": "successor"})
            await released.wait()
            await _occupy(
                client,
                sem,
                "successor",
                0.0,
                prompt="Reply with the single word next.",
                max_tokens=16,
                stop_after_chunks=None,
                hold_after_stop_s=0.0,
                origin=origin,
                events=events,
                released=None,
                model=model,
            )

        await asyncio.gather(target, *bystanders, successor())
        await asyncio.sleep(0.4)
        stop.set()
        await sampler
        after = _parse_prometheus((await client.get(f"{BASE}/metrics")).text)
    disconnect = next((e["t_s"] for e in events if e["event"] == "local_read_done" and e["role"] == "target" and e.get("closed_early")), None)
    slot_release = next(e["t_s"] for e in events if e["event"] == "slot_released" and e["role"] == "target")
    bystander_start = next((e["t_s"] for e in events if e["event"] == "slot_acquired" and e["role"] == "bystander-0"), None)
    return {
        "arm": name,
        "source": "vllm-0.32.0-windows",
        "model": model,
        "slots": 1,
        "stop_after_chunks": stop_chunks,
        "hold_after_stop_s": hold_s,
        "events": events,
        "metric_samples": samples,
        "metrics_before": {k: before.get(k) for k in before if "finished_reason" in k or k in GAUGES},
        "metrics_after": {k: after.get(k) for k in after if "finished_reason" in k or k in GAUGES},
        "observed_residual_s": _residual_s(samples, disconnect),
        "target_slot_release_s": slot_release,
        "bystander0_slot_acquired_s": bystander_start,
        "claim_ceiling": "only gauges and this process's disconnect; no injected residual",
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render(reports: list[dict[str, Any]]) -> str:
    lines = [
        "# v5.1 vLLM 四组",
        "",
        "引擎是本机 Windows 轮子 vLLM 0.32.0，模型 `Qwen/Qwen2.5-1.5B-Instruct`，RTX 4060 8GB。四组各跑一次，不是确认性矩阵。脚本注入的残余不在这里。",
        "",
        "| 组 | 断流 | 本地槽再持有 | 断流后 running>0 的观测秒数 | 焦点放槽时刻 | bystander-0 拿到槽 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for report in reports:
        target = next(e for e in report["events"] if e["event"] == "local_read_done" and e["role"] == "target")
        lines.append(
            "| {arm} | {closed} | {hold} | {residual} | {release} | {bystander} |".format(
                arm=report["arm"],
                closed="是" if target.get("closed_early") else "否",
                hold=_fmt(report["hold_after_stop_s"]),
                residual=_fmt(report["observed_residual_s"]),
                release=_fmt(report["target_slot_release_s"]),
                bystander=_fmt(report["bystander0_slot_acquired_s"]),
            )
        )
    lines.extend(
        [
            "",
            "`observed_residual_s` 来自 `/metrics` 里 `vllm:num_requests_running` 在断流之后仍大于 0 的时间。采样间隔约 0.15 秒。没有 per-request abort 状态接口时，不把这个 gauge 写成 L2-a 的 aborted 回执。",
            "KV 占用只在 `vllm:gpu_cache_usage_perc` 实际出现并变化时才能谈回收。",
            "",
        ]
    )
    return "\n".join(lines)


async def main() -> None:
    async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
        health = await client.get(f"{BASE}/v1/models")
        if health.status_code != 200:
            raise SystemExit(f"vLLM not ready: http_{health.status_code}")
        model = health.json()["data"][0]["id"]
    plan = [
        ("G0", None, 0.0),
        ("G2-local", 8, 0.0),
        ("G2-residual", 8, 0.0),
        ("G2-hold-slot", 8, 1.5),
    ]
    reports = []
    for name, chunks, hold in plan:
        print(f"start {name}", flush=True)
        report = await run_arm(name, model=model, stop_chunks=chunks, hold_s=hold)
        reports.append(report)
        print(
            f"done {name} residual={report['observed_residual_s']} release={report['target_slot_release_s']}",
            flush=True,
        )
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            for _ in range(40):
                parsed = _parse_prometheus((await client.get(f"{BASE}/metrics")).text)
                running = float(parsed.get("vllm:num_requests_running") or 0)
                waiting = float(parsed.get("vllm:num_requests_waiting") or 0)
                if running == 0 and waiting == 0:
                    break
                await asyncio.sleep(0.25)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    MD.write_text(render(reports), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
