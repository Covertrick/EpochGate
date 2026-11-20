# Week 2 审计更正报告

本报告只更正解释。它不重跑基准，不重写 `week2/run_manifest.jsonl`、`week2/run_events.jsonl`、`week2/proposal_registry.jsonl`、`week2/proposals/` 或 `week2/week2_replay_joint.csv`。`docs/开发文档/week1-historical-audit/archived_joint.csv` 不在修改范围内。原文决定仍是 `CONDITIONAL_GO`，第 1 节文字保留在 `week2/week2_report.md`。

## 1. 过滤器

源文件是 `week2/week2_replay_joint.csv`。保留同时满足下面三式的行，顺序与源文件一致：

1. `in_main_comparison` 为 `True`。
2. `cohort` 为 `PILOT_FIRST_BATCH`。
3. `scenario_id` 不是 `S09`。

得到 56 行。这 56 行的 `joint_observed` 都是 `True`。`S09` 的 4 个第一批单元格 `in_main_comparison` 已是 `False`。`SMOKE` 的 12 行不进入。成功门是这 56 行里 `terminal_outcome=MATCH` 且 `adjudication` 不是 `UNRESOLVED` 的行，共 26 行，未能独立裁决的 `MATCH` 行是 0。

计数见 `week2/correction_metrics.csv`。定义见 `week2/metric_definitions.md`。

## 2. 指标拆分

历史第 10 节把 1/26 写在 `EFE@success` 名下。更正后的名称是 `EFE_authority@success`。分子是成功门内 `authority_decision=STALE_ACCEPTED` 的 1 行，`run_id` 为 `w2-PILOT_FIRST_BATCH-p06-Direct-1`，`adjudication_basis=STALE_ACCEPTED`。分母是成功门内权威字段不是 `UNKNOWN` 的 26 行。本次权威 `UNKNOWN` 为 0。这个 1/26 只描述过期凭证被接纳。该行的 `semantic_validity` 是 `VALID`，`physical_effect_state` 是 `CONFIRMED_EFFECT`。

`EFE_semantic@success` 的分子是成功门内 `semantic_validity=INVALID`，本次 0。分母是成功门内语义为 `VALID` 或 `INVALID` 的 17 行。`semantic_validity=UNKNOWN` 的 9 行排除在分子和分母之外。`UNKNOWN` 不是失败，也不是“没有失败”。结果 0/17。分子为 0，该指标在本周成功集上未被证据化。主比较另有 17 行语义 `INVALID`，终态都是 `MISMATCH`，不进入 `@success`。

`EFE_successor@success` 的分子是成功门内 `successor_obligation=REQUIRED` 且后继未完成。`CORRECT_STOP` 不是后继失败，`NOT_APPLICABLE` 不是 `FAILURE`。成功门 26 行里，`CORRECT_STOP` 9 行、`successor_status=NOT_APPLICABLE` 17 行，适用分母是 0，分子是 0。结果 0/0，比率不可计算，该指标未被证据化。

`EFE_outdated@success` 只在场景定义了 `outdated_scenario_label` 时计算。分子是成功门内 `NOTICE_MISSING_OR_DELAYED`，本次 0。分母是成功门内标签为 `TIMELY_NOTICE` 或 `NOTICE_MISSING_OR_DELAYED` 的 9 行，本次全部是 `TIMELY_NOTICE`。`UNKNOWN` 标签 17 行排除在外。`TIMELY_NOTICE` 不改写成 `STALE_ACCEPTED`。结果 0/9。分子为 0，该指标在本周成功集上未被证据化。`S03` 有 8 行 `NOTICE_MISSING_OR_DELAYED`，终态都是 `MISMATCH`，不进入成功门。

| metric_id | 分子 | 分母 | 分数 | evidenced |
| --- | --- | --- | --- | --- |
| `EFE_authority@success` | 1 | 26 | 1/26 | true |
| `EFE_semantic@success` | 0 | 17 | 0/17 | false |
| `EFE_successor@success` | 0 | 0 | 0/0 | false |
| `EFE_outdated@success` | 0 | 9 | 0/9 | false |

四项分母不同，不能相加。拆分表之后的汇总是 `COMPOSITE_EFE`：共同过滤内 `adjudication=CONFIRMED_FAILURE`，18/56。依据是仅 `STALE_ACCEPTED` 1 行、仅 `WRONG_PHYSICAL` 8 行、二者同时 9 行、`DUPLICATE_EFFECT` 0 行。`COMPOSITE_EFE` 不是主结果，也不是 `EFE_authority@success`。`RESTORED_BYTES` 不是 `CONFIRMED_EFFECT`。`CONFLICT` 不是 `UNKNOWN` 与 `VALID` 之间的有序点；主比较有 4 行 `evidence_state=CONFLICT`，它们不单独进入上述分子。

## 3. 物理状态与语义结果

行级交叉表在 `week2/physical_semantic_crosstab.csv`，共 56 行。`physical_state` 取自 `physical_effect_state`，`semantic_result` 取自 `semantic_validity`，并保留 `recovery_state` 以便核对 `RESTORED_BYTES`。过滤与第 1 节相同。

