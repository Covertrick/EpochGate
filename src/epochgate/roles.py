"""一次焦点事件下的工作流角色。"""

from __future__ import annotations


TARGET = "target"
SUCCESSOR = "successor"
FALSE_TARGET = "false_target"
BYSTANDER = "bystander"
RESTART = "restart"

FOCAL_ROLES = {TARGET, FALSE_TARGET}


def role_for_focal(trigger_class: str) -> str:
    """焦点工作流在事件前的角色。

    输入：``harmful`` 或 ``harmless``。
    输出：前提会被破坏时为 target，否则为 false_target。
    副作用：无。
    失败：未知触发类抛出 ``ValueError``。
    """
    if trigger_class == "harmful":
        return TARGET
    if trigger_class == "harmless":
        return FALSE_TARGET
    raise ValueError(f"unknown trigger_class: {trigger_class}")


def followup_role(trigger_class: str) -> str:
    """撤销或拒绝之后的后继角色。

    输入：触发类。
    输出：有害变化的必要后继，或无害误撤的 restart。
    副作用：无。
    失败：未知触发类抛出 ``ValueError``。
    """
    if trigger_class == "harmful":
        return SUCCESSOR
    if trigger_class == "harmless":
        return RESTART
    raise ValueError(f"unknown trigger_class: {trigger_class}")
