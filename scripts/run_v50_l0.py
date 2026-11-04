"""L0：脚本残余网格，校准 local/e2e 分叉与 M0 草稿。不进确认性主表。"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

from epochgate.v50_runner import run_v50_trace

OUT = Path("results/experiments/v5_0/l0/traces.jsonl")
REPORT = Path("results/experiments/v5_0/l0/l0_summary.json")
DELAYS = [0.0, 0.5, 1.0, 2.0, 4.0, 8.0]
SEEDS = list(range(6000, 6005))


async def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    rows_out: list[dict] = []
    for delay in DELAYS:
        for seed in SEEDS:
            for arm in ("G0", "G2-local", "G2-residual"):
                hold = delay if arm == "G2-residual" else 0.0
                trace = await run_v50_trace(
                    seed=seed,
                    arm=arm,  # type: ignore[arg-type]
                    backend_kind="scripted",
                    cell_id=f"l0-d{delay}",
                    pair_id=f"l0-s{seed}-d{delay}",
                    batch_id=f"l0-s{seed}-d{delay}-{arm}",
                    residual_hold_s=hold,
                    bystanders_b=8,
                    slots_s=2,
                )
                rec = {
                    "seed": seed,
                    "arm": arm,
                    "delay_s": delay,
                    "failed": trace["failed"],
                    "W_slot_p95_s": trace["summary"]["W_slot_p95_s"],
                    "T_e2e_p95_s": trace["summary"]["T_e2e_p95_s"],
                    "bystander_count": trace["summary"]["bystander_count"],
                    "incomplete_count": trace["summary"]["incomplete_count"],
                    "focal_W_slot": (trace["focal"] or {}).get("W_slot_s"),
                    "focal_T_e2e": (trace["focal"] or {}).get("T_e2e_s"),
                    "focal_slot_released": (trace["focal"] or {}).get("slot_released_mono"),
                    "focal_revoked": (trace["focal"] or {}).get("eligibility_revoked_mono"),
                }
                rows_out.append(rec)
                with OUT.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
                print(f"l0 seed={seed} arm={arm} D={delay} W={rec['W_slot_p95_s']} E={rec['T_e2e_p95_s']}", flush=True)

    # Fit crude M0 on G0 vs G2-local at D=0: dT ~ alpha * dU + beta using focal occupancy proxy
    pairs = []
    for seed in SEEDS:
        g0 = next(r for r in rows_out if r["seed"] == seed and r["arm"] == "G0" and r["delay_s"] == 0.0)
        g2 = next(r for r in rows_out if r["seed"] == seed and r["arm"] == "G2-local" and r["delay_s"] == 0.0)
        if g0["T_e2e_p95_s"] is None or g2["T_e2e_p95_s"] is None:
            continue
        # client occupancy proxy: e2e of focal if present else wall
        u0 = g0.get("focal_T_e2e") or 0.0
        u2 = g2.get("focal_T_e2e") or 0.0
        d_u = float(u0) - float(u2)
        d_t = float(g0["T_e2e_p95_s"]) - float(g2["T_e2e_p95_s"])
        pairs.append((d_u, d_t))
    alpha = beta = None
    if len(pairs) >= 2:
        # least squares
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        n = len(xs)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        var_x = sum((x - mean_x) ** 2 for x in xs)
        if var_x > 1e-12:
            alpha = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / var_x
            beta = mean_y - alpha * mean_x
        else:
            alpha, beta = 0.0, mean_y

    # Separation scan for residual delays
    sep = []
    for delay in DELAYS:
        for seed in SEEDS:
            g0 = next(r for r in rows_out if r["seed"] == seed and r["arm"] == "G0" and r["delay_s"] == delay)
            g2r = next(
                r for r in rows_out if r["seed"] == seed and r["arm"] == "G2-residual" and r["delay_s"] == delay
            )
            if None in (g0["W_slot_p95_s"], g0["T_e2e_p95_s"], g2r["W_slot_p95_s"], g2r["T_e2e_p95_s"]):
                continue
            d_w = float(g0["W_slot_p95_s"]) - float(g2r["W_slot_p95_s"])
            d_e = float(g0["T_e2e_p95_s"]) - float(g2r["T_e2e_p95_s"])
            sep.append({"seed": seed, "delay_s": delay, "dW_slot": d_w, "dT_e2e": d_e})

    REPORT.write_text(
        json.dumps(
            {
                "n_rows": len(rows_out),
                "M0_draft": {"alpha": alpha, "beta": beta, "n_pairs": len(pairs)},
                "separation_scan": sep,
                "note": "L0 scripted only; not confirmatory; DS residual arm forbidden",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {REPORT} alpha={alpha} beta={beta}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
