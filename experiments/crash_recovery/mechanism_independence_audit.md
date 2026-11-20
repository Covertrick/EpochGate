# Week 4 机制独立性审计

## 结论

`decision` = `PARTIALLY_SHARED`。

Week 3 主比较里 Claim 与 Outbox 的四项配对差为 0，是因为 25 对格子的科学输入按同一条 proposal 记录对齐，而且计分走的是同一套运行器、同一个接收端、同一个恢复入口。持久行并不是同一个函数写出来的：`ByteClaim` 写 `byte_claims`，`Outbox` 写 `business_effects` 与 `outbox`。这两套写入在 S05 和 S08 的现有 SQLite 里留下了不同的状态字，预注册的 authority、semantic、successor、outdated 不读这些状态字。

因此本实现下 Claim 不是 Outbox 的变体或封装，零差也不是两种独立机制的等价证明。`SAME_CORE` 才会使用的句子“本实现下 Claim 是 Outbox 的变体/封装，零差是预期结果，不能声称两种机制独立等价。”与源码不符，本审计不采用前半句。后半句的禁止仍然成立：不能声称两种机制独立等价。

S17 与 S18 共用一条冻结路径。发现应写成：durable claim 路径不发布旧字节。

## 读过的代码

正式格子的入口是 `week3/execute.py` 的 `run_cell`。它按场景分成三条：

- `S13`–`S16` 调用 `run_separation`。
- `S17`–`S18` 调用 `run_freeze`。
- 其余调用 `run_week2_style`。`run_week2_style` 用 `week2.paths.path_factories()` 构造路径对象，再调用 `week2.frozen_proposal_runner._execute`。

Claim 的存储类是 `week2/baselines/byte_claim.py` 的 `ByteClaim`，适配器是 `week2/paths.py` 的 `ClaimPath`。Outbox 的存储类是 `week2/baselines/outbox.py` 的 `Outbox`，适配器是 `OutboxPath`。两边的 `authorize` 都只是把自己的 `store.commit` 交出去，谁也不调用谁。

接收端是 `week2/receiver_adapter.py` 的 `RecordingReceiver.accept`，内部调用 `scripts/reliability_contract.py` 的 `SharedReceiver.accept`。权威行读取是 `week2/db.py` 的 `read_authority`。进程杀死后的恢复是 `week3/kill_child.py` 的 `run_child` 与 `run_reconcile`。这些函数没有按路径名分叉，路径名只被写进合约或账本列。

本审计没有重跑模型，也没有重跑运行时。下面的库内行来自已有 `week3/formal_runs/*/state.sqlite` 与 `kill/state.sqlite` 的只读查询。

## 主比较过滤

`week3/week3_replay_joint.csv` 共 108 行。`in_main_comparison` 为 true 的是 100 行：25 个簇，每簇 Direct、Admission、Claim、Outbox 各一格。为 false 的 8 行是 `S06` 的 4 格（`mechanism_role` = `NON_DISCRIMINATIVE`）和 `S09` 的 4 格（`mechanism_role` = `MECHANISM_SPECIFIC_DIAGNOSTIC`）。

这与 `week3/contract.py` 的 `in_main_comparison` 一致：`scenario_id` 属于 `EXCLUDED_SCENARIOS`（`S06`、`S09`），或 `mechanism_role` 属于 `NON_DISCRIMINATIVE`、`MECHANISM_SPECIFIC_DIAGNOSTIC`，则不进主比较。Week 3 报告里 Claim 对 Outbox 的簇数是 25，与这 100 格里的 25 对 Claim/Outbox 相同。

这 25 对里，`scenario_behavior` 为 `week2_path` 的 17 对，`separation` 的 4 对（S13–S16），`notified_freeze` 的 4 对（S17 的 p24/p25，S18 的 p26/p27）。

## 逐层判断

