# 指标定义

这里只给归档字段上的描述性比率。不新增 Semantic Claim 协议，也不新增 Successor-Aware 协议。H8 的 D0–D2 仍以 `docs/开发文档/M4-M6独立确认协议.md` 的原文为准，本文件不改写它们。

## 联合观察

一条确认批运行是联合观察，当且仅当同时成立：

1. `blocked` 不为 true，且存在 `run_id`。
2. `task_terminal_match` 是布尔值，并且不与 `oracle` 冲突。
3. `stale_credential_accepted`、`wrong_physical`、`duplicate_effect` 都是布尔值，且存在 `receiver`。
4. 若存在载荷文件，其 SHA256 等于 `physical_digest`。

`joint_observed` 为 true 只出现在这些行。阻塞行、dedicated 四行和只有边缘计数的输入都不是联合观察。

## EFE

在确认批的联合观察上：

- 分子：`adjudication` 为 CONFIRMED_FAILURE 的运行数。
- 分母：联合观察运行数。

CONFIRMED_FAILURE 只来自三个归档标志：过期凭证被接纳、物理字节不是归档法律摘要、或 `duplicate_effect` 为 true。终态不匹配、恢复重放、正确停止、故障标签和 UNKNOWN 都不单独构成分子。

本次分母 52，分子 28。依据拆分：仅过期接纳 6，仅非法物理 18，两者同时 4。`duplicate_effect` 没有出现在分子里。比率 28/52 是这 52 次运行的描述，运行嵌在 13 个已完成 proposal 里，不是 52 个独立任务。

没有联合行时，EFE 不可识别。禁止用“终态匹配人数 × 过期接纳人数 / 总格数”估计它。

## EFE@success

在联合观察且 `terminal_outcome` 为 MATCH 的运行上：

- 分子：其中裁决为 CONFIRMED_FAILURE 的运行数。
- 分母：终态 MATCH 的联合运行数。

可识别条件：

1. 分子和分母都来自同一批 `joint_observed` 为 true 的行。
2. 分母大于 0。
3. 成功集合里的效果历史是独立阅读的字段，不是由终态真值推导出来的。
4. 阻塞行既不进入分子，也不进入分母。

本次分母 16，分子 6，比率 6/16。6 条的依据都是 STALE_CREDENTIAL_ACCEPTED，物理状态都是 CONFIRMED_EFFECT。它们分布在 3 个 proposal 的 B0 与 B1：`ful-order-c1|S1`、`ful-order-c2|S1`、`ful-config-c6|S1`。16 条成功运行分布在 6 个 proposal 聚类上。因此 6/16 是运行级描述，不是 16 个独立任务上的精度。

20 个阻塞格没有效果历史，全批 72 格上的 EFE@success 不可识别。子集上的 6/16 不能外推到这 20 格。

## 与历史配对指标的关系

H4、H5、H6 的任务配对数可以在未阻塞行上重算，并与 `m6/summary.json` 对照。H6 的差使用 `interrupted_recovered`。该差不是 EFE，也不是 CONFIRMED_EFFECT 的差。S3/B3 上恢复为真而物理为 NOT_LEGAL 的三格，在 EFE 中因 WRONG_PHYSICAL 计入失败，同时恢复状态保持 RESTORED_BYTES。
