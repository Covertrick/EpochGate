"""开发集和冻结配置。只解析本仓库用到的标量和方括号列表。"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


@dataclass
class ExperimentConfig:
    """一次对照实验的冻结参数。测试集只读这份配置。"""

    window_s: float = 60.0
    stock: int = 3
    max_inflight: int = 1
    refresh_budget: int = 8
    min_refresh_interval_s: float = 0.05
    service_prior_s: float = 0.0
    kappa: float = 1.0
    epsilon: float = 0.05
    fair_weight: float = 1.0
    fair_every_n: int = 4
    lease_ttl_s: float = 30.0
    lease_strict_s: float = 5.0
    lease_loose_s: float = 30.0
    observe_cost_s: float = 0.05
    risk_lambda: float = 0.5
    exogenous_n: int = 3
    dev_seeds: tuple[int, ...] = (1, 2, 3)
    test_seeds: tuple[int, ...] = (11, 12, 13)
    grid_interval: tuple[float, ...] = (0.0, 0.05)
    grid_weight: tuple[float, ...] = (0.0, 1.0)
    grid_fair_every_n: tuple[int, ...] = (0, 4)

    def overlaps_dev_and_test(self) -> bool:
        """开发种子和测试种子是否相交。

        输入：无。
        输出：有相同种子时为真。
        副作用：无。
        失败：无。
        """
        return bool(set(self.dev_seeds) & set(self.test_seeds))


def repo_root() -> Path:
    """仓库根目录。

    输入：无。
    输出：包含 ``pyproject.toml`` 的目录。
    副作用：无。
    失败：无。
    """
    return Path(__file__).resolve().parents[2]


def load_config(path: str | Path | None = None) -> ExperimentConfig:
    """读取 YAML 子集并覆盖默认配置。

    输入：配置路径。为空时读取 ``configs/frozen.yaml``。
    输出：``ExperimentConfig``。
    副作用：只读文件。
    失败：文件缺失，或出现未知字段时抛出 ``ValueError``。
    """
    target = repo_root() / "configs" / "frozen.yaml" if path is None else Path(path)
    raw = _parse_flat(target.read_text(encoding="utf-8"))
    known = {item.name for item in fields(ExperimentConfig)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"unknown config fields: {', '.join(unknown)}")
    values: dict[str, Any] = {}
    for name in known:
        if name not in raw:
            continue
        default = getattr(ExperimentConfig(), name)
        values[name] = _coerce(raw[name], default)
    return ExperimentConfig(**values)


def choose_frozen(candidates: list[dict[str, float]]) -> dict[str, float]:
    """在开发集候选里选一组参数。

    输入：每项含 ``cohort``、``refresh_count``、``min_refresh_interval_s``、``fair_weight``、``fair_every_n``。
    输出：选中的那一项。
    副作用：无。
    失败：候选为空时抛出 ``ValueError``。
    """
    if not candidates:
        raise ValueError("dev grid is empty")
    return max(
        candidates,
        key=lambda row: (
            float(row["cohort"]),
            -float(row["refresh_count"]),
            float(row["fair_every_n"]),
            float(row["fair_weight"]),
        ),
    )


def _parse_flat(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        root[key.strip()] = _parse_value(value.strip())
    return root


def _parse_value(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part.strip()) for part in inner.split(",")]
    return _scalar(value)


def _scalar(value: str) -> Any:
    if value == "":
        return None
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _coerce(value: Any, default: Any) -> Any:
    if isinstance(default, tuple):
        return tuple(value if isinstance(value, list) else [value])
    if isinstance(default, float):
        return float(value)
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)
    return value
