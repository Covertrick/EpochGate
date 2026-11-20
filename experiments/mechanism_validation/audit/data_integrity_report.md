# 数据完整性报告

本次只读 Week 3 联合表、提案登记、正式运行目录、崩溃矩阵、Week 2 运行清单与事件，以及 Week 4 的原始运行表。没有删除任何行，也没有改写 `week2/`、`week3/`、`week4/`、`docs/` 或 `results/`。

Week 5 接收端账本一致性：`PENDING`。`week5/experiments` 不存在，因此没有可核对的 Week 5 接收端账本。`week5/semantic/ablation_results.csv` 也不存在。本次没有分析 Week 5 实验。

## 归档与计划

`docs/开发文档/week1-historical-audit/archived_joint.csv` 的 SHA256 是 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`。这与要求保留的散列相同。

`week3/statistical_plan.md` 的 SHA256 是 `cdc83a6031bb5c5138d6f1815cf52e5f6496cbd4ddb6028ca9c69a25282544aa`，与 `week3/statistical_plan.sha256` 相同。没有发现计划正文相对冻结散列的改动。

## 联合表规模与路径

`week3/week3_replay_joint.csv` 有 108 行，`cohort` 全部是 `WEEK3_FORMAL`。`run_id` 没有重复，也没有空的 `run_id`。每个簇都有 Direct、Admission、Claim、Outbox 各一行，簇内 `in_main_comparison` 没有混杂。

正式样本每条路径 27 行。主比较每条路径 25 行。主比较标志与排除 S06、S09 的规则一致，得到 25 个簇、100 个格子。

`week3/formal_runs` 有 108 个运行目录，与联合表的 `run_id` 一一对应。没有只有目录没有联合表行的运行，也没有只有联合表行没有目录的运行。

## 提案散列

联合表里的 27 个提案都能在 Week 2 或 Week 3 的 `proposal_registry.jsonl` 里找到。登记散列、提案文件 SHA256、联合表的 `payload_sha256` 三者一致，27/27。同一簇四条路径的载荷散列、`world_before_hash`、`world_after_hash` 和 `authority_initial_hash` 各自相同，并与登记一致。

每一行的 `payload_sha256_before`、`payload_sha256_after` 与 `payload_sha256` 相同，`payload_hash_match` 为真。没有发现运行前后提案字节被改写。

`week4/second_model_proposal_sanity.csv` 里与联合表提案号重叠的载荷散列，与联合表一致。

## 运行事件与缺失结果

Week 2 的 `run_manifest.jsonl` 有 72 行，`run_events.jsonl` 有 574 行。每一条清单行都有事件，都有 `RUN_STARTED`，非弃跑行都有终态事件。没有“清单行没有事件”的记录，也没有事件里的 `run_id` 缺少清单行。

Week 3 没有 `run_manifest.jsonl`，也没有 `run_events.jsonl`。Week 3 的对应记录是联合表行、`observation.json`，以及崩溃运行目录。S13 到 S18 的场景清单所绑定的提案，在联合表里都有四条路径。联合表里的每个场景都能在 Week 2 或 Week 3 的场景清单里找到。

106 条已观测运行的 `observation.json` 与联合表在 `run_id`、场景、提案散列、`runner_status`、`duplicate_effect`、`published_old_bytes`、恢复证据、后继义务和过期标签上一致，并且同目录有 `receiver_log.json` 与 `authority_log.json`。

## 失败是否入账

主比较里有 4 条 `runner_status == CRASHED`，都已写入联合表，`joint_observed` 为真，观察文件与联合表一致。它们留在主终点分母里：

- `w3-p10-Direct-1`，S08
- `w3-p10-Admission-1`，S08
- `w3-p11-Direct-1`，S08
- `w3-p11-Admission-1`，S08

没有发现 `BLOCKED`、`INVALID` 或 `ABANDONED` 行。没有发现崩溃状态写在观察文件里、联合表却写成 `COMPLETED` 的情况。

`week3/crash_matrix.csv` 有 80 行，`run_id` 不重复，并且都不在正式联合表里。`week3/kill_runs` 有 80 个同名目录。每一行都有 `marker.json`、`contract.json` 和 `recovery_result.json`。合同里的运行号、提案散列、世界散列、权威散列、提案号、检查点和路径与矩阵一致。

Week 4 原始表 `rsr_ablation.csv` 40 行，`crash_matrix_extended.csv` 60 行，`run_id` 都不重复。`week4/crash_runs` 有 100 个目录，与这两张表的运行号一致。每一行都有合同和恢复结果，合同中的运行号与表一致。这些重复运行没有进入 Week 3 主比较。RSR 表里 `SEMANTICALLY_SUPERSEDED` 为 20 行，S14 与 S16 的 `ByteClaim+RSR` 各 10 行。这是原始表计数，不是 Week 5 估计。

## 异常清单

异常按运行计，不把同一条短观察文件的每个缺字段再算成一条独立异常。异常数是 2。字段级自动核对把它们展开成 22 条差异，22 条全部属于这两次运行，已经保留，没有删除。

1. `w3-p12-Direct-1`，簇 `p12:S09`，路径 Direct。联合表记录 `runner_status=NOT_APPLICABLE`、`joint_observed=false`、`unobserved_reason=MECHANISM_NOT_APPLICABLE`、`in_main_comparison=false`。`observation.json` 只有 `mechanism_applicable: false` 和 `run_id`。目录里没有 `receiver_log.json` 和 `authority_log.json`。
2. `w3-p12-Admission-1`，同一提案和场景，路径 Admission。记录与上一条相同。

`week3/execute.py` 在路径不在提案的 `applicable_paths` 里时，会写成这种短观察文件，并把联合表标成 `NOT_APPLICABLE`。这两条因此是已经入账的不适用记录，不是丢失的失败，也不是主比较分母里的缺口。S09 的 Claim 与 Outbox 是完整观察。S09 整体不在 25 个主比较簇里。

除此之外，提案散列、重复 `run_id`、路径计数、缺失观察文件、未入账失败和可检测的事后改写都没有再发现异常。

## 目录未被修改

重算前后，下列目录的文件内容散列保持不变。

| 目录 | 文件数 | SHA256 |
| --- | --- | --- |
| week2 | 383 | `40c5fc05055b2faa91c7e6ee3f389136ab761ec58f1ea21534f4166523adf556` |
| week3 | 1403 | `ac193cfced8d60ecf1fd6c8e6f58e548bf5907c824335fb4c65dcd0123e6cb62` |
| week4 | 805 | `1c74a10af0b3d10fc9ac9f80e6ff4cf28f2cdb9a73765433cbada89a1e58d2a2` |
| docs | 206 | `a35a89573ba88a45c5a5d46275f58e50c99d4ba810d8bd2deb1726ae002f03e0` |
| results | 1156 | `2273c72ddf0163eb1c68e7d5560e58b46095d346b9b84f8bdceaed0aceb98e04` |

## 复现脚本

命令：

```text
c:\Project\EpochGate\.venv\Scripts\python.exe c:\Project\EpochGate\week5\audit\reproduction_check.py
```

退出码：0。联合表没有重复 `run_id`。脚本从 `week3/week3_replay_joint.csv` 重算主终点。主编码 `UNKNOWN_AS_ZERO`，25 个簇，4000 次，种子 20261120，百分位下标 99 与 3899。

Claim vs Direct authority：配对差 -0.44，95% 区间 [-0.64, -0.24]，差的合计 -11，不一致簇 11。同一次运行里的 semantic 对照是同一组数。脚本打印 `week5_estimates` 为 `NOT_COMPUTED`。
