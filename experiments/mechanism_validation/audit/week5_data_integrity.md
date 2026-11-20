# Week 5 数据完整性（独立重算）

统计日期 2026-11-20。本文件只记录核对结果，不删除、不改正任何原始行。原始表、Week 4 重算文件和 `archived_joint.csv` 都没有改。

## 清单合计

异常按条目计。重复 `run_id`、缺失格子、散列不一致、与幸存目标账本矛盾、内部列矛盾都是 0。目标效果计数未能核对的行是 254：回放 95 行，语义矩阵 159 行。清单条目合计 **254**。这 254 行是核对缺口，不是已经证实的计数错误。

能够核对的回放行只有 `w5rep-w5c24-Claim`。它声称 `effect_count = 1`，幸存目标账本里同一幂等键的 `logical_effect_count = 1`。

## 重复 run_id

`replay_joint.csv`、`ablation_results.csv`、`toctou_matrix.csv`、`rsr_truth_table.csv` 都没有重复的 `run_id`。列表为空。

回放里每个 `(proposal_id, scenario_id, path_id)` 也只出现一次。没有把重复尝试当成额外簇，因为回放的 `attempt_id` 全部是 `r01`。

## 缺失格子

清单有 24 个簇。预期格子是这 24 个簇乘以 Direct、Admission、Claim、Outbox，共 96。观察是 96。缺失列表为空，多余格子为空。

语义矩阵每个 `(proposal_id, scenario_id, fault_id, attempt_id)` 都有 R0、R1、R2。各故障的尝试号没有空洞。`ablation_results.csv` 的 159 个 `run_id` 等于 `rsr_truth_table.csv` 的 9 个加上 `toctou_matrix.csv` 的 150 个，两边不相交。

## 散列不一致

下列核对都相符，不一致列表为空。

- 回放清单的规范 JSON SHA256 与 `replay_manifest.sha256` 都是 `44a43eeed02003e900b258bc14eaa2776d875efcc199c1bb23df707b62c329df`。
- 24 个载荷文件、24 个后继载荷文件与清单中的 SHA256 和长度相符。
- `world_before`、`authority_initial`、`parameters`、`fault_schedule` 的规范散列与清单相符。
- 96 行联合表的 `proposal_hash`、`world_hash`、`authority_hash` 与对应清单簇相符。
- 回放和消融记录的 `code_hash` 都是 `93455ee6058a84622de7d79d9b8a7b1788224723b0e9c4d19b1844967e8bcaa6`，与当前 `week5/runtime` 下 `*.py` 按文件名排序后的散列相同。

## 目标效果计数

回放目标库是 `week5/experiments/replay_receiver/receiver.sqlite`。`target_effects` 只剩一行：`w5:w5c24:HOLD-13`，`logical_effect_count = 1`。四条路径共用这个幂等键，路径之间会清空账本。按时间最后写入的是 `w5rep-w5c24-Claim`，声称计数 1，与这一行相符。`w5c24` 的另外三条路径也声称 1，但它们的写入已经在后续路径开始前被清掉，不能用最后这一行回头核对。

`week5/experiments/replay_work` 下 48 个本地 SQLite 是 Claim 或 Outbox 自己的表，没有 `logical_effect_count`，也没有 `target_effects`。它们不能代替目标账本。

语义目标库是 `week5/semantic/runs/receiver/receiver.sqlite`。`target_effects` 是空的。最后一行 `w5sem-toctou-e-R2-r10` 声称 `effect_count = 0`，空表与这一声称相容，但表里没有该键的计数字段可读。其余行的键已经不在表中。其中 115 行声称计数大于 0。为了不把“键不存在”写成已经核对为 0，159 行全部列入未能核对。没有“键仍在且计数不符”的行。

### 回放中未能核对的 95 个 run_id

