# Week 3 进程崩溃注入

这是受控的进程崩溃注入：父进程确认检查点标记后，用 Windows `TerminateProcess` 结束子进程。子进程内存随之消失，另一个新进程重新打开 SQLite 和文件系统再做核对。

这不是掉电耐久性。这不是失败概率。矩阵里的重复次数是预先固定的重复，不是从随机崩溃里估计出来的比率。

Week 2 里标成 CRASHED 的格子是进程内故障 `IN_PROCESS_FAULT`，不是这里的操作系统级结束，也不是掉电。本矩阵的 `process_kill` 为 true，`power_loss_claim` 为 false。不声称任意 HTTP 恰好一次，不声称跨库原子性。

- 行数：80。检查点 4，路径 2（Claim、Outbox），每格重复 10 次。
- 复用的冻结提案：p10, p11。字节文件没有重写。提案合同匹配行数：80 / 80。
- 稳定复现的证据状态：CONFIRM_TARGET, PUBLISH_SEALED, REPLAY_RECEIPT, UNKNOWN。共 4 个。
- Claim 与 Outbox 使用同一个子进程代码路径、同一张 `kill_ledger`、同一套接收端文件条件。路径名只是列标签。恢复不读取子进程内存里的载荷。

## 每个检查点的稳定状态

| 检查点 | 路径 | 证据状态 | 是否单一 |
| --- | --- | --- | --- |
| after_claim_before_sealed | Claim | UNKNOWN | 是 |
| after_claim_before_sealed | Outbox | UNKNOWN | 是 |
| after_sealed_before_replace | Claim | PUBLISH_SEALED | 是 |
| after_sealed_before_replace | Outbox | PUBLISH_SEALED | 是 |
| after_replace_before_receipt | Claim | CONFIRM_TARGET | 是 |
| after_replace_before_receipt | Outbox | CONFIRM_TARGET | 是 |
| after_receipt | Claim | REPLAY_RECEIPT | 是 |
| after_receipt | Outbox | REPLAY_RECEIPT | 是 |

## 证据状态的含义

- `UNKNOWN`：只有未密封的 claim，没有可发布的字节。核对不发明载荷。
- `PUBLISH_SEALED`：密封字节已落盘，目标文件还没有。新进程从 SQLite 读出密封字节再发布。
- `CONFIRM_TARGET`：目标文件的摘要已经等于密封摘要，回执还没有。新进程只确认，不重写。
- `REPLAY_RECEIPT`：回执和目标都已落盘且摘要一致。新进程重放回执，不再发布一次。
- `CONFLICT`：密封摘要与目标摘要同时存在且不相等。本研究的四个一致检查点不把 CONFLICT 当作稳定结果；状态本身仍由核对器实现。

语义 oracle 使用 Week 2 的 `semantics_oracle` 1.0.0 和 `predicate.fulfillment.v1`，评分世界是该提案冻结的 `world_after`。p10 与 p11 的崩溃日程不改变世界，所以发布后的语义结果是 VALID；未发布时物理字节缺失，语义结果是 UNKNOWN。UNKNOWN 不是 FAILURE。