| 层 | 判断 | 依据 |
| --- | --- | --- |
| 路径适配器入口 | 部分共享 | 分发共享，类独立 |
| 权威检查 | 部分共享 | `read_authority` 共享，比较代码各写一份；S17/S18 不走这两份 `commit` |
| claim/outbox 表写入 | 部分共享 | `init_schema` 把表都建出来；成功提交后的行写入独立 |
| 中继触发 | 部分共享 | `_deliver` 共享；`mark_sending` 的 SQL 独立 |
| 接收端调用 | 共享 | 同一个 `RecordingReceiver.accept`，`effect_key` 都是 `idempotency_key` |
| 恢复入口 | 共享 | `_try_recover`、`run_child`、`run_reconcile` 不按路径分叉 |
| 回执读取 | 部分共享 | 回执值来自同一次 `accept` 返回；落库表独立 |
| 密封字节读取 | 部分共享 | week2 路径按表读；进程杀死路径读同一张 `kill_ledger`；冻结路径不把冻结字节交给接收端 |

### 路径适配器入口

`run_week2_style` 在构造 `RunCtx` 之后调用 `_execute(path, ctx)`。`path` 来自 `path_factories()[path_name]()`。`ClaimPath.authorize` 调用 `ByteClaim.commit`。`OutboxPath.authorize` 调用 `Outbox.commit`。方法名并列：`authorize`、`blob`、`has_durable`、`mark_sending`、`acknowledge`。类体不互相引用。

S13–S18 的 `run_cell` 不调用 `path_factories`。这两条场景族的适配器入口没有被执行。

### 权威检查

`ByteClaim.commit` 与 `Outbox.commit` 都是：`faults.hit("before_db_transaction")`，`BEGIN IMMEDIATE`，`faults.hit("inside_transaction")`，然后 `read_authority`。不通过的条件都是 `eligible` 为假，或 `token`、`epoch` 与请求不一致，然后 `ROLLBACK` 并返回 false。通过则各自插入并 `COMMIT`，再 `faults.hit("after_commit_before_send")`。

比较式写了两遍，没有一个共用的 `commit`。共用的是 `read_authority` 和 `FaultController.hit`。

S17/S18 的权威变化由 `run_freeze` 里的 `_log_authority` 记下通知操作（`supersede` 或 `revoke`）。那里不调用 `ByteClaim.commit` 或 `Outbox.commit`。

### 表写入

`week2/db.py` 的 `init_schema` 对每条 week2 风格连接创建 `authority`、`authority_log`、`business_effects`、`outbox`、`byte_claims`。所以 Claim 运行的库里看得到空的 `outbox`，Outbox 运行的库里看得到空的 `byte_claims`。空表是共享模式，不是共享写入。

`ByteClaim.commit` 只 `INSERT INTO byte_claims`，初始 `status` 为 `CLAIMED`。`Outbox._insert_locked` 在同一事务里先 `INSERT INTO business_effects`，再 `INSERT INTO outbox`，初始 `status` 为 `PENDING`，`attempt_count` 为 0。`outbox` 行不保存 `authority_token` 与 `authority_epoch`，这两列在 `business_effects` 上。

已有库与这段代码一致。S03 的 `w3-p04-Claim-1` 只有 `byte_claims` 一行，`status` 为 `ACKED`，载荷 SHA256 `0ca9e658f6ba14abfcce15a8fed65c692b536dbc79e34d12b51d5ba894145268`，`receipt_id` 为 `12a7210a3085957f0d4523f6f86f4d01a3517f95fa85cdb0b3ed051caaba5c9c`。`w3-p04-Outbox-1` 的 `byte_claims` 为空，同样的载荷摘要和同样的 `receipt_id` 在 `outbox`（`event_id` = `obx:p04`，`attempt_count` = 1）和 `business_effects` 上。

S01、S02、S04、S11、S12 的故障点在 `before_effect`，权威在 `commit` 之前已经变化。两边都回滚，`business_effects`、`outbox`、`byte_claims` 都是空的。写入函数仍然不同，持久行都没有留下。

### 中继触发

