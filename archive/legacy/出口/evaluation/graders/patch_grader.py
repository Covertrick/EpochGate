"""外部 patch 分类。Agent 不能通过工具注册表调用这里。"""

from epochgate.eg2_patch import classify_trees, with_apply_result

__all__ = ["classify_trees", "with_apply_result"]