- `w5rep-w5c01-Direct`
- `w5rep-w5c01-Admission`
- `w5rep-w5c01-Claim`
- `w5rep-w5c01-Outbox`
- `w5rep-w5c02-Admission`
- `w5rep-w5c02-Claim`
- `w5rep-w5c02-Outbox`
- `w5rep-w5c02-Direct`
- `w5rep-w5c03-Claim`
- `w5rep-w5c03-Outbox`
- `w5rep-w5c03-Direct`
- `w5rep-w5c03-Admission`
- `w5rep-w5c04-Outbox`
- `w5rep-w5c04-Direct`
- `w5rep-w5c04-Admission`
- `w5rep-w5c04-Claim`
- `w5rep-w5c05-Direct`
- `w5rep-w5c05-Admission`
- `w5rep-w5c05-Claim`
- `w5rep-w5c05-Outbox`
- `w5rep-w5c06-Admission`
- `w5rep-w5c06-Claim`
- `w5rep-w5c06-Outbox`
- `w5rep-w5c06-Direct`
- `w5rep-w5c07-Claim`
- `w5rep-w5c07-Outbox`
- `w5rep-w5c07-Direct`
- `w5rep-w5c07-Admission`
- `w5rep-w5c08-Outbox`
- `w5rep-w5c08-Direct`
- `w5rep-w5c08-Admission`
- `w5rep-w5c08-Claim`
- `w5rep-w5c09-Direct`
- `w5rep-w5c09-Admission`
- `w5rep-w5c09-Claim`
- `w5rep-w5c09-Outbox`
- `w5rep-w5c10-Admission`
- `w5rep-w5c10-Claim`
- `w5rep-w5c10-Outbox`
- `w5rep-w5c10-Direct`
- `w5rep-w5c11-Claim`
- `w5rep-w5c11-Outbox`
- `w5rep-w5c11-Direct`
- `w5rep-w5c11-Admission`
- `w5rep-w5c12-Outbox`
- `w5rep-w5c12-Direct`
- `w5rep-w5c12-Admission`
- `w5rep-w5c12-Claim`
- `w5rep-w5c13-Direct`
- `w5rep-w5c13-Admission`
- `w5rep-w5c13-Claim`
- `w5rep-w5c13-Outbox`
- `w5rep-w5c14-Admission`
- `w5rep-w5c14-Claim`
- `w5rep-w5c14-Outbox`
- `w5rep-w5c14-Direct`
- `w5rep-w5c15-Claim`
- `w5rep-w5c15-Outbox`
- `w5rep-w5c15-Direct`
- `w5rep-w5c15-Admission`
- `w5rep-w5c16-Outbox`
- `w5rep-w5c16-Direct`
- `w5rep-w5c16-Admission`
- `w5rep-w5c16-Claim`
- `w5rep-w5c17-Direct`
- `w5rep-w5c17-Admission`
- `w5rep-w5c17-Claim`
- `w5rep-w5c17-Outbox`
- `w5rep-w5c18-Admission`
- `w5rep-w5c18-Claim`
- `w5rep-w5c18-Outbox`
- `w5rep-w5c18-Direct`
- `w5rep-w5c19-Claim`
- `w5rep-w5c19-Outbox`
- `w5rep-w5c19-Direct`
- `w5rep-w5c19-Admission`
- `w5rep-w5c20-Outbox`
- `w5rep-w5c20-Direct`
- `w5rep-w5c20-Admission`
- `w5rep-w5c20-Claim`
- `w5rep-w5c21-Direct`
- `w5rep-w5c21-Admission`
- `w5rep-w5c21-Claim`
- `w5rep-w5c21-Outbox`
- `w5rep-w5c22-Admission`
- `w5rep-w5c22-Claim`
- `w5rep-w5c22-Outbox`
- `w5rep-w5c22-Direct`
- `w5rep-w5c23-Claim`
- `w5rep-w5c23-Outbox`
- `w5rep-w5c23-Direct`
- `w5rep-w5c23-Admission`
- `w5rep-w5c24-Outbox`
- `w5rep-w5c24-Direct`
- `w5rep-w5c24-Admission`

### 语义矩阵中未能核对的 159 个 run_id

