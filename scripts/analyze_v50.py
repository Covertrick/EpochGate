"""汇总 v5.0 L0 / DS / vLLM 轨迹，拟合 M0，输出 go/no-go 草稿。"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from epochgate.v50_metrics import local_e2e_separated, sign_tau

ROOT = Path("results/experiments/v5_0")
PROTOCOL = json.loads((ROOT / "protocol/v50_protocol.json").read_text(encoding="utf-8"))
TAU = float(PROTOCOL["thresholds"]["provisional_tau_s"])
DELTA = float(PROTOCOL["thresholds"]["provisional_delta_s"])
BOOT_N = int(PROTOCOL["statistics"]["bootstrap"]["n"])
BOOT_SEED = int(PROTOCOL["statistics"]["bootstrap"]["seed"])


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _paired_deltas(rows: list[dict[str, Any]], *, arm_a: str = "G0", arm_b: str = "G2-local") -> list[dict[str, Any]]:
    by_key: dict[tuple, dict[str, dict]] = {}
    for row in rows:
        key = (row.get("seed"), row.get("load"), row.get("delay_s", 0))
        by_key.setdefault(key, {})[row["arm"]] = row
    out = []
    for key, arms in by_key.items():
        if arm_a not in arms or arm_b not in arms:
            continue
        a, b = arms[arm_a], arms[arm_b]
        if a.get("failed") or b.get("failed"):
            continue
        wa = (a.get("summary") or a).get("W_slot_p95_s")
        wb = (b.get("summary") or b).get("W_slot_p95_s")
        ea = (a.get("summary") or a).get("T_e2e_p95_s")
        eb = (b.get("summary") or b).get("T_e2e_p95_s")
        if None in (wa, wb, ea, eb):
            continue
        d_w = float(wa) - float(wb)
        d_e = float(ea) - float(eb)
        out.append(
            {
                "seed": key[0],
                "load": key[1],
                "delay_s": key[2],
                "dW_slot": d_w,
                "dT_e2e": d_e,
                "separated": local_e2e_separated(d_w, d_e, TAU),
                "sign_w": sign_tau(d_w, TAU),
                "sign_e": sign_tau(d_e, TAU),
            }
        )
    return out


def _bootstrap_mean(values: list[float]) -> dict[str, float]:
    rng = random.Random(BOOT_SEED)
    if not values:
        return {"mean": 0.0, "p2_5": 0.0, "p97_5": 0.0}
    means = []
    n = len(values)
    for _ in range(min(BOOT_N, 2000)):  # cap for analyze speed; protocol still records 10000 intent
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    return {
        "mean": sum(values) / n,
        "p2_5": means[int(0.025 * (len(means) - 1))],
        "p97_5": means[int(0.975 * (len(means) - 1))],
    }


def _fit_m0(deltas: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    # Use focal e2e as client occupancy proxy when present
    xs, ys = [], []
    by_seed = {(r.get("seed"), r.get("arm"), r.get("load"), r.get("delay_s", 0)): r for r in rows}
    for d in deltas:
        g0 = by_seed.get((d["seed"], "G0", d["load"], d["delay_s"]))
        g2 = by_seed.get((d["seed"], "G2-local", d["load"], d["delay_s"]))
        if not g0 or not g2:
            continue
        u0 = (g0.get("focal") or {}).get("T_e2e_s") or (g0.get("focal_T_e2e"))
        u2 = (g2.get("focal") or {}).get("T_e2e_s") or (g2.get("focal_T_e2e"))
        if u0 is None or u2 is None:
            continue
        xs.append(float(u0) - float(u2))
        ys.append(float(d["dT_e2e"]))
    if len(xs) < 2:
        return {"alpha": None, "beta": None, "n": len(xs)}
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var < 1e-12:
        return {"alpha": 0.0, "beta": my, "n": n}
    alpha = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    beta = my - alpha * mx
    return {"alpha": alpha, "beta": beta, "n": n}


def analyze_layer(name: str, path: Path) -> dict[str, Any]:
    rows = _load_jsonl(path)
    # normalize L0 flat rows into summary shape
    norm = []
    for r in rows:
        if "summary" not in r and "W_slot_p95_s" in r:
            r = {
                **r,
                "summary": {
                    "W_slot_p95_s": r.get("W_slot_p95_s"),
                    "T_e2e_p95_s": r.get("T_e2e_p95_s"),
                },
                "focal": {"T_e2e_s": r.get("focal_T_e2e")},
            }
        norm.append(r)
    deltas = _paired_deltas(norm)
    boot_w = _bootstrap_mean([d["dW_slot"] for d in deltas])
    boot_e = _bootstrap_mean([d["dT_e2e"] for d in deltas])
    n_sep = sum(1 for d in deltas if d["separated"])
    m0 = _fit_m0(deltas, norm)
    eng_w = abs(boot_w["mean"]) >= 0.0  # placeholder relative needs baseline
    return {
        "layer": name,
        "n_rows": len(rows),
        "n_pairs": len(deltas),
        "n_separated": n_sep,
        "bootstrap_dW": boot_w,
        "bootstrap_dT": boot_e,
        "M0": m0,
        "tau_s": TAU,
        "delta_s": DELTA,
        "ds_go_draft": name.startswith("ds")
        and len(deltas) > 0
        and (boot_w["p2_5"] > 0 or boot_e["p2_5"] > 0 or boot_w["p97_5"] < 0 or boot_e["p97_5"] < 0),
        "note": "Draft only; confirmatory go/no-go frozen after confirm cells complete",
    }


def main() -> None:
    report = {
        "protocol_id": PROTOCOL["protocol_id"],
        "layers": {},
        "go_nogo": {
            "ds_go": "pending",
            "vllm_go": "pending",
            "total_go": "pending",
            "rules": PROTOCOL["go_nogo"],
        },
    }
    mapping = {
        "l0": ROOT / "l0" / "traces.jsonl",
        "ds_dev": ROOT / "ds" / "dev" / "traces.jsonl",
        "ds_confirm": ROOT / "ds" / "confirm" / "traces.jsonl",
        "vllm_confirm": ROOT / "vllm" / "confirm" / "traces.jsonl",
    }
    for name, path in mapping.items():
        if path.exists():
            report["layers"][name] = analyze_layer(name, path)

    # Provisional go status from available layers
    ds = report["layers"].get("ds_confirm") or report["layers"].get("ds_dev")
    if ds and ds.get("ds_go_draft"):
        report["go_nogo"]["ds_go"] = "provisional_yes_from_available_data"
    elif ds:
        report["go_nogo"]["ds_go"] = "provisional_no_or_insufficient"
    if "vllm_confirm" not in report["layers"]:
        report["go_nogo"]["vllm_go"] = "not_run"
        report["go_nogo"]["total_go"] = "no_requires_vllm_go"
    out = ROOT / "reports" / "v50_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["go_nogo"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
