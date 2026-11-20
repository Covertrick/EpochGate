# Week 5 独立重算（第二遍）

统计日期 2026-11-20。数字从 `week5/experiments/replay_joint.csv`、`week5/experiments/replay_manifest.json`、`week5/semantic/ablation_results.csv`、`week5/semantic/toctou_matrix.csv`、`week5/semantic/rsr_truth_table.csv`、`week5/proposals/generator_comparison.csv` 和 `week5/proposals/comparison_thresholds.json` 重新算出。没有把 `week5/architecture/implementation_report.md`、`week5/experiments/coverage_report.md` 或 `week5/semantic/performance_report.md` 的汇总当作输入。

这不是投稿建议。新颖性维持 `week5/novelty/novelty_assessment.md` 已经写下的 **NOVELTY_NOT_ESTABLISHED**。本文不重开这个问题。

## 已确认

- 回放样本是 24 个提案-场景簇、96 个格子。每个簇有 Direct、Admission、Claim、Outbox 各一格，`attempt_id` 都是 `r01`。系统工程师说的 24 个簇、96 个格子与此相同。
- 主终点是 Claim 减去 Direct 的过期权威接纳。主编码 `UNKNOWN_AS_ZERO` 下，配对差是 −11/24 = −0.458333。95% 簇 bootstrap 区间是 [−16/24, −6/24] = [−0.666667, −0.250000]。种子 20261120，重抽 4000 次，百分位下标 99 与 3899。这不是 Wilson 区间。
- 权威字段没有 `UNKNOWN`。`UNKNOWN_AS_FAILURE` 和 `UNKNOWN_EXCLUDED` 与主编码是同一组数，簇数仍是 24。
- 11 个不一致簇全部是 Direct 为 1、Claim 为 0。Claim、Admission、Outbox 的过期权威接纳都是 0/24。
- `ablation_results.csv` 按臂加总的行标志与工程师给出的 R0、R1、R2 数字一致。条件 A 上，R1 的 10 次尝试仍同时记着陈旧发布和无效发布；R2 的 10 次尝试都是 `WORLD_VERSION_MISMATCH`，效果没有落地。
- 回放 96 行和消融 159 行的 `process_kill` 都是 false。这批没有操作系统杀死。
- 下面列出的散列核对没有发现不一致。`archived_joint.csv` 的 SHA256 是 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`。

## 未确认

- 结果不推广成 agent-general。清单里 24 条记录的 `external_llm_called` 都是 false，`calls_make_world` 都是 false。模型接口按本轮说明为 NOT_AVAILABLE，本次没有调用模型。
- 掉电耐久没有被测量。`power_loss_claim` 在上述 96 行和 159 行上都是 false。
- Claim 对 Admission、Claim 对 Outbox 的过期权威配对差都是 0。这个 0 只描述这 24 个簇。它没有建立等价，也没有建立 Claim 优于 Outbox 或 Admission。
- Week 2 到 Week 4 维持 PARTIALLY_SHARED。`week5/architecture/mechanism_independence.json` 里的 INDEPENDENT 只写在新的 week5 runtime 提交、持久化、状态转移、恢复和确认这一范围。本次没有重审那段代码，也不用配对差为这个标签提供证据。
- 版本围栏的原子性没有被结果表证明。表里能看见的是条件 A 的拒绝计数；`ATOMIC_SQLITE_TRANSACTION` 是写在 R2 行上的标签。
- 生成器比较在当前阈值下会得到 DIFFERS，但阈值是否先于比较结果冻结无法证明。该结论标为 UNVERIFIED_THRESHOLD。
- 预注册的完成终点和未解决终点缺少字段，不能计算。被取代终点的证据状态列也不在回放表里。
- 目标账本只能核对 1 条回放行。其余 95 条回放行和 159 条语义行的声称效果计数未能核对。没有发现与幸存账本矛盾的行。
- 无效语义发布的区间是否整体低于 0，依赖 `UNKNOWN_AS_ZERO`。把它当成失败时，Claim 对 Direct 的区间跨过 0。

## 样本与区间

簇键是 `proposal_id|scenario_id`，按字典序进入抽样列表。配对差是同一簇里 Claim 的格子指示减去对照的格子指示。点估计是这些簇配对差的平均。每次比较都用 `random.Random(种子)` 重新开始。有放回地抽取与簇数相同的簇，计算平均配对差。`B = 4000`。排序后取 `floor(0.025 × 3999) = 99` 和 `floor(0.975 × 3999) = 3899`。补充的 McNemar 精确双侧概率不决定终点。

回放没有第二轮尝试。语义矩阵里的 `r01` 到 `r10` 是同一夹具的重复，加进臂合计，不当成新的独立提案。

主编码 `UNKNOWN_AS_ZERO`：该指标的 UNKNOWN 格子记 0，并留在分母里。`UNKNOWN_AS_FAILURE` 把该指标的 UNKNOWN 记 1，只作敏感性。`UNKNOWN_EXCLUDED` 在任一侧为 UNKNOWN 时去掉整个簇，只作敏感性。过期权威的指示只在 `authority_decision == STALE_ACCEPTED` 时为 1。无效语义发布的指示只在 `semantic_validity == INVALID` 时为 1。

## 主终点

过期权威接纳在 96 格里是 11 格，全部在 Direct。Admission、Claim、Outbox 都是 0/24。权威取值只有 `STALE_ACCEPTED` 11 格和 `NO_STALE_ACCEPTANCE_RECORDED` 85 格。

| 比较 | 政策 | 配对差 | 95% 下界 | 95% 上界 | 簇 | 不一致 | 仅 Claim | 仅对照 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Claim vs Direct | 三种政策相同 | −0.458333 | −0.666667 | −0.250000 | 24 | 11 | 0 | 11 |
| Claim vs Admission | 三种政策相同 | 0.000000 | 0.000000 | 0.000000 | 24 | 0 | 0 | 0 |
| Claim vs Outbox | 三种政策相同 | 0.000000 | 0.000000 | 0.000000 | 24 | 0 | 0 | 0 |

Claim vs Direct 的差是 −11/24。11 个不一致簇是 `w5c01` 至 `w5c06`（W5-AUTH-01 至 W5-AUTH-06）和 `w5c19` 至 `w5c23`（W5-SUC-01 至 W5-SUC-05）。`w5c24`、六个 semantic 簇和六个 recovery 簇没有过期权威接纳。这 11 个 1 都落在名为 Direct 的格子上。种子 20261120 到 20261124 的区间端点都是 −0.666667 和 −0.250000，没有移动。

补充的 McNemar 精确双侧概率是 2/2048 = 0.0009765625。它不决定主终点。

区间整体低于 0，表示在这 24 个已观察簇的重抽里，Claim 的过期权威指示低于 Direct。正值才会表示 Claim 更常接纳过期权威。bootstrap 重抽的是这 24 个簇，不把结果推广到其他模型或其他生成器。

Claim 对 Admission、Claim 对 Outbox 的配对差是 0，区间是 [0.000000, 0.000000]，不一致簇是 0。这是这 24 个簇上的描述。等价性没有被检验。Claim 优于 Outbox，或优于 Admission，也没有被这个 0 支持。Week 2 到 Week 4 仍是 PARTIALLY_SHARED。

## 次要终点与 UNKNOWN 敏感性

无效语义发布在 `UNKNOWN_AS_ZERO` 下是 19/96。四条路径分别是 Direct 10/24、Admission 3/24、Claim 3/24、Outbox 3/24。`semantic_validity == UNKNOWN` 的格子是 45。semantic 家族四条路径的 INVALID 与 VALID 计数相同，都是 3 与 3，这一家族内部的配对差是 0。−7/24 来自 7 个簇：Direct 为 INVALID，Claim 为 UNKNOWN，主编码把 UNKNOWN 记为 0。这 7 个簇是 `w5c01`、`w5c05`、`w5c19`、`w5c20`、`w5c21`、`w5c22`、`w5c23`。

| 比较 | 政策 | 配对差 | 95% 下界 | 95% 上界 | 簇 | 去掉的簇 | 不一致 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Claim vs Direct | UNKNOWN_AS_ZERO | −0.291667 | −0.458333 | −0.125000 | 24 | 0 | 7 |
| Claim vs Admission | UNKNOWN_AS_ZERO | 0.000000 | 0.000000 | 0.000000 | 24 | 0 | 0 |
| Claim vs Outbox | UNKNOWN_AS_ZERO | 0.000000 | 0.000000 | 0.000000 | 24 | 0 | 0 |
| Claim vs Direct | UNKNOWN_AS_FAILURE | −0.083333 | −0.333333 | 0.166667 | 24 | 0 | 10 |
| Claim vs Admission | UNKNOWN_AS_FAILURE | −0.250000 | −0.416667 | −0.083333 | 24 | 0 | 6 |
| Claim vs Outbox | UNKNOWN_AS_FAILURE | 0.000000 | 0.000000 | 0.000000 | 24 | 0 | 0 |
| Claim vs Direct | UNKNOWN_EXCLUDED | 0.000000 | 0.000000 | 0.000000 | 7 | 17 | 0 |
| Claim vs Admission | UNKNOWN_EXCLUDED | 0.000000 | 0.000000 | 0.000000 | 7 | 17 | 0 |
| Claim vs Outbox | UNKNOWN_EXCLUDED | 0.000000 | 0.000000 | 0.000000 | 13 | 11 | 0 |

`UNKNOWN_AS_ZERO` 下，Claim vs Direct 的种子 20261120、20261123、20261124 下界是 −0.458333，种子 20261121 与 20261122 的下界是 −0.500000。上界都是 −0.125000。点估计保持 −0.291667。下界移动了 1/24。`UNKNOWN_AS_FAILURE` 的区间是 [−0.333333, 0.166667]，跨过 0。因此“无效语义发布的区间整体低于 0”只在主编码下成立，对把 UNKNOWN 记为 1 不稳健。`UNKNOWN_EXCLUDED` 的 0 只属于两侧都不是 UNKNOWN 的簇。

重复逻辑效果在回放 96 格上都是 0，三条配对差都是 0.000000，区间都是 [0.000000, 0.000000]。消融 159 行的 `duplicate_effect` 之和也是 0。这是这两张表的计数。它没有建立等价，也没有建立掉电下的恰好一次。

被取代：回放表没有 `evidence_state`。已有字段里也不含 `SUPERSED`。预注册定义中的证据状态这一支无法核对，所以不能把该终点确认为 0。字段扫描的配对差是 0，状态是 PARTIAL_UNCHECKED。消融表上的 `semantically_superseded` 属于另一批夹具，见下一节，不拿来填这个终点。

完成：预注册要求 `runner_status == COMPLETED`。回放表没有这一列。终点 NOT_COMPUTABLE。`arm_outcome` 的 `ACCEPTED`、`WITHHELD`、`NO_DURABLE_STATE` 不代入完成。

未解决：预注册要求 `adjudication == UNRESOLVED`，或 `terminal_outcome == UNKNOWN`，或 `joint_observed` 为假。这三列都不在回放表里。终点 NOT_COMPUTABLE。另记描述，不充当该终点：`semantic_validity` 为 UNKNOWN 的格子 45/96，`authority_decision` 为 UNKNOWN 的格子 0/96，`stop_correctness` 为 UNKNOWN 的格子 60/96。

## R0、R1、R2 与条件 A

语义矩阵在 `ablation_results.csv`，159 行。`rsr_truth_table.csv` 的 9 行是其中 `fault_id == NONE` 的行，`toctou_matrix.csv` 的 150 行是其余行，两边的 `run_id` 不重叠，并起来等于消融表。每臂 53 行。8 个夹具：BASE 三个提案各 1 次尝试，TOCTOU A 到 E 各 1 个提案、10 次尝试。下面的数字是行标志之和，10 次尝试都算进行里。无效和陈旧可以落在同一行。

| 臂 | 行 | 无效 | 陈旧 | 重复 | 语义取代 | 权威取代 | UNKNOWN 停止 | CORRECT_STOP | 成功完成 | 围栏拦住 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| R0 | 53 | 21 | 20 | 0 | 0 | 0 | 0 | 0 | 31 | 0 |
| R1 | 53 | 10 | 10 | 0 | 11 | 0 | 1 | 1 | 31 | 0 |
| R2 | 53 | 0 | 0 | 0 | 11 | 0 | 11 | 1 | 21 | 20 |

与工程师的逐项对照：24 簇与 96 格一致。R0 的无效 21、陈旧 20、重复 0、取代 0、UNKNOWN 0、停止 0、成功 31 一致。R1 的无效 10、陈旧 10、取代 11、UNKNOWN 1、停止 1、成功 31 一致。R2 的无效 0、陈旧 0、取代 11、UNKNOWN 11、停止 1、成功 21 一致。口头报告没有单列 R1 和 R2 的重复；重算都是 0。口头的“取代”与 `semantically_superseded` 一致。`authority_superseded` 三臂都是 0，没有并进上面的取代数。没有需要另写的差额。

这些和里含着重复。R0 的无效 21 来自 BASE 1 行、条件 A 的 10 行和条件 E 的 10 行。R0 的陈旧 20 来自条件 A 和条件 E 各 10 行。R1 的无效 10 和陈旧 10 全部来自条件 A。

条件 A 是夹具 `toctou-a`，10 次尝试，不是 10 个独立提案。RSR 单独没有消掉这个窗口。

- R1：10/10 行 `invalid_publication = 1`，10/10 行 `stale_publication = 1`，10/10 行效果落地且 `published_new = 1`，`arm_outcome` 都是 `ACCEPTED`，`rsr_blocked = 0`，围栏没有调用。陈旧且无效的发布仍在。
- R2：10/10 行 `fence_result = WORLD_VERSION_MISMATCH`，`fence_blocked = 10`，`effect_landed = 0`，`published_new = 0`，无效 0，陈旧 0。这是 10 次观察到的拒绝。

条件 E 是另一个窗口：R1 和 R2 各有 10 行 `SEMANTICALLY_SUPERSEDED`，无效和陈旧都是 0；R0 仍是 10 行无效且陈旧。条件 E 说明发布前已经换掉的字节可以被 RSR 拦住。它不取消条件 A 上 R1 仍然发布的那 10 行。

## 版本围栏的原子性

条件 A 的拒绝计数来自结果表，已写在上一节。`version_fence_status` 在全部 53 条 R2 行上都是 `ATOMIC_SQLITE_TRANSACTION`，在另外 106 行上都是 `NOT_IN_ARM`。这个字符串按臂写成常数。结果表没有 journal 模式、提交边界或事务中断前后的记录。

因此，这批数据支持的是条件 A 上 10 次 `WORLD_VERSION_MISMATCH` 且效果未落地。原子性主张不能由这些行证明。它留在本次没有重审的接收端代码上。`power_loss_claim` 仍是 false，这批也不测量掉电窗口。

## 生成器阈值

比较按两份登记重算。生成器 1 是 `week2/proposal_registry.jsonl` 的 15 条加上 `week3/proposal_registry.jsonl` 的 12 条，合计 27 条，提案号没有重复，也与 Week 5 的 24 个提案号不相交。生成器 2 是冻结清单里的 24 个簇。八个轴在四舍五入到 6 位小数后与 `generator_comparison.csv` 一致。

| 轴 | 差 | 当前阈值 | 越过 |
| --- | ---: | ---: | ---: |
| semantic_class_tv | 0.250000 | 0.20 | 是 |
| sku_dest_alias_rate | 0.875000 | 0.20 | 是 |
| payload_length | 0.095779 | 0.25 | 否 |
| idempotency_uniqueness | 0.000000 | 0.05 | 否 |
| oracle_adjudicable_rate | 0.000000 | 0.05 | 否 |
| world_state_tv | 0.458333 | 0.20 | 是 |
| authority_tv | 0.125000 | 0.20 | 否 |
| successor_tv | 0.055556 | 0.20 | 否 |

载荷长度的相对均值差是 0.095779。两组长度范围是 25–26 与 0–28，范围不相交为假，所以该轴没有越过。CSV 每一行的 `conclusion` 都写成 DIFFERS，因为那一列是整批结论，不是单轴结论。按文件里的规则，三个轴越过就会使整批成为 DIFFERS。

这个 DIFFERS 标为 **UNVERIFIED_THRESHOLD**。`comparison_thresholds.json` 自述 `frozen_before_comparison: true`。比较脚本只读取该文件。两份文件都还没有 git 提交。阈值文件的创建时间和修改时间都是 2026-11-20 23:40:41，比较 CSV 都是 2026-11-20 23:43:28。仓库里没有另一份先于比较结果保存的阈值散列。这些材料不能证明阈值在看见比较结果之前已经冻结。

## 机制标签

`mechanism_independence.json` 的 `decision` 是 INDEPENDENT，`decision_scope` 写的是新的 week5 runtime 提交、持久化、状态转移、恢复和确认，`historical_week2_to_week4` 是 PARTIALLY_SHARED。本次统计没有重审这些函数。INDEPENDENT 只按该文件自己的范围引用，不扩大到 Week 2–4，也不从 Claim 与 Outbox 的零配对差推出来。

## 归档

`docs/开发文档/week1-historical-audit/archived_joint.csv` 的 SHA256 是 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`。写出本文件之前和本文件的数字固定之后各核了一次，两次相同。

完整性清单、未能核对的 `run_id` 和散列核对范围在 `week5/audit/week5_data_integrity.md`。估计表在 `week5/statistics/week5_recomputed_results.csv` 和 `week5/statistics/week5_bootstrap_results.csv`。