`_deliver` 先 `faults.hit("before_send")`。若 `receiver.fail_next`，它记一次失败并返回，不读 blob，也不 `mark_sending`。否则它调用 `path.blob`、核对载荷 SHA256、`path.mark_sending`，再 `receiver.accept`。

`ByteClaim.mark_sending` 执行 `UPDATE byte_claims SET status = 'UNRESOLVED'`。`Outbox.mark_sending` 把 `attempt_count` 加 1，超过 `RETRY_LIMIT`（3）则 `status` 为 `DEAD`，否则为 `UNRESOLVED`。正式样本里第二次 `mark_sending` 只出现在 S08 的恢复里，`attempt_count` 停在 2，`DEAD` 分支没有走到。

### 接收端调用

`_deliver` 与 `_try_recover` 传给 `RecordingReceiver.accept` 的 `effect_key` 都是 `ctx.request.idempotency_key`，不是 `outbox_event_id`。`RecordingReceiver._one` 再调用 `SharedReceiver.accept`。`SharedReceiver` 的注释写明接纳日志和业务文件不随臂更换存储。主比较 25 对的 `receiver_impl` 都是 `reliability_contract.SharedReceiver`，`receiver_version` 都是 `m4-shared-receiver-1`。

### 恢复入口

week2 风格的恢复入口是 `_try_recover`。它要求故障点属于 `after_commit_before_send`、`after_send_before_receipt`、`after_receipt_before_ack`，且 `path.supports_recovery` 与 `path.has_durable` 都为真。`ClaimPath.supports_recovery` 与 `OutboxPath.supports_recovery` 都是 true。函数体共用：读 `path.blob`，核对摘要，必要时再次 `mark_sending` 并 `accept`，然后 `path.acknowledge`。

进程杀死的恢复入口是 `kill_once` → `week3.kill_child`。`launch_and_kill` 把路径名放进 `contract["path"]`。`run_child` 只把这个字符串写入 `kill_ledger.path`。`_commit_claim`、`_commit_seal`、`_commit_receipt`、`run_reconcile` 的分支看的是检查点、密封字节、目标文件和回执文件，不看路径名是 Claim 还是 Outbox。`week3/kill_runner.py` 的摘要已经写了同一句：两条路径使用同一个子进程代码路径、同一张 `kill_ledger`。

S13 的 `w3-p19-Claim-1` 与 `w3-p19-Outbox-1` 的 `kill_ledger` 除 `path` 列以外相同：`claim_status` 为 `CLAIMED`，`sealed_payload` 为空，`payload_sha256` 为 `66f9f54fcc618f91f448fa277694bd84780160e659054603b4451f794d114fa1`。S14–S16 同样只差 `path` 列，密封摘要与 `claim_status` 成对相同。

### 回执读取

week2 风格的回执值是 `receiver.accept` 的返回值。`_deliver` 把它赋给 `ctx.observed_receipt`，再交给 `path.acknowledge`。`ByteClaim.acknowledge` 更新 `byte_claims.receipt_id`。`Outbox.acknowledge` 更新 `outbox.receipt_id`。S03 的 p04 两边记下了同一个 `receipt_id`。

进程杀死路径读的是 `kill/receipt.json`，函数是 `run_reconcile`，两条路径共用。

### 密封字节读取

week2 风格：`ByteClaim.payload` 执行 `SELECT payload FROM byte_claims`。`Outbox.payload` 执行 `SELECT payload FROM outbox`。`_deliver` 与 `_try_recover` 都用请求上的 `payload_sha256` 核对读出的字节。

进程杀死路径：`run_reconcile` 读 `kill_ledger.sealed_payload`。缺目标文件且没有回执时，它用这份密封字节调用 `SharedReceiver.accept`。S14 与 S16 的密封摘要在 Claim 与 Outbox 之间相同。

冻结路径不读这两张表，也不把 `frozen_claim.payload` 交给接收端。见下一节。

## 数据路径

比较文件是 `week4/claim_outbox_cell_comparison.csv`。一对一行，共 25 行。判定只看科学输入和计分结果。`run_id` 全部不同（`w3-{proposal}-Claim-1` 与 `w3-{proposal}-Outbox-1`），单独的 `run_id` 差异记为 `IDENTICAL`。

