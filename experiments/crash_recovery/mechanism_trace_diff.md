# Claim 与 Outbox 的共享函数和内部轨迹

`decision` = `PARTIALLY_SHARED`。本文同时列出共享函数和已经留在正式运行库里的轨迹差。轨迹来自只读打开现有 SQLite，不是重跑。

## 共享函数

这些函数对 Claim 和 Outbox 是同一个代码对象。路径差异只出现在它们回调的 `path.blob`、`path.mark_sending`、`path.acknowledge`，或者只出现在写进表里的路径名字符串。

运行与计分：

- `week2/frozen_proposal_runner.py`：`_execute`、`_execute_inner`、`_deliver`、`_try_recover`、`_observation`、`_score`、`_classify`
- `week3/execute.py`：`run_cell`、`run_week2_style`、`run_separation`、`run_freeze`、`_freeze_status`、`_separated_physical`
- `week2/faults.py`：`FaultController.hit`
- `week2/db.py`：`connect`、`init_schema`、`read_authority`、`authority_log`、`pragma_snapshot`

接收端：

- `week2/receiver_adapter.py`：`RecordingReceiver.accept`
- `scripts/reliability_contract.py`：`SharedReceiver.accept`、`SharedReceiver.physical`、`SharedReceiver.accepted_count`

进程杀死与恢复：

- `week3/kill_runner.py`：`launch_and_kill`、`kill_once`
- `week3/kill_child.py`：`run_child`、`_commit_claim`、`_commit_seal`、`_mark_replaced`、`_commit_receipt`、`run_reconcile`

共享表，指两边的连接都会建、或两边的恢复都会写的表：

- `authority`、`authority_log`：week2 风格与冻结路径都写。
- `business_effects`、`outbox`、`byte_claims`：`init_schema` 都创建。某一侧成功提交时，另一侧对应的表保持为空。
- `kill_ledger`：S13–S16 两边都写，除 `path` 列外成对相同。
- `frozen_claim`：S17/S18 两边都写，除 `path` 列外成对相同。

## Claim 专有

- `week2/paths.py`：`ClaimPath`
- `week2/baselines/byte_claim.py`：`ByteClaim.commit`、`has_row`、`payload`、`mark_sending`、`acknowledge`
- 表 `byte_claims` 的行。键是 `idempotency_key`。初始状态是 `CLAIMED`。`mark_sending` 只把状态改成 `UNRESOLVED`，没有尝试计数。

`ClaimPath` 的五个方法各自转发到 `ByteClaim`，不调用 `Outbox`。

## Outbox 专有

- `week2/paths.py`：`OutboxPath`
- `week2/baselines/outbox.py`：`Outbox.commit`、`_insert_locked`、`has_row`、`payload`、`mark_sending`、`acknowledge`
- 表 `business_effects` 与 `outbox` 的行。中继键是 `outbox_event_id`。初始状态是 `PENDING`，`attempt_count` 为 0。
- `RETRY_LIMIT = 3`。`mark_sending` 在 `attempt_count + 1 > 3` 时把状态写成 `DEAD`。
- `crash_matrix` 与 `write_crash_matrix` 只服务 Week 2 的故障矩阵，Week 3 的 `run_cell` 不调用它们。

`OutboxPath` 不调用 `ByteClaim`。

## 轨迹差：S05，提交后的状态字

场景 p07，故障点 `before_send`，操作 `receiver_fail`。`_execute_inner` 先调用 `path.authorize`，也就是各自的 `commit`，然后再进入 `_deliver`。`_deliver` 看见 `fail_next` 后直接返回，不调用 `mark_sending`，也不调用 `receiver.accept`。

只读结果：

- `w3-p07-Claim-1`：`byte_claims` 一行，键 `effect:p07`，`status` = `CLAIMED`，`receipt_id` 为空。`outbox` 与 `business_effects` 为空。
- `w3-p07-Outbox-1`：`business_effects` 一行，键 `effect:p07`。`outbox` 一行，`event_id` = `obx:p07`，`status` = `PENDING`，`attempt_count` = 0，`receipt_id` 为空。`byte_claims` 为空。
- 两边载荷 SHA256 都是 `3f8d593fcfb953bf045a033bf1fd84b180b865add50171ec43703a43ae6e1f0f`。

计分没有变：两边都是 `physical_effect_state` = `ABSENT`，`evidence_state` = `SUFFICIENT`，`semantic_validity` = `UNKNOWN`，`successor_status` = `NOT_APPLICABLE`，`terminal_outcome` = `MISMATCH`。oracle 不读 `CLAIMED` 与 `PENDING` 的差别，也不读空的回执列。接收端没有被调用，所以没有第二套物理字节。

## 轨迹差：S03，同一回执落在不同表

p04 的故障点是 `before_effect` 的 `drift`。`drift` 不改权威，两边都提交并完成一次发送。

只读结果：

