# Week 4 进程崩溃恢复

这是受控的进程崩溃注入。父进程确认检查点标记之后，用 Windows `TerminateProcess` 结束子进程。子进程内存随之消失。另一个新进程重新打开 SQLite 和文件系统，再按证据谓词核对。恢复不读取子进程内存里的载荷。

这不是掉电耐久性，也不是失败概率。每一行的 `process_kill` 为 true，`power_loss_claim` 为 false。重复次数是预先固定的。不声称任意 HTTP 恰好一次，不声称跨库原子性。

路径按代码名记录。接收端崩溃走 `SharedReceiver`。中继崩溃走 `Outbox.commit`，发送使用 `SharedReceiver.accept`。重复恢复走 `week3.kill_child` 里的 `kill_ledger`，这是 Week 3 里两个路径标签实际执行的同一段子进程。RSR 消融走 `ByteClaim.commit`。本报告不把这些名字写成两套独立机制。

- 崩溃矩阵行数：60。
- 中继 `logical_effect_count` 的取值：[1]。
- 重复恢复里二次发布或重写效果文件的行：0。

## 证据状态机

符号：`sealed` 是 SQLite 里密封载荷的 SHA256，没有密封字节则为空。`target` 是 `effect.bin` 的 SHA256，文件不存在则为空。`receipt` 表示 `receipt.json` 里有回执，且回执摘要等于 `sealed`。判定只使用这些已经落盘的事实，顺序固定。

1. `CONFLICT`：`sealed` 与 `target` 都非空，并且不相等。这是同一个 effect key 上已经落盘的载荷和密封载荷不同。动作 `HOLD_CONFLICT`，不覆盖文件。
2. `REPLAY_RECEIPT`：回执存在，并且回执摘要、`sealed`、`target` 三者相等。动作 `REPLAY_STORED_RECEIPT`，不再发布一次。
3. `CONFIRM_TARGET`：`target` 等于 `sealed`，回执不在盘上。接收端已经接纳，回执丢失。动作 `CONFIRM_EXISTING_TARGET`，不重写。
4. `PUBLISH_SEALED`：`sealed` 非空，`target` 为空，回执不存在。未启用 RSR 时，动作是发布密封字节。中继检查点的动作是 `RELAY_THEN_REPLAY`：新进程把已提交的 outbox 载荷交给 `SharedReceiver`，再交一次；第二次是 replay，`accepted_count` 保持 1。启用 RSR 时，发布前先对密封字节调用 `semantics_oracle`。
5. `SEMANTICALLY_SUPERSEDED`：第 4 步的持久条件成立，且 RSR 预检为 `INVALID`。动作 `WITHHOLD_SUPERSEDED`，不发布。
6. `UNKNOWN`：`sealed`、`target`、回执都缺失。接收端没有接纳，也没有可发布的字节。动作 `WITHHOLD_UNSEALED`。RSR 预检结果不是 `VALID` 也不是 `INVALID` 时，同样返回 `UNKNOWN` 并且不发布。

`UNKNOWN` 不是失败。`CONFLICT` 不是把文件改写成密封字节。

## 接收端崩溃

被结束的是接收端进程。检查点在 `SharedReceiver.accept` 之前，以及 accept 已经把效果文件 fsync、回执还只在该进程内存里的时候。冲突检查点使用同一个 effect key，效果文件里是另一份载荷。每个检查点 10 次。恢复进程只分类，不发布，也不重写 `effect.bin`。

| 检查点 | 代码路径 | 证据状态 | 次数 | 是否单一 |
| --- | --- | --- | --- | --- |
| before_receiver_accept | SharedReceiver | UNKNOWN:10 | 10 | 是 |
| after_receiver_accept | SharedReceiver | CONFIRM_TARGET:10 | 10 | 是 |
| after_receiver_accept_conflict | SharedReceiver | CONFLICT:10 | 10 | 是 |

`before_receiver_accept` 的谓词是第 6 步：没有密封字节，效果文件也不存在，所以是 `UNKNOWN`。
`after_receiver_accept` 的谓词是第 3 步：效果文件摘要等于密封摘要，回执文件不存在，所以是 `CONFIRM_TARGET`。
`after_receiver_accept_conflict` 的谓词是第 1 步：同一 effect key 上两个摘要都在且不相等，所以是 `CONFLICT`。

## 中继崩溃

检查点 `after_commit_before_send`。子进程调用 `Outbox.commit`，业务行和 outbox 行提交之后、`SharedReceiver.accept` 之前被结束。新进程重新打开 `outbox.sqlite`，读出已提交载荷，先发送一次，再以另一个 attempt id 发送一次。

| 检查点 | 代码路径 | 证据状态 | 次数 | logical_effect_count |
| --- | --- | --- | --- | --- |
| after_commit_before_send | Outbox | PUBLISH_SEALED | 10 | [1] |

至少一次体现在崩溃后新进程仍会发送已提交的 outbox 行。幂等体现在第二次 `SharedReceiver.accept` 返回 replay，并且 `logical_effect_count` 仍是 1。这不是任意 HTTP 的恰好一次，也不是跨库原子。

## 重复恢复

同一次杀死之后，`week3.kill_child` 的核对进程跑两遍。覆盖的检查点是 `after_claim_before_sealed`、`after_sealed_before_replace`、`after_replace_before_receipt`、`after_receipt`。每个检查点 5 次。代码路径名是 `kill_ledger`。

第一遍的证据状态写在 `evidence_state`。`recovery_action` 同时记下两遍的动作。第二遍若再次执行 `PUBLISH_SEALED_BYTES`，或者效果文件的摘要发生变化，该行会被运行器拒绝，不会进入矩阵。

