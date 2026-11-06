# EpochGate 2.0

日期：2026-11-06。这是一条新的测量平台，不覆盖 [项目开发文档2.0.md](项目开发文档2.0.md)。UCR、4.0、5.0、RRM 的代码和结果留在原路径。

DSH 0.2.2-rc.1 跑 Agent。`deepseek-flash` 做推理，并记录调用日期和响应里的模型标识。Python 选任务、冻结协议和评分。真实 Git 历史只在 Linux 容器里重放。结论限定在这个模型和这个 Harness。

## 三个节点

- 数据：至少五个来源可追溯、能在基线上重放、变更后仍可解决、并且有独立评分的真实事件对。15 到 20 条候选只是搜索量。
- 工程：Agent 只通过隔离工具完成一条真实 Clean 任务，并留下 Session、Git 和容器状态都在内的 checkpoint。插件能导入，或 API 返回 `API_OK`，都不算完成。
- 论文：错误要排除 Clean 能力不足、变更应用失败、后状态评分不可靠、工具结果与文件不一致、标注错误。相对 SWE-Shift（arXiv:2610.32196）还要有新的解释维度。

工程按本文件交付。论文数字只来自后来的实验。

## 阶段

M-1 和 M0 可以并行。M0 没有通过之前，不做 M2 以后的大规模研究开发。

| 阶段 | 要通过的东西 | 交付 |
| --- | --- | --- |
| M-1 | Linux 容器能跑；sdk profile 能启动 | 环境审计。缺管理员权限或需要重启时记阻塞 |
| M0 | 五个完整事件对 | 数据可行性报告 |
| M1 | 一条 Verified 任务走完七步 | Agent 工程最小闭环 |
| M2 | Clean 与 Changed 共享可核验前缀 | 反事实执行记录 |
| M3 | 无标准答案泄漏；三本账分开 | 评测 Harness v1 |
| M4 | 现象不是环境或评分器错误 | Pilot 报告 |
| M5 | 主比较按冻结协议执行 | 留出结果。数量由筛选决定 |
| M6 | 第三个节点成立 | 才讨论论文候选 |

M1 的七步是：加载已审核任务、按固定 SHA 和镜像恢复、sdk 启动、受控工具读写并测试、Gateway 交出候选、外部评分、保存 Session 与成本后能做 Trace Replay。

## 可部署策略

同一个 `eg-dependency-policy` 只加载四种配置：Global Reset、Re-read and Retest、Static Dependency、LLM Dependency。不处理更新的诊断臂仍用同一 Agent Loop 和预算。Oracle 只在外部评分进程，不进 DSH。

主对照是 Re-read and Retest 与 Static Dependency。Global Reset 是保守对照。

## 相关文档

- [dsh-plugins.md](../architecture/dsh-plugins.md)
- [security-boundary.md](../architecture/security-boundary.md)
- [case_admission.md](../research/case_admission.md)
- [annotation_guidelines.md](../research/annotation_guidelines.md)
- [measurement_contract.md](../research/measurement_contract.md)
- [novelty_matrix.md](../research/novelty_matrix.md)
- [EpochGate-2.0实验方案.md](../research/EpochGate-2.0实验方案.md)