- Claim 的 `byte_claims.status` = `ACKED`，`receipt_id` = `12a7210a3085957f0d4523f6f86f4d01a3517f95fa85cdb0b3ed051caaba5c9c`。
- Outbox 的 `outbox.status` = `ACKED`，`attempt_count` = 1，`receipt_id` 与上面相同。另有一行 `business_effects`。
- 载荷 SHA256 都是 `0ca9e658f6ba14abfcce15a8fed65c692b536dbc79e34d12b51d5ba894145268`。

`attempt_count` 为 1，因为 `_deliver` 只调用了一次 `mark_sending`，没有进入 `_try_recover`。物理字节、语义和终态因此相同：`NOT_LEGAL`、`INVALID`、`MISMATCH`。p05、p16、p17、p18 是同一形状。

## 轨迹差：S08，共享恢复入口，Outbox 尝试计数为 2

p10 与 p11 的故障点是 `after_send_before_receipt` 的 `crash`。`_deliver` 已经调用过一次 `mark_sending` 和一次 `receiver.accept`，然后 `faults.hit` 抛出 `FaultCut`。此时 `ctx.observed_receipt` 还是空的。`_try_recover` 发现回执为空，再调用一次 `path.mark_sending`，并用 `attempt_id` 后缀 `:recover` 再调用一次 `accept`，最后 `acknowledge`。

只读结果，p10：

- Claim：`byte_claims.status` = `ACKED`，`receipt_id` = `f42aed909462f44666563a016f88fcd7bd16f2293556405c0eeee6eb8d5697b2`。没有 `attempt_count` 列。
- Outbox：`outbox.status` = `ACKED`，`attempt_count` = 2，`receipt_id` 与 Claim 相同。另有 `business_effects` 一行。`byte_claims` 为空。
- 接收日志两边都是两条：`{run_id}:send` 为 `accepted`，`{run_id}:send:recover` 为 `replayed`，回执相同。p11 的形状相同，回执是 `f69cf40d9a58e3123608120d18e708b5a90fd2b7533e6418247ef5166c539e48`。

第二次 `accept` 命中 `SharedReceiver` 的相同摘要重放，`applied` 为 false。`duplicate_effect` 看的是 `accepted_count > 1`，重放不加接受次数，所以两边都是 false。`Outbox.mark_sending` 的第二次把计数从 1 加到 2，状态仍是 `UNRESOLVED`，随后 `acknowledge` 把它改成 `ACKED`。`RETRY_LIMIT` 是 3，`DEAD` 没有发生。计分字段因此与 Claim 相同。

这条差说明恢复入口是共享的，而中继状态机不是同一个函数。它没有变成预注册指标上的不一致对。

## S13–S16：恢复轨迹只差路径标签

`run_separation` 对 `RECOVERY_PATHS` 调用 `kill_once`。子进程不读 `byte_claims` 或 `outbox`。

p19（S13）两边的 `kill_ledger.claim_status` 都是 `CLAIMED`，密封字节都为空，`payload_sha256` 都是 `66f9f54fcc618f91f448fa277694bd84780160e659054603b4451f794d114fa1`。`path` 列分别是 `Claim` 和 `Outbox`。

p20（S14）两边都是 `SEALED`，密封 SHA256 都是 `4a3296939010375224c4a20ad8098aaa272af533fc27ef6e75864bf0725e2de5`。p21（S15）两边都是 `REPLACED`，密封 SHA256 都是 `0099495fd0133c5792482a374c07f5c8ba13125537b2a846f6300368250eaea7`。p22（S16）两边都是 `SEALED`，`receipt_required` 都是 1，密封 SHA256 都是 `fdaa02213abcdeebb92854ce453ee0f561d759f820ee2da7cb5eacf7a1f0a402`。

`run_reconcile` 按密封字节、目标文件和回执文件选择 `PUBLISH_SEALED`、`CONFIRM_TARGET`、`REPLAY_RECEIPT` 或 `UNKNOWN`。路径名不参与这个选择。所以分离场景的零差来自同一条恢复函数，不是来自两套存储类的巧合。

## S17 / S18：一条冻结路径

`run_freeze` 不调用 `ClaimPath`、`OutboxPath`、`ByteClaim.commit`、`Outbox.commit`。Claim 与 Outbox 都落在 `path_name in RECOVERY_PATHS` 里。

p24（S17）两边的 `byte_claims`、`outbox`、`business_effects` 都为空。两边都有 `frozen_claim`：`status` = `FROZEN`，`publish_allowed` = 0，载荷 SHA256 = `4409f7edc327b8af167741da7fcd4e369b9d0bcac48b086d68b0667d1a49b5ae`。`path` 列分别是 `Claim` 和 `Outbox`。

发布出去的字节是 `legal_payload(observed)`，不是 `frozen_claim.payload`。p24 与 p25 因此是合法后继，`successor_status` = `COMPLETED_VALID`，`published_old_bytes` = false。p26 与 p27 的合法字节为空，接收日志记 `frozen_stop`，`successor_status` = `CORRECT_STOP`，物理状态为 `ABSENT`。

这条轨迹没有 Claim 存储和 Outbox 存储的差别。路径名是标签。对应的发现是：durable claim 路径不发布旧字节。