联合表没有 `fault_schedule_hash`、`timeout_ms`、`budget_units`。这三列来自同一 `proposal_id` 的注册记录：`week2/proposal_registry.jsonl` 的 p01–p15，以及 `week3/proposal_registry.jsonl` 的 p16–p27。`run_week2_style` 从这条记录构造唯一的 `EffectRequest`，两条路径读到的是同一组数。CSV 里仍各写一列，用来留下比较结果。25 对的 `timeout_ms` 都是 2000，`budget_units` 都是 10。

列映射：

| 审计字段 | 实际来源 |
| --- | --- |
| `proposal_sha256` | `week3_replay_joint.csv` 的 `payload_sha256` |
| `world_before_hash` | 同名列 |
| `authority_initial_hash` | 同名列 |
| `fault_schedule_hash` | proposal 注册记录，不在联合表 |
| `receiver_impl` | 同名列 |
| `receiver_version` | 同名列 |
| `oracle_versions` | 同名列 |
| `timeout_ms` | proposal 注册记录 |
| `budget_units` | proposal 注册记录 |
| `physical_state` | `physical_effect_state` |
| `evidence_state` | 同名列 |
| `semantic_result` | `semantic_validity` |
| `successor_status` | 同名列 |
| `terminal_score` | `terminal_outcome` |

`IDENTICAL` 的定义：上表输入列两边相等，且五个结果列两边相等。输入有任一不等则为 `DIFFERS_INPUT`。输入相等而结果有任一不等则为 `DIFFERS_OUTCOME`。

计数：`IDENTICAL` 25，`DIFFERS_INPUT` 0，`DIFFERS_OUTCOME` 0。

另外核对过、但没有放进判定列的字段也成对相等：`authority_decision`、`recovery_state`、`published_old_bytes`、`adjudication`、`duplicate_effect`、`runner_status`、`stop_correctness`。`latency_ms`、`killed_pid`、`provenance_ref` 不在科学输入里，不参与判定。

Week 3 报告里的四个指标是这些字段的函数：authority 看 `authority_decision == STALE_ACCEPTED`，semantic 看 `semantic_validity == INVALID`，successor 看 `successor_status` 属于 `FAILED` 或 `NOT_COMPLETED`，outdated 是语义、权威和物理/恢复的合取。字段成对相等，所以这四个指标的配对差为 0，McNemar 没有不一致对。报告原句仍然成立：这不是等价检验，也不是 Outbox 更优。本审计补充的是机制原因，不把这句改成等价证明。

## 零差归因

零差同时有两部分原因，缺一不可。

科学输入相同。25 对共用 proposal 字节摘要、世界摘要、权威摘要、故障日程摘要、接收端实现与版本、oracle 版本、超时和预算。故障日程按 proposal 安装，`FaultController` 的注释写明同一份日程对象装到每条路径上。

计分实现不是两套独立核心碰巧没被打中。17 对 `week2_path` 进入同一个 `_execute`。4 对分离场景进入同一个 `run_reconcile`。4 对冻结场景进入 `run_freeze` 的同一个 `RECOVERY_PATHS` 分支。oracle 在这之后打分，不读表名，不读 `CLAIMED`/`PENDING`，不读 `attempt_count`。

存储实现也不是同一个核心。`ClaimPath` 不调用 `Outbox`，`OutboxPath` 不调用 `ByteClaim`。S05 的 `w3-p07-Claim-1` 留下 `byte_claims.status = CLAIMED`、`receipt_id` 为空。`w3-p07-Outbox-1` 留下 `business_effects` 一行和 `outbox.status = PENDING`、`attempt_count = 0`、`receipt_id` 为空。故障点是 `before_send` 的 `receiver_fail`：两边都已经 `commit`，`_deliver` 在 `mark_sending` 之前返回。计分结果仍是 `physical_effect_state = ABSENT`，`semantic_validity = UNKNOWN`，`successor_status = NOT_APPLICABLE`，`terminal_outcome = MISMATCH`，`evidence_state = SUFFICIENT`。

