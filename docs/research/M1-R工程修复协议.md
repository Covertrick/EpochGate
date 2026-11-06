# M1-R 工程修复协议

日期：2026-11-06。原 M1 记录 `results/semantic-state/m1_clean.json` 保持 `passed: false`。这一轮的新结果只写到 `results/semantic-state/recovery/m1/`。

任务固定为 `astropy__astropy-12907`，基线 `d16bfe05a744909de4b27f5875fe0d4ed41ce607`。Agent 和 Scorer 使用同一个任务镜像 digest。Agent 容器不接收 API key、gold patch 或隐藏测试。

Patch 只有四类：`VALID_PATCH`、`EMPTY_PATCH`、`INVALID_PATCH`、`EXPORT_ERROR`。`__pycache__`、字节码和 `astropy/_version.py` 这种运行桩不进入 patch。

工程闭环通过的条件是：DSH 会话、模型标识、容器工具或明确的工具拒绝、合法的 patch 分类，以及外部评分器完整执行。`EMPTY_PATCH` 可以是一次失败的任务运行，仍然满足工程闭环。

真实修复样例通过的条件是：Agent 自己交出 `VALID_PATCH`，干净 Scorer 证明对应的 `FAIL_TO_PASS` 被解决。这一级失败时，不把工程闭环改成失败，也不写成论文成功率。
