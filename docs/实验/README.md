# 实验与证据

修改运行时时，先从协议找到要验证的行为，再到相应的实现、复现步骤和结果核对。运行时的模块和测试入口见 [仓库布局](../现行/架构/仓库布局.md)。

| 要核对的行为 | 协议与实施 | 结果与复现 |
| --- | --- | --- |
| 状态推进后是否拒绝旧凭据 | [状态机](../现行/协议/状态机.md)、[RunLedger](../../packages/runtime/src/epochgate_runtime/ledger.py) | [E4 重复批次说明](E4三问与重复批次.md)、[结果汇总](../../results/runtime/correctness/e4-repeat/summary.json) |
| 副作用在故障后能否确认或恢复 | [副作用](../现行/协议/副作用.md)、[恢复](../现行/协议/恢复.md)、[恢复实现](../../packages/runtime/src/epochgate_runtime/recovery.py) | [正确性修复](正确性修复与补充实验.md)、[补实验协议](补实验协议.md)、[补实验结果](补实验结果.md)、[运行时复现](../现行/复现/运行时.md) |
| 取消后何时释放槽位 | [取消](../现行/协议/取消.md)、[调度](../现行/协议/调度.md)、[调度器](../../packages/runtime/src/epochgate_runtime/scheduler.py) | [故障矩阵](../../results/runtime/fault_matrix.json)、[容器测试](../现行/复现/容器测试.md) |
| 在线调用的执行开销 | [在线对照设计](../现行/基准/在线对照.md)、[测量脚本](../../scripts/runtime_online_arms.py) | [B0/B1/B2 汇总](../../results/runtime/online_b0b1b2.json) |

## 独立确认与后续审计

[实验方法](实验方法.md) 说明各批次的边界和测量方法。[可靠性发现与投稿判断](可靠性发现与投稿判断.md) 记录当时可支持的结论。

| 批次 | 协议 | 结果 |
| --- | --- | --- |
| M4–M6 | [独立确认协议](M4-M6独立确认协议.md) | [结果与审计](M4-M6结果与审计.md)、[M6 汇总](../../results/runtime/correctness/m6/summary.json) |
| M4–M6 v2 | [编码确认协议](M4-M6-v2协议.md) | [结果与验收](M4-M6-v2结果与验收.md)、[阻塞审计](M4-M6-v2-blocked审计.md)、[v2 M6 汇总](../../results/runtime/correctness/v2-m6/summary.json) |

历史证据审计从独立确认批的原始记录重建证据。后续实验在此基础上展开冻结提案、故障回放、机制比较和语义检查。每个阶段的证据适用范围见对应报告。

| 阶段 | 读报告或查数据 |
| --- | --- |
| 历史证据审计 | [审计报告](../../experiments/historical_audit/audit_report.md)、[联合观察](../../experiments/historical_audit/archived_joint.csv) |
| 冻结提案对照 | [实验报告](../../experiments/frozen_proposals/week2_report.md)、[更正](../../experiments/frozen_proposals/week2_correction_report.md) |
| 故障注入回放 | [阶段报告](../../experiments/fault_replay/week3_report.md)、[联合数据](../../experiments/fault_replay/week3_replay_joint.csv) |
| 崩溃恢复扩展 | [崩溃证据](../../experiments/crash_recovery/crash_evidence_extended.md)、[统计稳健性](../../experiments/crash_recovery/statistical_robustness.md) |
| 机制与语义验证 | [基线状态](../../experiments/mechanism_validation/baseline_status.md)、[数据完整性](../../experiments/mechanism_validation/audit/week5_data_integrity.md) |

命令、路径迁移和已知证据缺口见 [分阶段实验复现](../../experiments/README.md)。其他历史脚本见 [脚本索引](../../scripts/README.md)，写作引用见 [论文材料](../现行/论文/README.md)。

[提交历史配对实验](../../experiments/effect_history/README.md) 提供逐格目标账本、等预算对照和投稿判断。

[EpochGate 配置选择实证研究计划](EpochGate配置选择实证研究计划.md) 记录通知交叉设计、评价阶梯、确认实验和投稿条件。

[配置选择先导结果](../../experiments/configuration_selection/README.md) 记录验收、96 格配对证据与 G2 停止判决。

[履约发布恢复义务实验](../../experiments/recovery_obligation/README.md) 记录历史案例重放、持久意图强对照和 20 格复核结果。

[Git 工具恢复中的效果归属](../../experiments/git_effect_evidence/README.md) 记录真实 Git 条件更新、证据可见性与条件重试的先导结果。

[Git 负结果技术报告](../../experiments/git_effect_evidence/technical-report.md) 归档普通推送控制与外部工作流筛查。[显式撤回工具先导](../../experiments/package_withdrawal/README.md) 记录包发布筛查和真实 Twine 的本地重试结果。

[论文实验索引](论文实验索引.md) 记录稿件使用的批次与引用边界。
