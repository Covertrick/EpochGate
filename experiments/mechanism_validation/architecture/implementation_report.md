# Week 5 实现报告

## 改了什么

新代码只在 `week5/runtime/`、`week5/architecture/`、`week5/tests/`、`week5/semantic/`、`week5/proposals/`、`week5/experiments/`。Week 2 到 Week 4 的调用图写在 `call_graph.md`，判定维持 `PARTIALLY_SHARED`，没有改写成当时已经分开。

新的 Claim 使用 `claim_authority` 和 `claim_rows`，自己提交、转移阶段、恢复和确认。新的 Outbox 在同一个事务里写 `outbox_business` 和 `outbox_relay`，中继在提交之后，自带 `attempt_count`、重试上限和 `DEAD`。两边的核心方法不是同一个函数对象，恢复也不是按路径标签切换的私有状态机。

接收端是单独进程里的 SQLite。路径只能 `submit` 和 `query`。审计进程打开接收端数据库。同一键同一载荷是重放，同一键不同载荷是冲突。逻辑效果计数不因重放增加。

`mechanism_independence.json` 的判定是 `INDEPENDENT`。共享的 harness、`PathReceiverClient` 和计分器被记在 `shared_but_not_sufficient`，它们单独不构成独立。

## 新数据文件

- `week5/semantic/rsr_truth_table.csv`：9 行，含 `VALID`、`INVALID`、`UNKNOWN`
- `week5/semantic/toctou_matrix.csv`：150 行，A 到 E 各 10 次、三臂
- `week5/semantic/ablation_results.csv`：159 行
- `week5/semantic/fixtures/` 与 `week5/semantic/runs/`：夹具字节和当次运行库
- `week5/proposals/registry.jsonl`：w4l01–w4l18 的散列副本，加上 24 条 v2 提案
- `week5/proposals/bytes/`：v2 载荷
- `week5/proposals/generator_comparison.csv`
- `week5/experiments/replay_manifest.json` 与 sidecar
- `week5/experiments/replay_joint.csv`：24 簇、96 格

## 测试

命令：

`c:\Project\EpochGate\.venv\Scripts\python.exe -m pytest week5/tests -q --tb=short`

结果：11 passed。

## 新产物 SHA256

- `architecture/call_graph.md` `5c159560d057ceba5680be8e9f2b43ff08fdd6b744cdc68caeef67f8670d4fc4`
- `architecture/receiver_contract.md` `310dd0caa6b1b18e0f4f831b74cdc8214763f7e52b99b9db08b6c9c411aafc77`
- `architecture/target_ledger_schema.sql` `60247550716ff7434283834774dd90d3faea8feaa8e7a2a0146b0e0a4f6dbed4`
- `architecture/mechanism_independence.json` `dcddf43d8bf68736436fa7f7ee61febb28348bfe25883bb8931dc92e6d66555d`
- `runtime/` 目录 `93455ee6058a84622de7d79d9b8a7b1788224723b0e9c4d19b1844967e8bcaa6`
- `tests/` 目录 `f8e8c8fe925c392d36aa5c6d447f13a9b659450cf9d3234e786026dcf6e2b343`
- `semantic/version_fence_protocol.md` `59dc87f1fec9419b063ba6d6eb946d93535159b65a8d14b284a7c4255e461894`
- `semantic/rsr_truth_table.csv` `0d137faa7334473ec921c0e732d4960152c02eb54c6c3732bfc9f16f9b76553b`
- `semantic/toctou_matrix.csv` `fd1dcf5e2e0064a63a67056206669c66e6be7776f65371e828b698ce1e398a44`
- `semantic/ablation_results.csv` `f2970e8a9e5c59e83cf4d7d9eb752c60355abb21ec8327c3df68b31577038cf1`
- `semantic/performance_report.md` `69916ed2af3abb8ed43534acc92b8c9a2a98d8abe147e07c24bcbf32f5e6704c`
- `semantic/limitations.md` `8f73c043d116354ef4a3ccf1488208dc2e7a2e499caebea029c6cfd497be3fdf`
- `semantic/run_ablation.py` `b9c80235139e2f4461c2f46eb6eba3dc1aefcaa6c4f01826a4dcd640b86a5800`
- `proposals/registry.jsonl` `ae15a7b574ac0bdacc5fc4a23610db30e58404b47fa3aa0b4018b35bcd65d831`
- `proposals/second_generator_v2.py` `c10dcd0521529a80fc99f5dcc638f022c7e30280742c8af75e9b78413a1d0684`
- `proposals/comparison_thresholds.json` `e9f16a8f6aa3ace52c8461e1af418b9d80d567eea9fc71c29ecb045f5de73da0`
- `proposals/generator_comparison.csv` `addebfa7e26a5cc0793128945fb77f9c9ae702938c9539c59a42aceef0768935`
- `experiments/replay_manifest.json` `f7d0e5c82648d151b1d08843e58bacefcd5901ed0c4e1fdfbac3835e420e021f`
- `experiments/replay_joint.csv` `3754e4fbf549f3a61bac41977ddfadedd17718bdbff85285b37d86f84d287654`
- `experiments/coverage_report.md` `4fd0657523ea9004e8624400b85a1fee5a630159c92c8c4a9a44af5fd858c03c`
- `experiments/run_replay.py` `fc46b179a85b75617f66bf45f9f595a10d0a3f3fe83995f0beab2536ac08c751`

本报告自身不计入上面的散列。`archived_joint.csv` 的 SHA256 是 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`。

## 未完成

- 消融样本里 `AUTHORITY_SUPERSEDED` 是 0。权威不一致的拒绝在接收端事务和单元测试里，没有进入 A–E 的发布计数。
- 本周没有做 `TerminateProcess` 实验。语义矩阵和回放的 `process_kill` 都是 false。
- 可选模型生成是 `NOT_AVAILABLE`。配置指向 `https://api.deepseek.com/v1`，没有调用。
- 生成器比较里 authority、successor、载荷长度、幂等键唯一率和 oracle 可裁定率没有越过预先冻结的阈值。

## 局限

接收端是本地测试进程，不是第三方服务。围栏原子性是一次 SQLite 事务，不是跨库原子，也不是掉电持久性。条件 A 的间隙由实验控制器写入，不是另一个未同步的写进程。RSR 不消除 TOCTOU。`UNKNOWN` 不自动重发。`RESTORED_BYTES` 不是 `CONFIRMED_EFFECT`。`CORRECT_STOP` 不是后继失败。

## 可能影响论文主张的问题

不能写 Claim 优于 Outbox。回放里二者在权威和后继家族上一起停发，在恢复家族上各自从自己的表恢复；Direct 仍会发出旧字节。不能把 Week 2 到 Week 3 写成已经独立。不能把 RSR 单独写成消除了 TOCTOU：条件 A 上 R1 仍有 10 次无效且陈旧的发布，这 10 次是 R2 的围栏拒绝的。不能把围栏写成掉电持久或任意 HTTP exactly-once。`DIFFERS` 是两个确定性生成器的分布差，不是语言模型，也不是 agent-general。回放里 45 行 `semantic_validity=UNKNOWN` 是没有物理字节，不是被丢掉的失败。