S08 的内部轨迹也不同，结果仍相同。细节在 `week4/mechanism_trace_diff.md`。`DEAD` 与第三次以上的 `mark_sending` 没有出现在这 25 对里，所以重试上限没有机会把 Outbox 打成另一条计分结果。

## S17 / S18

`run_cell` 看到 `FREEZE_SCENARIOS` 就调用 `run_freeze`，不构造 `ClaimPath` 或 `OutboxPath`。`run_freeze` 里两处 `path_name in RECOVERY_PATHS` 把 Claim 和 Outbox 放进同一分支。`RECOVERY_PATHS` 是 `("Claim", "Outbox")`。

该分支创建 `frozen_claim`，插入一行，`status` 为 `FROZEN`，`publish_allowed` 为 0，`path` 列写下路径名。然后若 `legal_payload(observed)` 非空，就把这份合法后继字节交给 `RecordingReceiver.accept`；若为空，就在接收日志里记 `frozen_stop`，不调用 `accept`。Direct 与 Admission 走另一分支，把提案旧字节交给 `accept`。

`_freeze_status` 同样只按是否属于 `RECOVERY_PATHS` 分支。合法后继已发布时记 `COMPLETED_VALID`。合法字节为空且没有物理目标时记 `CORRECT_STOP`。

现有库与此一致。`w3-p24-Claim-1` 与 `w3-p24-Outbox-1` 的 `byte_claims`、`outbox`、`business_effects` 都是空的。两边都有一行 `frozen_claim`，载荷 SHA256 都是 `4409f7edc327b8af167741da7fcd4e369b9d0bcac48b086d68b0667d1a49b5ae`，`publish_allowed` 都是 0。唯一不同的列是 `path`：`Claim` 与 `Outbox`。p25、p26、p27 同样只差这个标签。S17 两边的 `successor_status` 都是 `COMPLETED_VALID`，`published_old_bytes` 都是 false。S18 两边都是 `CORRECT_STOP`，物理状态都是 `ABSENT`。

所以 S17/S18 是两个标签下的一条 durable claim 冻结路径，不是两条独立机制。门 C 里“Claim/Outbox 不发布旧字节”应改读成：durable claim 路径不发布旧字节。Direct 与 Admission 仍发布旧字节，那是这条分支之外的行为。

## 叙事规则

`narrative_rule`：论文叙事只写，在协作式事务表面上，durable claim 与 same-DB outbox 共用运行器、接收端和恢复入口，本样本已计分结果一致；持久行写入不是同一个函数。零配对差不是两种机制的独立等价，也不是 Claim 优于 Outbox。S17/S18 写成同一条 durable claim 路径不发布旧字节。

不写“Claim 不差于 Outbox”。不把 CI `[0, 0]` 写成等价检验。

## 未改动核对

`docs/开发文档/week1-historical-audit/archived_joint.csv` 的 SHA256 为 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`，与 `week3/contract.py` 的 `ARCHIVED_SHA256` 一致。写入本目录之前，`week2/` 与 `week3/` 全部文件的树哈希为 `9c0db4425bf0344ca61a16f5ec718e8862bdd103122463005e5eafd07662332f`（1786 个文件，9,766,304 字节；按相对路径排序，对每个文件的路径和 SHA256 再做一次 SHA256）。

这两个目录在 git 里是未跟踪目录，`git status -- week2 week3` 只显示 `??`，看不出目录内部是否被改过。树哈希是内容核对。本审计只新增 `week4/` 下的四份文件。写完后的树哈希记在文末复核句里；若与上面的值相同，则 `week2/` 与 `week3/` 的内容没有变化。

写完后复核：`week2_week3_tree_sha256_after` = `9c0db4425bf0344ca61a16f5ec718e8862bdd103122463005e5eafd07662332f`。`archived_joint.csv` 的 SHA256 仍为 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`。
