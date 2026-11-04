"""v5.1 冻结分析。

复现开跑前把本文件的 sha256 写入协议。重采样单位是 trace 配对，不是 bystander。
"""

from __future__ import annotations

import hashlib
import math
import random
from pathlib import Path
from typing import Any

HALF_WIDTH_TARGET_S = 0.25
SAMPLE_CAP = 50
SAMPLE_FLOOR = 8
BOOT_N = 10000
BOOT_SEED_CONFIRM = 20261104
BOOT_SEED_REPLICATION = 20261105
RELATIVE_MIN = 0.10
TAU_S = 0.05
HOLDOUT_DELTA_S = 0.20

DU_DEFINITION = "focal local slot occupancy (slot_released_mono - slot_acquired_mono), G0 minus G2-local"
DT_DEFINITION = "paired difference of within-trace bystander P95; not a single-request difference"
M0_SHARE = "alpha and beta estimated on medium only and transferred to high; not re-fit per load"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def analysis_source_sha256() -> str:
    return sha256_file(Path(__file__).resolve())


def bootstrap_mean(values: list[float], *, replicates: int = BOOT_N, seed: int) -> dict[str, float]:
    """对配对差的均值做有放回重采样。每次抽 len(values) 个，不拆开 trace。"""
    if not values:
        raise ValueError("bootstrap needs at least one paired difference")
    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(replicates):
        total = 0.0
        for _draw in range(n):
            total += values[rng.randrange(n)]
        means.append(total / n)
    means.sort()

    def _at(p: float) -> float:
        return means[int(p * (len(means) - 1))]

    return {
        "n": float(n),
        "mean": sum(values) / n,
        "p2_5": _at(0.025),
        "p97_5": _at(0.975),
        "replicates": float(replicates),
        "seed": float(seed),
    }


def engineering_gate(mean_delta: float, p2_5: float, baseline_mean: float) -> dict[str, Any]:
    """相对至少 10%，且 2.5% 分位大于 0。正差表示 G2-local 更短。"""
    relative = None if baseline_mean == 0 else mean_delta / baseline_mean
    relative_ok = relative is not None and relative >= RELATIVE_MIN
    ci_ok = p2_5 > 0
    return {
        "relative": relative,
        "relative_ok": relative_ok,
        "ci_lower_positive": ci_ok,
        "passed": bool(relative_ok and ci_ok),
    }


def _occupancy_s(focal: dict[str, Any] | None) -> float | None:
    if not focal:
        return None
    released = focal.get("slot_released_mono")
    acquired = focal.get("slot_acquired_mono")
    if released is None or acquired is None:
        return None
    return float(released) - float(acquired)


