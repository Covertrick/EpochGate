# Week 3 报告

## 1. 决定与门

决定：进行 Week 3 正式簇配对比较。四道门都通过。主要结果是 authority、semantic、successor、outdated 四个分开的指标，不设复合 EFE 标题。

通过：A, B, C, D。未通过：无。

- 门 A 分离：通过。物理 `CONFIRMED_EFFECT` 或 `RESTORED_BYTES` 且语义 `INVALID` 的主比较格子 22 个。按场景：{'S13': 2, 'S14': 4, 'S15': 4, 'S16': 4, 'S17': 4, 'S18': 4}。
- 门 B 崩溃：通过。矩阵 80 行。稳定证据状态：CONFIRM_TARGET, PUBLISH_SEALED, REPLAY_RECEIPT, UNKNOWN。
- 门 C 冻结：通过。Claim/Outbox 不发布旧字节并完成合法后继、同时 Direct/Admission 发布旧字节的案例：S17:p24, S17:p25。
- 门 D 规模：通过。合法簇 27。模式错误 0。盲审行 16，占轨迹 0.1481。

## 2. 进程崩溃注入、进程内故障、掉电

B-1 是受控进程崩溃注入。父进程看见检查点标记后调用 Windows `TerminateProcess`。子进程内存消失，新进程重新打开 SQLite 和文件。`process_kill` 为 true，`power_loss_claim` 为 false。这不是失败概率。

Week 2 格子里的 CRASHED 是进程内 `FaultCut`，在本周记为 `IN_PROCESS_FAULT`。它不是操作系统级结束，也不是掉电。本周没有把这两种故障写成同一类证据。

## 3. 分离结果（门 A）

物理列在分离场景里按声明摘要解释：目标等于冻结声明，前向发布是 `CONFIRMED_EFFECT`；新进程从密封字节写回目标是 `RESTORED_BYTES`，物理列保持 `PRESENT_UNVERIFIED`。语义列是 `semantics_oracle` 对 `observed_world` 的结果。`NOT_LEGAL` 加语义 `INVALID` 仍是 Week 2 那种叠在一起的条件，不计入门 A。

| 场景 | 路径 | 物理 | 恢复 | 语义 | 崩溃证据 | 格子 |
| --- | --- | --- | --- | --- | --- | --- |
| S13 | Admission | CONFIRMED_EFFECT | NOT_APPLICABLE | INVALID | NOT_APPLICABLE | 1 |
| S13 | Claim | ABSENT | NOT_RECOVERED | UNKNOWN | UNKNOWN | 1 |
| S13 | Direct | CONFIRMED_EFFECT | NOT_APPLICABLE | INVALID | NOT_APPLICABLE | 1 |
| S13 | Outbox | ABSENT | NOT_RECOVERED | UNKNOWN | UNKNOWN | 1 |
| S14 | Admission | CONFIRMED_EFFECT | NOT_APPLICABLE | INVALID | NOT_APPLICABLE | 1 |
| S14 | Claim | PRESENT_UNVERIFIED | RESTORED_BYTES | INVALID | PUBLISH_SEALED | 1 |
| S14 | Direct | CONFIRMED_EFFECT | NOT_APPLICABLE | INVALID | NOT_APPLICABLE | 1 |
| S14 | Outbox | PRESENT_UNVERIFIED | RESTORED_BYTES | INVALID | PUBLISH_SEALED | 1 |
| S15 | Admission | CONFIRMED_EFFECT | NOT_APPLICABLE | INVALID | NOT_APPLICABLE | 1 |
| S15 | Claim | CONFIRMED_EFFECT | NOT_APPLICABLE | INVALID | CONFIRM_TARGET | 1 |
| S15 | Direct | CONFIRMED_EFFECT | NOT_APPLICABLE | INVALID | NOT_APPLICABLE | 1 |
| S15 | Outbox | CONFIRMED_EFFECT | NOT_APPLICABLE | INVALID | CONFIRM_TARGET | 1 |
| S16 | Admission | CONFIRMED_EFFECT | NOT_APPLICABLE | INVALID | NOT_APPLICABLE | 1 |
| S16 | Claim | PRESENT_UNVERIFIED | RESTORED_BYTES | INVALID | PUBLISH_SEALED | 1 |
| S16 | Direct | CONFIRMED_EFFECT | NOT_APPLICABLE | INVALID | NOT_APPLICABLE | 1 |
| S16 | Outbox | PRESENT_UNVERIFIED | RESTORED_BYTES | INVALID | PUBLISH_SEALED | 1 |

## 4. 崩溃证据（门 B）

四个检查点乘 Claim 与 Outbox，各 10 次，共 80 行。稳定状态：CONFIRM_TARGET, PUBLISH_SEALED, REPLAY_RECEIPT, UNKNOWN。
p10 与 p11 的载荷 SHA256、初始世界哈希和权威初始哈希与 Week 2 冻结登记一致：80 / 80。细节在 `crash_evidence_summary.md`。

## 5. 通知冻结（门 C）

S17 和 S18 是场景行为：冻结旧声明，不发布旧字节，再接受合法后继或正确停止。这不是新的协议名称。