| 检查点 | 第一遍 | 第二遍 | 次数 | 账本行数 | 二次重写 |
| --- | --- | --- | --- | --- | --- |
| after_claim_before_sealed | UNKNOWN | UNKNOWN | 5 | [1] | 0 |
| after_sealed_before_replace | PUBLISH_SEALED | CONFIRM_TARGET | 5 | [1] | 0 |
| after_replace_before_receipt | CONFIRM_TARGET | CONFIRM_TARGET | 5 | [1] | 0 |
| after_receipt | REPLAY_RECEIPT | REPLAY_RECEIPT | 5 | [1] | 0 |

`after_receipt` 的两遍都是 `REPLAY_RECEIPT`，效果文件摘要不变，所以回执重放没有再次发布。
`after_sealed_before_replace` 的第一遍发布密封字节，第二遍因为文件已经存在而变成 `CONFIRM_TARGET`，文件摘要和修改时间保持第一遍留下的值。
`after_replace_before_receipt` 的两遍都只确认已有目标。
`after_claim_before_sealed` 的两遍都是 `UNKNOWN`，不发明载荷。
`kill_ledger` 在两遍前后都是 1 行，claim 身份列不变，没有第二条 claim。

## 矩阵计数

| 检查点 | 代码路径 | 证据状态 | 次数 |
| --- | --- | --- | --- |
| after_claim_before_sealed | kill_ledger | UNKNOWN | 5 |
| after_commit_before_send | Outbox | PUBLISH_SEALED | 10 |
| after_receipt | kill_ledger | REPLAY_RECEIPT | 5 |
| after_receiver_accept | SharedReceiver | CONFIRM_TARGET | 10 |
| after_receiver_accept_conflict | SharedReceiver | CONFLICT | 10 |
| after_replace_before_receipt | kill_ledger | CONFIRM_TARGET | 5 |
| after_sealed_before_replace | kill_ledger | PUBLISH_SEALED | 5 |
| before_receiver_accept | SharedReceiver | UNKNOWN | 10 |

## RSR 消融

场景是 S14 与 S16。被结束的代码路径是 `ByteClaim.commit`，发生在 `SharedReceiver.accept` 之前。恢复进程从 SQLite 读出 claim 字节。`ByteClaim+RSR` 在发布前再调用 `semantics_oracle` 1.0.0：`VALID` 才发布，`INVALID` 返回 `SEMANTICALLY_SUPERSEDED` 并且不发布，`UNKNOWN` 返回 `UNKNOWN` 并且不发布。

无效恢复指：恢复把密封字节发布出去，并且这些字节相对该场景的 `world_after` 被 `semantics_oracle` 判为 `INVALID`。`CORRECT_STOP` 仍只表示后继场景里的正确停止，本表单独计数。

| 场景 | 路径 | 次数 | 无效恢复 | UNKNOWN | SEMANTICALLY_SUPERSEDED | CORRECT_STOP | 恢复耗时均值 ms | 预检均值 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S14 | ByteClaim | 10 | 10 | 0 | 0 | 0 | 590.023 | 0.000 |
| S14 | ByteClaim+RSR | 10 | 0 | 0 | 10 | 0 | 699.839 | 0.045 |
| S16 | ByteClaim | 10 | 10 | 0 | 0 | 0 | 572.696 | 0.000 |
| S16 | ByteClaim+RSR | 10 | 0 | 0 | 10 | 0 | 653.461 | 0.030 |

S14：无效恢复减少 10 次。证据状态 `UNKNOWN` 增加 0 次。`SEMANTICALLY_SUPERSEDED` 增加 10 次。`CORRECT_STOP` 增加 0 次。恢复进程耗时均值变化 +109.816 ms（ByteClaim+RSR 减 ByteClaim）。
S16：无效恢复减少 10 次。证据状态 `UNKNOWN` 增加 0 次。`SEMANTICALLY_SUPERSEDED` 增加 10 次。`CORRECT_STOP` 增加 0 次。恢复进程耗时均值变化 +80.765 ms（ByteClaim+RSR 减 ByteClaim）。

这组数字是权衡，不是单边收益。无效恢复从每场景 10 次降到 0 次，被挡住的发布记为 `SEMANTICALLY_SUPERSEDED`。本次 40 次预检全部是 `INVALID`，所以 `UNKNOWN` 与 `CORRECT_STOP` 没有上升。恢复耗时是进程墙钟，包含启动。预检调用本身不到 0.05 ms，墙钟均值差大于这次调用，并落在各次启动时间的波动里。

RSR 不消除 TOCTOU。`semantics_oracle` 返回之后、真正发布之前，评分世界仍然可能再变。`VALID` 分支只说明检查当时世界和密封字节一致，不把这次检查变成与发布原子的事实。

## 边界

矩阵里的每一行都是父进程对真实子进程的结束。`process_kill` 为 true，`power_loss_claim` 为 false。不把这个结果外推成掉电之后的磁盘状态，不外推成任意 HTTP 恰好一次，也不外推成两个数据库之间的原子提交。

## 收束

`independence_decision.json` 的结论是 PARTIALLY_SHARED。审计写明：durable claim 与 same-DB outbox 共用运行器、接收端和恢复入口；持久行分别写入 `byte_claims` 与 `business_effects`/`outbox`。本报告测量的是这条共享恢复入口的证据状态机。`SharedReceiver`、`Outbox.commit` 之后的中继、`kill_ledger` 的重复核对，以及 `ByteClaim` 上的 RSR 预检，都按上面的同一套谓词记录。
