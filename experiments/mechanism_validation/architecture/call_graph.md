# Week 2–Week 3 调用图

这份图描述当时的代码。Week 5 的新运行时不改写这段历史。Week 4 对那段代码的判定是 `PARTIALLY_SHARED`。

## 共享函数

这些函数同时服务 Claim 和 Outbox，或者根本不区分二者：

- `week2/db.py` 的 `connect`、`init_schema`、`read_authority`、`authority_log`、`pragma_snapshot`
- `week2/faults.py` 的 `FaultController.hit`
- `week2/paths.py` 的 `path_factories`
- `week2/frozen_proposal_runner.py` 的 `_execute`、`_execute_inner`、`_deliver`、`_try_recover`、`_observation`、`_score`、`_classify`
- `week2/receiver_adapter.py` 的 `RecordingReceiver.accept`
- `scripts/reliability_contract.py` 的 `SharedReceiver.accept`
- `week3/execute.py` 的 `run_cell`、`run_week2_style`、`run_separation`、`run_freeze`、`_freeze_status`、`_separated_physical`
- `week3/kill_runner.py` 的 `launch_and_kill`、`kill_once`
- `week3/kill_child.py` 的 `run_child`、`_commit_claim`、`_commit_seal`、`_commit_receipt`、`run_reconcile`

`_try_recover` 是共享恢复入口。它看 `path.supports_recovery` 和 `path.blob`，然后调用同一个 `receiver.accept`。Claim 和 Outbox 没有各自的恢复函数。

## 只属于 Claim

- `week2/paths.py` 的 `ClaimPath`
- `week2/baselines/byte_claim.py` 的 `ByteClaim.commit`、`has_row`、`payload`、`mark_sending`、`acknowledge`
- 表 `byte_claims`

`ByteClaim.commit` 在一次事务里检查 `authority` 并插入 `byte_claims`。恢复时重放的是这些已存字节，不重新给载荷打分。

## 只属于 Outbox

- `week2/paths.py` 的 `OutboxPath`
- `week2/baselines/outbox.py` 的 `Outbox.commit`、`_insert_locked`、`has_row`、`payload`、`mark_sending`、`acknowledge`，以及 `crash_matrix`
- 表 `business_effects` 和 `outbox`
- `RETRY_LIMIT = 3`

`_insert_locked` 在同一个事务里写业务行和 outbox 行。中继在 `COMMIT` 之后。`mark_sending` 增加 `attempt_count`，超过上限写成 `DEAD`。

## 共享接收端

`RecordingReceiver` 包着 `SharedReceiver`。Direct、Admission、Claim、Outbox 都调用 `accept`。同一键、同一载荷得到 replay；同一键、不同载荷得到 conflict。这个接收端是进程内对象，效果文件和接纳日志都在运行目录里。路径代码可以直接看到那份状态。

## 共享恢复

`frozen_proposal_runner._try_recover` 和 `week3/execute.py` 里 `path_name in RECOVERY_PATHS` 的分支是共享恢复。`week3/contract.py` 把 `RECOVERY_PATHS` 定义成 `("Claim", "Outbox")`。S17 和 S18 不调用 `ClaimPath` 或 `OutboxPath`。`run_freeze` 对这两个名字走同一段插入，`publish_allowed` 为 0。

## 只改路径标签的函数

- `run_freeze` 把 `path_name` 写进 `frozen_claim.path`。Claim 和 Outbox 的插入语句是同一条，差别是这个文本列。
- `_freeze_status` 用 `path_name in RECOVERY_PATHS` 选择同一组返回值，不读取 Claim 或 Outbox 各自的表。

这两处在当时的样本里不能被说成两种机制已经分开。持久行的写入函数仍然不是同一个：`ByteClaim.commit` 写 `byte_claims`，`Outbox._insert_locked` 写 `business_effects` 和 `outbox`。共享的是运行器、接收端和恢复入口。

Week 5 的新代码在 `week5/runtime/`。它不把 Week 2 到 Week 3 重新叙述成已经独立。