def _optional_diff(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def pair_traces(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一 (seed, load) 的 G0 与 G2-local 合成一个配对单位。"""
    grouped: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((int(row["seed"]), str(row["load"])), {})[str(row["arm"])] = row
    pairs: list[dict[str, Any]] = []
    for (seed, load), arms in sorted(grouped.items()):
        g0 = arms.get("G0")
        g2 = arms.get("G2-local")
        if g0 is None or g2 is None:
            pairs.append({"seed": seed, "load": load, "usable": False, "reason": "missing_arm"})
            continue
        if g0.get("failed") or g2.get("failed"):
            pairs.append({"seed": seed, "load": load, "usable": False, "reason": "failed"})
            continue
        s0 = g0.get("summary") or {}
        s2 = g2.get("summary") or {}
        if any(s0.get(key) is None or s2.get(key) is None for key in ("W_slot_p95_s", "T_e2e_p95_s")):
            pairs.append({"seed": seed, "load": load, "usable": False, "reason": "missing_p95"})
            continue
        f0 = g0.get("focal") or {}
        f2 = g2.get("focal") or {}
        u0, u2 = _occupancy_s(f0), _occupancy_s(f2)
        pairs.append(
            {
                "seed": seed,
                "load": load,
                "usable": True,
                "dW_slot": float(s0["W_slot_p95_s"]) - float(s2["W_slot_p95_s"]),
                "dT_e2e": float(s0["T_e2e_p95_s"]) - float(s2["T_e2e_p95_s"]),
                "g0_W": float(s0["W_slot_p95_s"]),
                "g2_W": float(s2["W_slot_p95_s"]),
                "g0_E": float(s0["T_e2e_p95_s"]),
                "g2_E": float(s2["T_e2e_p95_s"]),
                "dU_client": None if u0 is None or u2 is None else u0 - u2,
                "d_output_tokens": _optional_diff(f0.get("output_tokens"), f2.get("output_tokens")),
                "d_focal_e2e": _optional_diff(f0.get("T_e2e_s"), f2.get("T_e2e_s")),
                "arm_order": g0.get("arm_order") or g2.get("arm_order") or "",
            }
        )
    return pairs


def usable(pairs: list[dict[str, Any]], load: str) -> list[dict[str, Any]]:
    return [pair for pair in pairs if pair.get("usable") and pair["load"] == load]


def summarize_load(pairs: list[dict[str, Any]], load: str, *, seed: int) -> dict[str, Any]:
    chosen = usable(pairs, load)
    if not chosen:
        return {"load": load, "n": 0}
    boot_w = bootstrap_mean([p["dW_slot"] for p in chosen], seed=seed)
    boot_e = bootstrap_mean([p["dT_e2e"] for p in chosen], seed=seed)
    base_w = sum(p["g0_W"] for p in chosen) / len(chosen)
    base_e = sum(p["g0_E"] for p in chosen) / len(chosen)
    return {
        "load": load,
        "n": len(chosen),
        "dW_slot": boot_w,
        "dT_e2e": boot_e,
        "g0_W_mean": base_w,
        "g0_E_mean": base_e,
        "gate_W": engineering_gate(boot_w["mean"], boot_w["p2_5"], base_w),
        "gate_E": engineering_gate(boot_e["mean"], boot_e["p2_5"], base_e),
        "same_direction": sum(1 for p in chosen if p["dW_slot"] > TAU_S and p["dT_e2e"] > TAU_S),
        "separated": sum(
            1
            for p in chosen
            if (p["dW_slot"] > TAU_S and p["dT_e2e"] < -TAU_S)
            or (p["dW_slot"] < -TAU_S and p["dT_e2e"] > TAU_S)
        ),
        "sensitivity": {
            "dT_vs_d_output_tokens": pearson(
                [p["dT_e2e"] for p in chosen if p["d_output_tokens"] is not None],
                [p["d_output_tokens"] for p in chosen if p["d_output_tokens"] is not None],
            ),
            "dT_vs_d_focal_e2e": pearson(
                [p["dT_e2e"] for p in chosen if p["d_focal_e2e"] is not None],
                [p["d_focal_e2e"] for p in chosen if p["d_focal_e2e"] is not None],
            ),
            "dW_vs_dU_client": pearson(
                [p["dW_slot"] for p in chosen if p["dU_client"] is not None],
                [p["dU_client"] for p in chosen if p["dU_client"] is not None],
            ),
        },
    }


def pearson(xs: list[float], ys: list[float]) -> dict[str, float | None]:
    n = min(len(xs), len(ys))
    if n < 3:
        return {"n": n, "r": None}
    xs, ys = xs[:n], ys[:n]
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return {"n": n, "r": None}
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return {"n": n, "r": cov / math.sqrt(vx * vy)}


def sample_size_plan(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """用封存配对差的样本标准差规划复现 n。看到复现结果后不得再加 n。"""
    per_load: dict[str, Any] = {}
    needs: list[int] = []
    for load in ("medium", "high"):
        values = [p["dW_slot"] for p in usable(pairs, load)]
        n = len(values)
        if n < 2:
            per_load[load] = {"n_planning": n, "sd": None, "n_for_half_width": SAMPLE_CAP}
            needs.append(SAMPLE_CAP)
            continue
        mean = sum(values) / n
        sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
        if sd < 1e-9:
            need = SAMPLE_FLOOR
        else:
            need = math.ceil((1.96 * sd / HALF_WIDTH_TARGET_S) ** 2)
        need = max(SAMPLE_FLOOR, min(SAMPLE_CAP, need))
        per_load[load] = {"n_planning": n, "mean": mean, "sd": sd, "n_for_half_width": need}
        needs.append(need)
    return {
        "metric": "paired mean dW_slot",
        "half_width_target_s": HALF_WIDTH_TARGET_S,
        "cap": SAMPLE_CAP,
        "floor": SAMPLE_FLOOR,
        "n": max(needs) if needs else SAMPLE_CAP,
        "per_load": per_load,
        "rule": (
            "n = min(50, max over loads of the normal approximation n whose 95% half-width "
            "is at most 0.25s). Planning SD comes from sealed 7000-7049 and is not mixed "
            "into the replication estimate. Do not add seeds after seeing replication outcomes."
        ),
    }


def _ols(xs: list[float], ys: list[float]) -> dict[str, Any]:
    n = len(xs)
    if n < 2:
        return {"alpha": None, "beta": None, "n": n}
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var < 1e-12:
        return {"alpha": 0.0, "beta": my, "n": n, "train_mae": 0.0}
    alpha = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    beta = my - alpha * mx
    mae = sum(abs(y - (alpha * x + beta)) for x, y in zip(xs, ys)) / n
    return {"alpha": alpha, "beta": beta, "n": n, "train_mae": mae}


def fit_m0(pairs: list[dict[str, Any]], *, train_load: str = "medium") -> dict[str, Any]:
    xs: list[float] = []
    ys: list[float] = []
    for pair in usable(pairs, train_load):
        if pair.get("dU_client") is None:
            continue
        xs.append(float(pair["dU_client"]))
        ys.append(float(pair["dT_e2e"]))
    fit = _ols(xs, ys)
    fit.update(
        {
            "name": "M0",
            "train_load": train_load,
            "dU_client": DU_DEFINITION,
            "dT": DT_DEFINITION,
            "parameters_shared_across_loads": False,
            "transfer": M0_SHARE,
            "backend_residual": "not a covariate",
        }
    )
    return fit


def holdout_residuals(
    model: dict[str, Any],
    pairs: list[dict[str, Any]],
    *,
    holdout_load: str = "high",
    seed: int,
) -> dict[str, Any]:
    """留出集只做预测。训练行不得进入这里。"""
    if model.get("alpha") is None:
        return {"status": "not_fit", "n": 0}
    residuals: list[float] = []
    abs_err: list[float] = []
    for pair in usable(pairs, holdout_load):
        if pair.get("dU_client") is None:
            continue
        if pair["load"] == model.get("train_load"):
            raise ValueError("holdout load matches training load")
        pred = float(model["alpha"]) * float(pair["dU_client"]) + float(model["beta"])
        err = float(pair["dT_e2e"]) - pred
        residuals.append(err)
        abs_err.append(abs(err))
    if not residuals:
        return {"status": "empty", "n": 0}
    boot = bootstrap_mean(residuals, seed=seed)
    mean_abs = sum(abs_err) / len(abs_err)
    significant = boot["p2_5"] > 0 or boot["p97_5"] < 0
    large = abs(boot["mean"]) >= HOLDOUT_DELTA_S
    return {
        "status": "predicted",
        "n": len(residuals),
        "mean_residual": boot["mean"],
        "p2_5": boot["p2_5"],
        "p97_5": boot["p97_5"],
        "mae": mean_abs,
        "m0_leaves_unexplained_effect": bool(significant and large),
        "rule": "unexplained only if the holdout residual CI excludes 0 and |mean| >= 0.20s",
    }


def model_identifiability() -> dict[str, Any]:
    """DS 黑盒没有可观测后端残余。M1/M2 不拟合，不用 0 填充残余。"""
    return {
        "M0": {
            "formula": "dT_hat = alpha * dU_client + beta",
            "fit_on_ds": True,
            "dU_client": DU_DEFINITION,
            "dT": DT_DEFINITION,
            "alpha_beta": M0_SHARE,
            "backend_residual_covariate": False,
        },
        "M1": {
            "formula": "dT_hat = alpha * dU_client + gamma * residual_service + beta",
            "fit_on_ds": False,
            "reason": "backend residual service is unobserved on DeepSeek; do not substitute 0",
        },
        "M2": {
            "formula": "M1 + successor/bystander scheduling interaction",
            "fit_on_ds": False,
            "reason": "requires an observed backend residual term; a better in-sample fit would not be a new mechanism",
        },
    }


def replication_seeds(n: int, *, start: int = 8100) -> list[int]:
    if n < 1 or n > SAMPLE_CAP:
        raise ValueError(f"replication n must be in 1..{SAMPLE_CAP}")
    seeds = list(range(start, start + n))
    forbidden = set(range(6000, 6015)) | set(range(7000, 7050))
    if forbidden.intersection(seeds):
        raise ValueError("replication seeds overlap sealed windows")
    return seeds