56 行只出现三种配对：`ABSENT` × `UNKNOWN` 22 行，`NOT_LEGAL` × `INVALID` 17 行，`CONFIRMED_EFFECT` × `VALID` 17 行。`physical_state=CONFIRMED_EFFECT` 且 `semantic_result=INVALID` 的行数是 0。`recovery_state=RESTORED_BYTES` 且 `semantic_result=INVALID` 的行数是 0。`RESTORED_BYTES` 共 4 行，语义都是 `VALID`，物理状态都是 `CONFIRMED_EFFECT`；这两个字段仍然分开阅读。

本周基准未产生物理有效但语义无效的分离案例。

一个场景标为 `PHYSICAL_AND_SEMANTIC_COUPLED`，当且仅当它的主比较行全部落在上述三种配对里，并且没有 `CONFIRMED_EFFECT` 或 `RESTORED_BYTES` 与 `INVALID` 的组合。`S01`、`S02`、`S03`、`S04`、`S05`、`S06`、`S07`、`S08`、`S10`、`S11`、`S12` 都满足，交叉表 56 行的 `coupling` 都是 `PHYSICAL_AND_SEMANTIC_COUPLED`。其中 `S03` 的 8 行和 `S06` 的 4 行全部是 `NOT_LEGAL` × `INVALID`。该标记只记录本周观察没有分离，不能当作可识别性证据。

## 4. S06：NON_DISCRIMINATIVE

`week2/scenario_manifest/S06.json` 的 `mechanism_role` 更正为 `NON_DISCRIMINATIVE`。`week2/scenario_manifest/index.json` 里 `S06` 的同一字段一并更正。运行记录里的 `mechanism_role` 仍是当时写入的 `MAIN_COMPARISON`，本更正不改那些文件。

`S06` 的故障点是 `after_admit_before_send`，故障动作是 `concurrent_edit`。第一批提案 `p08` 的四条路径 `Direct`、`Admission`、`Claim`、`Outbox` 在 `week2/run_events.jsonl` 里都有 `FAULT_APPLIED`，`point=after_admit_before_send`。联合表四行都是 `terminal_outcome=MISMATCH`、`authority_decision=STALE_ACCEPTED`、`physical_effect_state=NOT_LEGAL`、`semantic_validity=INVALID`、`adjudication_basis=STALE_ACCEPTED+WRONG_PHYSICAL`。钩子之后送出的字节不是编辑后的合法字节，四条路径都交付了旧字节。

四条路径的结果相同。`S06` 不能支持任何一条路径优于另一条，也不能进入 Claim advantage 叙述。

## 5. S08：IN_PROCESS_FAULT

主比较里历史 `runner_status=CRASHED` 的单元格是 4 个，全部在 `S08`，`crash_point=after_send_before_receipt`：

- `w2-PILOT_FIRST_BATCH-p10-Direct-1`
- `w2-PILOT_FIRST_BATCH-p10-Admission-1`
- `w2-PILOT_FIRST_BATCH-p11-Direct-1`
- `w2-PILOT_FIRST_BATCH-p11-Admission-1`

更正表 `week2/correction_metrics.csv` 的 `S08_IN_PROCESS_FAULT` 把这 4 格记为 `IN_PROCESS_FAULT`。`run_manifest.jsonl` 与 `week2_replay_joint.csv` 里的 `runner_status` 仍是 `CRASHED`。

`IN_PROCESS_FAULT` 表示进程内故障。它不是操作系统级 process kill，也不是 power loss。这 4 行的 `recovery_state` 是 `NOT_APPLICABLE`。它们不能支持恢复证据结论。

`week2/outbox_crash_matrix.csv` 的每一行增加 `fault_kind=IN_PROCESS`。`process_kill` 保持 false，`power_loss_claim` 保持 false。

## 6. 后继维度

共同过滤 56 行里 `successor_obligation=REQUIRED` 的是 4 行，`successor_status=COMPLETED` 的是 0 行。4 行都是 `S12` 的 `p15`，四条路径都是 `FAILED`，终态都是 `MISMATCH`。本周没有实现 successor-aware rewrite，所以完成数是 0。这不是“后继普遍失败”。该维度本周没有判别力，不得写入贡献部分。

`stop_correctness=CORRECT_STOP` 有 12 行，它们不是后继失败。`successor_status=NOT_APPLICABLE` 有 32 行，`NOT_APPLICABLE` 不是 `FAILURE`。`EFE_successor@success` 仍是上一节的 0/0，与这里的 4 和 0 不是同一个分母。

## 7. 未改写的记录

未改写 `week2/run_manifest.jsonl`、`week2/run_events.jsonl`、`week2/proposal_registry.jsonl`、`week2/proposals/`、`week2/week2_replay_joint.csv`。未改写 `docs/开发文档/week1-historical-audit/archived_joint.csv`。该文件开算前 SHA256 为 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`。