| 场景 | 路径 | 发布旧字节 | 后继状态 | 语义 | 权威 | 格子 |
| --- | --- | --- | --- | --- | --- | --- |
| S17 | Admission | true | FAILED | INVALID | STALE_ACCEPTED | 2 |
| S17 | Claim | false | COMPLETED_VALID | VALID | NO_STALE_ACCEPTANCE_RECORDED | 2 |
| S17 | Direct | true | FAILED | INVALID | STALE_ACCEPTED | 2 |
| S17 | Outbox | false | COMPLETED_VALID | VALID | NO_STALE_ACCEPTANCE_RECORDED | 2 |
| S18 | Admission | true | FAILED | INVALID | STALE_ACCEPTED | 2 |
| S18 | Claim | false | CORRECT_STOP | UNKNOWN | NO_STALE_ACCEPTANCE_RECORDED | 2 |
| S18 | Direct | true | FAILED | INVALID | STALE_ACCEPTED | 2 |
| S18 | Outbox | false | CORRECT_STOP | UNKNOWN | NO_STALE_ACCEPTANCE_RECORDED | 2 |

S18 的 `CORRECT_STOP` 不是后继失败。S06 仍是 `NON_DISCRIMINATIVE`，S09 仍是 `MECHANISM_SPECIFIC_DIAGNOSTIC`，两者都不在主比较里。

## 6. 分开的样本量

- 正式簇：27。正式格子：108。
- 主比较簇：25。主比较格子：100。
- 崩溃重复行：80。这些行不在正式联合表里。
- Week 2 `PILOT_FIRST_BATCH`：15 个簇、60 个格子。只作引用，不进入本周正式样本。
- Week 1 归档的 52 行没有用来填空，归档文件没有被修改。

## 7. 预注册比较

单位是簇。估计是 Claim 减对照的平均配对差。区间是 4000 次簇 bootstrap 的 95% 百分位区间，种子 20261120。McNemar 精确 p 值只作补充。`UNKNOWN` 留在分母里，指标为 0，不记为失败。

| 比较 | 指标 | 配对差 | 95% 下界 | 95% 上界 | 簇数 | McNemar p |
| --- | --- | --- | --- | --- | --- | --- |
| Claim_vs_Direct | authority | -0.4400 | -0.6400 | -0.2400 | 25 | 0.0010 |
| Claim_vs_Direct | semantic | -0.4400 | -0.6400 | -0.2400 | 25 | 0.0010 |
| Claim_vs_Direct | successor | -0.1600 | -0.3200 | -0.0400 | 25 | 0.1250 |
| Claim_vs_Direct | outdated | -0.0400 | -0.1200 | 0.0000 | 25 | 1.0000 |
| Claim_vs_Admission | authority | -0.1600 | -0.3200 | -0.0400 | 25 | 0.1250 |
| Claim_vs_Admission | semantic | -0.2000 | -0.3600 | -0.0400 | 25 | 0.0625 |
| Claim_vs_Admission | successor | -0.1600 | -0.3200 | -0.0400 | 25 | 0.1250 |
| Claim_vs_Admission | outdated | -0.0400 | -0.1200 | 0.0000 | 25 | 1.0000 |
| Claim_vs_Outbox | authority | 0.0000 | 0.0000 | 0.0000 | 25 | 无 |
| Claim_vs_Outbox | semantic | 0.0000 | 0.0000 | 0.0000 | 25 | 无 |
| Claim_vs_Outbox | successor | 0.0000 | 0.0000 | 0.0000 | 25 | 无 |
| Claim_vs_Outbox | outdated | 0.0000 | 0.0000 | 0.0000 | 25 | 无 |

没有做这三对以外的确认性比较。Claim 与 Outbox 的配对差为 0 时，只表示这次样本里四个指标没有不一致对，不是等价检验，也不是 Outbox 更优。

## 8. 分开的 EFE 指标

下面四个比率都不相加。分母是全部主比较格子。

| 指标 | 分子 | 分母 | 比率 | 该列 UNKNOWN 格子 |
| --- | --- | --- | --- | --- |
| authority | 15 | 100 | 0.1500 | 0 |
| semantic | 48 | 100 | 0.4800 | 31 |
| successor | 16 | 100 | 0.1600 | 0 |
| outdated | 34 | 100 | 0.3400 | 31 |

主比较里至少一列主 oracle 为 UNKNOWN 的格子：31。这些格子留在分母中。

## 9. 与 Week 1、Week 2 的边界

归档文件 SHA256 为 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`。与要求的 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda` 一致：是。
Week 2 的决定仍是 CONDITIONAL_GO。本周没有改 week2 目录，没有把 60 个试点格子并进正式统计，也没有实现 Semantic Claim、Successor-Aware Reconciliation 或类似 Cordon 的任务事务。
不声称 Outbox 普遍优于其他路径，不声称任意 HTTP 恰好一次，不声称跨库原子性，不把进程崩溃说成掉电。

## 10. 复现命令

```text
.\.venv\Scripts\python.exe -m week3.kill_runner
.\.venv\Scripts\python.exe -m week3
.\.venv\Scripts\python.exe -m pytest week3/tests -q --tb=short
.\.venv\Scripts\python.exe -m pytest week2/oracle_test_suite -q --tb=short
```

统计计划 SHA256：`cdc83a6031bb5c5138d6f1815cf52e5f6496cbd4ddb6028ca9c69a25282544aa`。正式运行前写入 `statistical_plan.sha256`，本报告不改计划正文。
