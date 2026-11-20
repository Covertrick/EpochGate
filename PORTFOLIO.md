# EpochGate 面试演示

快速启动命令见 [README](README.md)。演示前各跑一次，留好两个 HTML 报告。

## 30 秒介绍

> 我实现了一个 Python／SQLite 工具执行控制面。工具调用拿到资格以后，业务状态仍可能变化，写入以后进程也可能来不及记录回执。我用 fencing token 在提交时核对资格，并用幂等键和目标侧回执处理重复调用和中断恢复。我可以现场演示两种故障及其逐事件记录。

## 三分钟演示

1. **前 40 秒** — 跑 `stale-write`，指出资格变更、旧尝试被撤销、新尝试成功提交。
2. **接下来 80 秒** — 跑 `crash-recovery`，打开生成的 HTML。按时间线说明实际效果、进程退出和回执持久化的顺序，再解释为什么重复恢复只重放结果。
3. **最后一分钟** — 展示一条终态正确、早期效果实际无效的轨迹。说明发布资格、效果证据与业务有效性各自回答的问题。 [效果历史实验](experiments/effect_history/README.md)

## 常见追问

| 追问 | 回答时打开 |
| --- | --- |
| 状态变化未进入控制面时，旧 token 能做什么？ | [资格与提交](packages/runtime/src/epochgate_runtime/ledger.py) |
| 同一个幂等键，提交不同字节时如何处理？ | [发布流程](packages/runtime/src/epochgate_runtime/files.py)及[重放与冲突测试](packages/runtime/tests/test_measurements.py) |
| 文件和 SQLite 之间的崩溃窗口如何处理？ | [文件恢复流程](packages/runtime/src/epochgate_runtime/files.py) |
| 为什么增加 Claim 没有改善受测业务结果？ | [恢复义务的强对照](experiments/recovery_obligation/README.md) |

## 简历表述

设计并实现面向 Agent 工具调用的 Python／SQLite 执行控制面，支持提交资格校验、幂等发布和崩溃对账。通过真实进程中断验证从目标端恢复回执及重复恢复行为。
