"""UCR-2.1 预登记常数。拟合和留出评估之前固定。"""

from __future__ import annotations

ALPHA = 0.1
MARGIN = 0.0
ETA = 0.0
LAMBDA_S = 1.0
LAMBDA_B = 1.0
FAILURE_SLACK = 0.02
RISK_THRESHOLD = 0.5
PROBE_TOKENS = 8
PROBE_SECONDS = 0.2
COSTLY_PROBE_SECONDS = 2.0
BENEFIT_S = 1.7
BAD_NEWS_S = 0.6


def information_probability(tau: float) -> float:
    """随谓词时刻上升，并在早时刻也留出可识别的 probe 机会。"""
    return min(1.0, 0.70 + 0.25 * tau)
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 8060
CELL_SIZE = 80
OVERLAP_GAP = 0.25

FEATURE_KEYS = (
    "generated_tokens",
    "tool_type",
    "tool_param_fields",
    "token_rate",
    "predicate_time",
    "predicate_valid",
    "queue_length",
    "slot_occupancy",
    "recent_restart_service",
    "completion_horizon",
    "probe_budget",
    "nominal_rate",
)

S_FEATURES = (
    "generated_tokens",
    "tool_type",
    "tool_param_fields",
    "token_rate",
    "predicate_time",
    "queue_length",
    "slot_occupancy",
    "completion_horizon",
    "nominal_rate",
)

C_FEATURES = S_FEATURES + (
    "recent_restart_service",
    "predicate_valid",
    "probe_budget",
)

FORBIDDEN_FEATURE_KEYS = frozenset(
    {
        "probe_class",
        "true_remaining_s",
        "true_restart_s",
        "predicate_after",
        "candidate_after",
        "oracle_action",
        "n_total",
        "S",
        "C",
    }
)

VOI_FORBIDDEN = frozenset(
    {
        "probe_class",
        "predicate_after",
        "true_remaining_s",
        "true_restart_s",
        "oracle_action",
        "candidate_after",
    }
)

TOOL_CODES = {"reserve": 0.0, "query": 1.0, "transfer": 2.0}
TOOLS = {
    "reserve": {"t_fixed": 0.4, "a": 36.0, "b": 8.0, "t_replan": 0.6},
    "query": {"t_fixed": 1.6, "a": 78.0, "b": 14.0, "t_replan": 2.2},
    "transfer": {"t_fixed": 4.5, "a": 150.0, "b": 20.0, "t_replan": 6.0},
}
TAU_BANDS = {"early": (0.05, 0.30), "mid": (0.35, 0.60), "late": (0.70, 0.95)}
TIME_BINS = (0.333, 0.667)
RATES = (0.8, 1.2)
FIELDS = (2, 4, 6, 8)
SCALES = (0.8, 1.2, 1.6)

# 开发只覆盖一部分联合。校准覆盖 tool × 时刻箱 × 名义速率的 12 格。
DEV_FAMILIES = {
    "F9": {
        "seed_start": 8000,
        "n": 800,
        "tool": "reserve",
        "bands": ("early", "mid"),
        "rate": 1.2,
        "fields": (2, 4),
        "scales": (0.8, 1.2),
    },
    "F10": {
        "seed_start": 9000,
        "n": 800,
        "tool": "query",
        "bands": ("mid", "late"),
        "rate": 0.8,
        "fields": (6, 8),
        "scales": (1.2, 1.6),
    },
}

HOLDOUT_FAMILIES = {
    "F13": {"seed_start": 12000, "n": 400, "tool": "reserve", "band": "late", "rate": 0.8, "fields": 8, "scale": 1.6},
    "F14": {"seed_start": 13000, "n": 400, "tool": "query", "band": "early", "rate": 1.2, "fields": 2, "scale": 0.8},
    "F15": {"seed_start": 14000, "n": 400, "tool": "reserve", "band": "mid", "rate": 0.8, "fields": 4, "scale": 1.6},
    "F16": {"seed_start": 15000, "n": 400, "tool": "query", "band": "late", "rate": 1.2, "fields": 6, "scale": 0.8},
}

STRESS_FAMILIES = {
    "F17": {"seed_start": 16000, "n": 200, "tool": "reserve", "band": "mid", "rate": 1.2, "fields": (12, 20), "scale": 1.0},
    "F18": {"seed_start": 17000, "n": 200, "tool": "reserve", "band": "mid", "rate": 1.2, "fields": 4, "scale": 2.5},
    "F19": {"seed_start": 18000, "n": 200, "tool": "transfer", "band": "mid", "rate": 1.2, "fields": 4, "scale": 1.2},
    "F20": {"seed_start": 19000, "n": 200, "tool": "query", "band": "mid", "rate": 0.4, "fields": 6, "scale": 1.2},
}

PRIMARY_ESTIMATOR = "hybrid-group"
RISK_RULE = "abort if predicate invalid, or p_fail > 0.5, or E[C] < E[S]; else continue; never probe"
