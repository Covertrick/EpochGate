# Figure 2 联合分布重建

会议 PDF 缺失，仓库里没有该图的单元格数据。本节的表来自 `results/runtime/correctness/m4-confirm/` 的 52 条联合运行，写在 `validation_report.json` 的 `joint_tables`。20 条阻塞记录不在表内。没有用边缘相乘去填这 20 格。

## 终态 × 权威决定

| 终态 | 权威决定 | 运行数 |
| --- | --- | --- |
| MATCH | NO_STALE_ACCEPTANCE_RECORDED | 10 |
| MATCH | STALE_ACCEPTED | 6 |
| MISMATCH | NO_STALE_ACCEPTANCE_RECORDED | 32 |
| MISMATCH | STALE_ACCEPTED | 4 |

合计 52。过期接纳共 10，全部出现在 S1：S1 上 STALE_ACCEPTED 10、NO_STALE_ACCEPTANCE_RECORDED 10；S2 的 16 行与 S3 的 16 行都是 NO_STALE_ACCEPTANCE_RECORDED。这是字段计数，不是因为故障标签 S1 被直接写成过期接纳。S2、S3 的场景标签没有被当成权威决定。

若把 20 条阻塞行的 `task_terminal_match: false` 算进终态边缘，MISMATCH 会从 36 变成 56。该数不是联合分布，本重建不采用。

## 终态 × 物理效果

| 终态 | 物理效果 | 运行数 |
| --- | --- | --- |
| MATCH | CONFIRMED_EFFECT | 16 |
| MISMATCH | ABSENT | 14 |
| MISMATCH | NOT_LEGAL | 22 |

16 条终态匹配的运行，载荷文件哈希都等于归档 `legal_digest`。6 条“终态匹配且过期接纳”的运行，物理状态仍是 CONFIRMED_EFFECT：失败依据是 `stale_credential_accepted`，不是非法字节。

## 终态 × 恢复

| 终态 | 恢复 | 运行数 |
| --- | --- | --- |
| MATCH | NOT_APPLICABLE | 14 |
| MATCH | NOT_RECOVERED | 1 |
| MATCH | RESTORED_BYTES | 1 |
| MISMATCH | NOT_APPLICABLE | 22 |
| MISMATCH | NOT_RECOVERED | 11 |
| MISMATCH | RESTORED_BYTES | 3 |

`interrupted_recovered` 为 null 的非 S3 行记为 NOT_APPLICABLE。RESTORED_BYTES 共 4 行，都是有证据的 S3/B3。其中只有 `ful-config-c6` 的物理状态为 CONFIRMED_EFFECT；另外三行是 NOT_LEGAL。恢复为真没有自动变成 CONFIRMED_EFFECT。

## 与 m6 边缘的对照

`results/runtime/correctness/m6/summary.json` 没有联合表，只有配对统计。用未阻塞联合行重算：

- H4：S1 上五个未阻塞任务里，终态相同且过期接纳不同的是 `ful-config-c6` 与 `ful-order-c3`，2/5 = 0.4。与 `h4_same_terminal_different_safety` 一致。`ful-config-c5` 的 S1 整组阻塞，不进入这 5 个任务。
- H5：五个 S1 任务的 B1 减 B2 过期接纳差都是 1，配对数 5。与 `h5.n` 一致。
- H6：四个未阻塞 S3 任务的 B3 减 B2 `interrupted_recovered` 差都是 1，配对数 4。与 `h6.n` 一致。这个差比较的是恢复标志，不是 CONFIRMED_EFFECT。

`confirm_n` 52 等于联合运行数，也等于文件头 72 减去阻塞 20。三者一致，因为 52 条汇总行与 evidence 相同。

## 未解决

Figure 2 的图内数字无法核对。阻塞的 5 个 proposal、20 格没有效果历史。`token_was_stale` 不在 JSON 中，SQLite 账本只做了字节哈希、没有查询，所以过期接纳是归档布尔值加上接收端“已接纳”日志，不是从账本重新推导的时效。
