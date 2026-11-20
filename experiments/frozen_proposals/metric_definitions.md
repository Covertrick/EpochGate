# 指标定义

本文件定义 Week 2 第一批主比较上的分项指标。数字全部从 `week2/week2_replay_joint.csv` 重算，没有重跑基准，也没有改写运行记录。字段名、指标标识和枚举值保持英文。

主结果是下面四项拆分。汇总指标 `COMPOSITE_EFE` 只出现在拆分表之后，不替代任何一项。

## 共同过滤

一行进入共同过滤，当且仅当同时成立：

1. `cohort` 为 `PILOT_FIRST_BATCH`。
2. `in_main_comparison` 为 `True`。
3. `scenario_id` 不是 `S09`。

按 `week2/week2_replay_joint.csv` 的行序保留，得到 56 行。这 56 行的 `joint_observed` 都是 `True`。`S09` 的 4 行 `in_main_comparison` 已是 `False`，第三式没有再删行。`SMOKE` 的 12 行不进入。

## 成功门

`@success` 各项先取共同过滤中的成功门：

1. `terminal_outcome` 为 `MATCH`。
2. `joint_observed` 为 `True`。
3. `adjudication` 不是 `UNRESOLVED`。

成功门共 26 行。其中未能独立裁决的 `MATCH` 行是 0。历史报告第 10 节的分母 26 就是这组行。

## 排除

下列规则适用于全部分项。被排除的行既不进入分子，也不进入分母。

- `UNKNOWN` 不是失败，也不是“没有失败”。
- `RESTORED_BYTES` 不是 `CONFIRMED_EFFECT`。两个字段分别阅读。
- `CORRECT_STOP` 不是后继失败。
- `NOT_APPLICABLE` 不是 `FAILURE`。
- `CONFLICT` 不是 `UNKNOWN` 与 `VALID` 之间的有序点。`evidence_state=CONFLICT` 不单独构成任何分项分子。
- 分子为 0 时，写出 0 和当时的分母，并说明该指标未被证据化。这不表示相应失败在世界上不存在。

## EFE_authority@success

历史报告第 10 节的 `EFE@success = 1/26` 更正为 `EFE_authority@success`。分子来源是 stale credential accepted，也就是 `authority_decision=STALE_ACCEPTED`。

- 分子：成功门内 `authority_decision=STALE_ACCEPTED` 的行。本次 1 行，`run_id` 为 `w2-PILOT_FIRST_BATCH-p06-Direct-1`。该行 `adjudication_basis=STALE_ACCEPTED`，`semantic_validity=VALID`，`physical_effect_state=CONFIRMED_EFFECT`。
- 分母：成功门内 `authority_decision` 不是 `UNKNOWN` 的行。本次 `UNKNOWN` 为 0，分母 26。
- 排除：成功门外的行；`authority_decision=UNKNOWN`。`WRONG_PHYSICAL`、`semantic_validity`、`successor_status`、`outdated_scenario_label`、`RESTORED_BYTES`、`CORRECT_STOP`、`NOT_APPLICABLE`、`CONFLICT` 都不构成本分子。
- 结果：1/26。这是权威分项。`NO_STALE_ACCEPTANCE_RECORDED` 只表示没有记录到过期接纳。

## EFE_semantic@success

- 分子：成功门内 `semantic_validity=INVALID` 的行。本次 0。
- 分母：成功门内 `semantic_validity` 为 `VALID` 或 `INVALID` 的行。本次 17，全部是 `VALID`。
- 排除：成功门内 `semantic_validity=UNKNOWN` 的 9 行。这 9 行的 `physical_effect_state` 都是 `ABSENT`。
- 结果：0/17。分子为 0，该指标在本周成功集上未被证据化。主比较另有 17 行 `semantic_validity=INVALID`，终态都是 `MISMATCH`，不进入 `@success`。

## EFE_successor@success

- 分子：成功门内 `successor_obligation=REQUIRED`，并且 `successor_status` 既不是 `COMPLETED`，也不是 `CORRECT_STOP`，也不是 `NOT_APPLICABLE`。本次 0。
- 分母：成功门内 `successor_obligation=REQUIRED`，并且 `successor_status` 不是 `CORRECT_STOP`、`NOT_APPLICABLE` 或 `UNKNOWN`。本次 0。
- 排除：成功门内 `CORRECT_STOP` 9 行，不是后继失败。成功门内 `successor_status=NOT_APPLICABLE` 17 行，不是 `FAILURE`。这两类与 `NOT_REQUIRED` 一起，把成功门的适用行减到 0。主比较里 `REQUIRED` 的 4 行终态都是 `MISMATCH`，不进入本分母。
- 结果：0/0。分子为 0，适用分母为 0，比率不可计算，该指标未被证据化。

人口计数另记，不并入本指标：共同过滤内 `successor_obligation=REQUIRED` 为 4，`successor_status=COMPLETED` 为 0。4 行都是 `S12` 的 `p15`，`successor_status=FAILED`，`terminal_outcome=MISMATCH`。本周没有实现 successor-aware rewrite，所以完成数是 0。这不是“后继普遍失败”。该维度本周没有判别力，不得写入贡献部分。

## EFE_outdated@success

本指标只在场景定义了 `outdated_scenario_label` 时计算。

- 场景已定义的标签：`TIMELY_NOTICE`、`NOTICE_MISSING_OR_DELAYED`。
- 分子：成功门内 `outdated_scenario_label=NOTICE_MISSING_OR_DELAYED`。本次 0。
- 分母：成功门内 `outdated_scenario_label` 为 `TIMELY_NOTICE` 或 `NOTICE_MISSING_OR_DELAYED`。本次 9，全部是 `TIMELY_NOTICE`。
- 排除：成功门内 `outdated_scenario_label=UNKNOWN` 的 17 行。`TIMELY_NOTICE` 留在分母，不进入分子，也不改写成 `STALE_ACCEPTED`。
- 结果：0/9。分子为 0，该指标在本周成功集上未被证据化。`S03` 另有 8 行 `NOTICE_MISSING_OR_DELAYED`，终态都是 `MISMATCH`，不进入 `@success`。

## 拆分表

| metric_id | 分子 | 分母 | 分数 | evidenced |
| --- | --- | --- | --- | --- |
| `EFE_authority@success` | 1 | 26 | 1/26 | true |
| `EFE_semantic@success` | 0 | 17 | 0/17 | false |
| `EFE_successor@success` | 0 | 0 | 0/0 | false |
| `EFE_outdated@success` | 0 | 9 | 0/9 | false |

四项的分母分别是 26、17、0、9，不能相加。

## 汇总：COMPOSITE_EFE

拆分表之后才给汇总。`COMPOSITE_EFE` 不是主结果。

- 分子：共同过滤内 `adjudication=CONFIRMED_FAILURE`。本次 18。
- 分母：共同过滤的 56 行。
- 依据：仅 `STALE_ACCEPTED` 1 行，仅 `WRONG_PHYSICAL` 8 行，`STALE_ACCEPTED+WRONG_PHYSICAL` 9 行，`DUPLICATE_EFFECT` 0 行。56 行的 `duplicate_effect` 都是 `False`。
- 结果：18/56。

`COMPOSITE_EFE` 的分母是 56。`EFE_authority@success` 的分母是 26。二者是两个指标。终态不匹配、`RESTORED_BYTES`、`CORRECT_STOP`、场景标签和 `UNKNOWN` 都不单独构成 `COMPOSITE_EFE` 的分子。
