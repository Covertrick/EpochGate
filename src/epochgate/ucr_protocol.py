"""UCR-Agent 预登记协议。拟合之前就固定，测试集不能再改这些常数。"""

from __future__ import annotations

ALPHA = 0.1
MARGIN = 0.0
ETA = 0.0
LAMBDA_S = 1.0
LAMBDA_B = 1.0
FAILURE_SLACK = 0.02
PROBE_TOKENS = 8
PROBE_SECONDS = 0.2
WIDTH_SCALES = (0.5, 1.0, 2.0)
PRIMARY_WIDTH = 1.0
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 7060

FEATURE_KEYS = (
    "generated_tokens",
    "prefix_length",
    "tool_type",
    "tool_param_fields",
    "recent_same_tool_service",
    "token_rate",
    "epoch_changed_since_start",
    "epoch_age",
    "has_recent_revision",
    "predicate_valid",
    "predicate_change_type",
    "queue_length",
    "slot_occupancy",
    "recent_restart_service",
    "plan_depth",
    "recent_replan_count",
    "arrival_rate",
)

S_FEATURES = (
    "generated_tokens",
    "prefix_length",
    "tool_type",
    "tool_param_fields",
    "recent_same_tool_service",
    "token_rate",
    "queue_length",
    "slot_occupancy",
)

C_FEATURES = S_FEATURES + (
    "recent_restart_service",
    "predicate_change_type",
    "plan_depth",
    "recent_replan_count",
)

FORBIDDEN_FEATURE_KEYS = frozenset(
    {
        "plan_epoch",
        "true_remaining_s",
        "true_restart_s",
        "remaining_service",
        "restart_service",
        "harmful",
        "harmless",
        "oracle_action",
        "future_tokens",
        "completion_time",
        "S",
        "C",
    }
)

TOOL_CODES = {"reserve": 0.0, "query": 1.0, "transfer": 2.0}
PREDICATE_CODES = {"valid": 0.0, "invalid": 1.0, "changed": 2.0}
COST_RATIOS = (0.5, 0.75, 1.0, 1.25, 2.0)

# 开发 400 条/族，校准 200 条/族，留出 100 条/族。种子区间不重叠。
FAMILIES: dict[str, dict] = {
    "F1": {"split": "dev", "seed_start": 1000, "n": 400, "fields": (1, 2), "replan_scale": 0.6, "timing": 0.25, "arrival": "steady", "rate": 1.0, "tools": ("reserve",)},
    "F2": {"split": "dev", "seed_start": 1000, "n": 400, "fields": (3, 5), "replan_scale": 1.0, "timing": 0.50, "arrival": "steady", "rate": 1.0, "tools": ("reserve",)},
    "F3": {"split": "cal", "seed_start": 2000, "n": 200, "fields": (6, 10), "replan_scale": 1.6, "timing": 0.80, "arrival": "steady", "rate": 1.0, "tools": ("reserve",)},
    "F4": {"split": "cal", "seed_start": 2000, "n": 200, "fields": (3, 5), "replan_scale": 1.0, "timing": 0.50, "arrival": "bursty", "rate": 1.0, "tools": ("reserve",)},
    "F5": {"split": "test", "seed_start": 3000, "n": 100, "fields": (12, 20), "replan_scale": 1.0, "timing": 0.50, "arrival": "steady", "rate": 1.0, "tools": ("reserve",)},
    "F6": {"split": "test", "seed_start": 3000, "n": 100, "fields": (3, 5), "replan_scale": 2.5, "timing": 0.50, "arrival": "steady", "rate": 1.0, "tools": ("reserve",)},
    "F7": {"split": "test", "seed_start": 3000, "n": 100, "fields": (3, 5), "replan_scale": 1.0, "timing": 0.90, "arrival": "shifted", "rate": 0.6, "tools": ("reserve", "query")},
    "F8": {"split": "test", "seed_start": 3000, "n": 100, "fields": (3, 5), "replan_scale": 1.0, "timing": 0.50, "arrival": "steady", "rate": 1.0, "tools": ("transfer",)},
}

ARRIVAL_FACTOR = {"steady": 1.0, "bursty": 2.5, "shifted": 0.6}