- `w5sem-sem-valid-R0-r01`
- `w5sem-sem-valid-R1-r01`
- `w5sem-sem-valid-R2-r01`
- `w5sem-sem-invalid-R0-r01`
- `w5sem-sem-invalid-R1-r01`
- `w5sem-sem-invalid-R2-r01`
- `w5sem-sem-unknown-R0-r01`
- `w5sem-sem-unknown-R1-r01`
- `w5sem-sem-unknown-R2-r01`
- `w5sem-toctou-a-R0-r01`
- `w5sem-toctou-a-R1-r01`
- `w5sem-toctou-a-R2-r01`
- `w5sem-toctou-a-R0-r02`
- `w5sem-toctou-a-R1-r02`
- `w5sem-toctou-a-R2-r02`
- `w5sem-toctou-a-R0-r03`
- `w5sem-toctou-a-R1-r03`
- `w5sem-toctou-a-R2-r03`
- `w5sem-toctou-a-R0-r04`
- `w5sem-toctou-a-R1-r04`
- `w5sem-toctou-a-R2-r04`
- `w5sem-toctou-a-R0-r05`
- `w5sem-toctou-a-R1-r05`
- `w5sem-toctou-a-R2-r05`
- `w5sem-toctou-a-R0-r06`
- `w5sem-toctou-a-R1-r06`
- `w5sem-toctou-a-R2-r06`
- `w5sem-toctou-a-R0-r07`
- `w5sem-toctou-a-R1-r07`
- `w5sem-toctou-a-R2-r07`
- `w5sem-toctou-a-R0-r08`
- `w5sem-toctou-a-R1-r08`
- `w5sem-toctou-a-R2-r08`
- `w5sem-toctou-a-R0-r09`
- `w5sem-toctou-a-R1-r09`
- `w5sem-toctou-a-R2-r09`
- `w5sem-toctou-a-R0-r10`
- `w5sem-toctou-a-R1-r10`
- `w5sem-toctou-a-R2-r10`
- `w5sem-toctou-b-R0-r01`
- `w5sem-toctou-b-R1-r01`
- `w5sem-toctou-b-R2-r01`
- `w5sem-toctou-b-R0-r02`
- `w5sem-toctou-b-R1-r02`
- `w5sem-toctou-b-R2-r02`
- `w5sem-toctou-b-R0-r03`
- `w5sem-toctou-b-R1-r03`
- `w5sem-toctou-b-R2-r03`
- `w5sem-toctou-b-R0-r04`
- `w5sem-toctou-b-R1-r04`
- `w5sem-toctou-b-R2-r04`
- `w5sem-toctou-b-R0-r05`
- `w5sem-toctou-b-R1-r05`
- `w5sem-toctou-b-R2-r05`
- `w5sem-toctou-b-R0-r06`
- `w5sem-toctou-b-R1-r06`
- `w5sem-toctou-b-R2-r06`
- `w5sem-toctou-b-R0-r07`
- `w5sem-toctou-b-R1-r07`
- `w5sem-toctou-b-R2-r07`
- `w5sem-toctou-b-R0-r08`
- `w5sem-toctou-b-R1-r08`
- `w5sem-toctou-b-R2-r08`
- `w5sem-toctou-b-R0-r09`
- `w5sem-toctou-b-R1-r09`
- `w5sem-toctou-b-R2-r09`
- `w5sem-toctou-b-R0-r10`
- `w5sem-toctou-b-R1-r10`
- `w5sem-toctou-b-R2-r10`
- `w5sem-toctou-c-R0-r01`
- `w5sem-toctou-c-R1-r01`
- `w5sem-toctou-c-R2-r01`
- `w5sem-toctou-c-R0-r02`
- `w5sem-toctou-c-R1-r02`
- `w5sem-toctou-c-R2-r02`
- `w5sem-toctou-c-R0-r03`
- `w5sem-toctou-c-R1-r03`
- `w5sem-toctou-c-R2-r03`
- `w5sem-toctou-c-R0-r04`
- `w5sem-toctou-c-R1-r04`
- `w5sem-toctou-c-R2-r04`
- `w5sem-toctou-c-R0-r05`
- `w5sem-toctou-c-R1-r05`
- `w5sem-toctou-c-R2-r05`
- `w5sem-toctou-c-R0-r06`
- `w5sem-toctou-c-R1-r06`
- `w5sem-toctou-c-R2-r06`
- `w5sem-toctou-c-R0-r07`
- `w5sem-toctou-c-R1-r07`
- `w5sem-toctou-c-R2-r07`
- `w5sem-toctou-c-R0-r08`
- `w5sem-toctou-c-R1-r08`
- `w5sem-toctou-c-R2-r08`
- `w5sem-toctou-c-R0-r09`
- `w5sem-toctou-c-R1-r09`
- `w5sem-toctou-c-R2-r09`
- `w5sem-toctou-c-R0-r10`
- `w5sem-toctou-c-R1-r10`
- `w5sem-toctou-c-R2-r10`
- `w5sem-toctou-d-R0-r01`
- `w5sem-toctou-d-R1-r01`
- `w5sem-toctou-d-R2-r01`
- `w5sem-toctou-d-R0-r02`
- `w5sem-toctou-d-R1-r02`
- `w5sem-toctou-d-R2-r02`
- `w5sem-toctou-d-R0-r03`
- `w5sem-toctou-d-R1-r03`
- `w5sem-toctou-d-R2-r03`
- `w5sem-toctou-d-R0-r04`
- `w5sem-toctou-d-R1-r04`
- `w5sem-toctou-d-R2-r04`
- `w5sem-toctou-d-R0-r05`
- `w5sem-toctou-d-R1-r05`
- `w5sem-toctou-d-R2-r05`
- `w5sem-toctou-d-R0-r06`
- `w5sem-toctou-d-R1-r06`
- `w5sem-toctou-d-R2-r06`
- `w5sem-toctou-d-R0-r07`
- `w5sem-toctou-d-R1-r07`
- `w5sem-toctou-d-R2-r07`
- `w5sem-toctou-d-R0-r08`
- `w5sem-toctou-d-R1-r08`
- `w5sem-toctou-d-R2-r08`
- `w5sem-toctou-d-R0-r09`
- `w5sem-toctou-d-R1-r09`
- `w5sem-toctou-d-R2-r09`
- `w5sem-toctou-d-R0-r10`
- `w5sem-toctou-d-R1-r10`
- `w5sem-toctou-d-R2-r10`
- `w5sem-toctou-e-R0-r01`
- `w5sem-toctou-e-R1-r01`
- `w5sem-toctou-e-R2-r01`
- `w5sem-toctou-e-R0-r02`
- `w5sem-toctou-e-R1-r02`
- `w5sem-toctou-e-R2-r02`
- `w5sem-toctou-e-R0-r03`
- `w5sem-toctou-e-R1-r03`
- `w5sem-toctou-e-R2-r03`
- `w5sem-toctou-e-R0-r04`
- `w5sem-toctou-e-R1-r04`
- `w5sem-toctou-e-R2-r04`
- `w5sem-toctou-e-R0-r05`
- `w5sem-toctou-e-R1-r05`
- `w5sem-toctou-e-R2-r05`
- `w5sem-toctou-e-R0-r06`
- `w5sem-toctou-e-R1-r06`
- `w5sem-toctou-e-R2-r06`
- `w5sem-toctou-e-R0-r07`
- `w5sem-toctou-e-R1-r07`
- `w5sem-toctou-e-R2-r07`
- `w5sem-toctou-e-R0-r08`
- `w5sem-toctou-e-R1-r08`
- `w5sem-toctou-e-R2-r08`
- `w5sem-toctou-e-R0-r09`
- `w5sem-toctou-e-R1-r09`
- `w5sem-toctou-e-R2-r09`
- `w5sem-toctou-e-R0-r10`
- `w5sem-toctou-e-R1-r10`
- `w5sem-toctou-e-R2-r10`

## 内部列

回放 96 行里，`duplicate_effect` 与 `effect_count > 1`、`published_new` 与 `effect_count` 的正负、`confirmed_effect` 与 `physical_effect_state == CONFIRMED_EFFECT` 都相符，`error` 都为空。语义 159 行里，`effect_landed` 与 `effect_count >= 1`、`duplicate_effect` 与 `effect_count > 1` 相符；无效或陈旧为 1 的行都有效果落地；R2 的围栏状态标签都是 `ATOMIC_SQLITE_TRANSACTION`，非 R2 都是 `NOT_IN_ARM`。内部矛盾列表为空。

## 过程杀死

回放 96 行和消融 159 行的 `process_kill` 都是 false，`power_loss_claim` 都是 false。这批没有操作系统杀死。掉电耐久没有被这些行测量。

## 归档散列

`docs/开发文档/week1-historical-audit/archived_joint.csv` 的 SHA256 是 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`。

