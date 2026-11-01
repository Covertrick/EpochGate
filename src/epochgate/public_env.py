"""第二套公开环境的接入闸门。

Gaia2 的场景是多应用异步用户模拟，动作粒度不是本仓库的
``observe`` / ``commit``。RealtimeGym 以时间或 token 预算推进环境，
同样不提供带版本的条件提交。两边都不把源码拷进仓库，也不报告榜单名次。
"""

from __future__ import annotations


_GAIA2_REASON = (
    "Gaia2 场景是多应用异步交互，官方验证器绑定 ARE 的应用状态，"
    "不是带 source_version 的 observe/commit。"
    "接入需要整套 ARE 运行时，许可证也不允许把场景拷进本仓库。"
    "因此不实现 EpochGate-Gaia2-Subset，也不报告官方榜单。"
)

_REALTIME_REASON = (
    "RealtimeGym 用时间或 token 预算推进环境，任务动作不是库存式条件提交。"
    "官方环境不在本仓库依赖里，本轮不安装、不改编判分，也不编造跨环境排序。"
)


def second_environment_status() -> dict[str, object]:
    """报告第二环境能不能接到当前适配器。

    输入：无。
    输出：两个环境的可用性和原因。``ranking`` 始终为空。
    副作用：无。不导入外部评测包。
    失败：无。
    """
    return {
        "gaia2": {"available": False, "reason": _GAIA2_REASON},
        "realtimegym": {"available": False, "reason": _REALTIME_REASON},
        "ranking": None,
    }
