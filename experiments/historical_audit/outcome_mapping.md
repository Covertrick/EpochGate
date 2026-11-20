# 结果映射

归一化由 `reconstruction/reconstruct.py` 的 `normalize_raw` 完成，并经 `outcome.schema.json` 校验。各维度分开赋值。

## 维度

- **终态** `terminal_outcome`：已完成记录的 `task_terminal_match`。true 为 MATCH，false 为 MISMATCH。`blocked: true` 时，即使原文字段为 false，结果也是 UNKNOWN。
- **权威** `authority_decision`：只读 `stale_credential_accepted`。true 为 STALE_ACCEPTED。false 为 NO_STALE_ACCEPTANCE_RECORDED。缺字段或冲突时为 UNKNOWN。故障标签不写入此列。false 不表示“拒绝了过期凭证”，因为 `token_was_stale` 没有归档。
- **物理效果** `physical_effect_state`：CONFIRMED_EFFECT 要求 `physical_digest` 等于 `legal_digest`、`verifiable_effect` 为 true、`wrong_physical` 为 false，且旁路载荷文件的 SHA256 等于 `physical_digest`。`wrong_physical` 为 true 且摘要不等，为 NOT_LEGAL。接收端没有字节，为 ABSENT。`interrupted_recovered` 不参与这个判断。
- **证据** `evidence_state`：有 `receiver` 且无冲突为 SUFFICIENT。阻塞或 dedicated 汇总为 INSUFFICIENT。
- **语义** `semantic_validity`：终态可裁决时 MATCH 为 VALID、MISMATCH 为 INVALID。UNKNOWN 终态保持 UNKNOWN。
- **恢复** `recovery_state`：`interrupted_recovered` true 为 RESTORED_BYTES，false 为 NOT_RECOVERED，null 为 NOT_APPLICABLE。
- **后继义务** `successor_obligation`：`successor_applicable`。
- **后继状态** `successor_status`：完成后 COMPLETED；没有义务时 NOT_APPLICABLE；有义务而未完成是 NOT_COMPLETED。CORRECT_STOP 且 `successor_applicable` 为 false 时为 NOT_APPLICABLE。
- **停止** `stop_correctness`：`correct_stop`。
- **场景标签** `outdated_scenario_label`：由 `fault` 映射，只描述场景。

## 裁决

`adjudication` 只有三个值。

- **CONFIRMED_FAILURE**：同一条运行同时可裁决终态和效果历史，且 `stale_credential_accepted`、`wrong_physical`、`duplicate_effect` 至少有一个为 true。依据字符串只写这些标志，不写恢复，也不写停止。
- **NO_CONFIRMED_FAILURE**：联合观察成立，且上述三个标志都是 false。终态 MISMATCH 本身不构成这个失败。物理字节缺失而 `wrong_physical` 为 false 的 14 行属于此类。
- **UNRESOLVED**：阻塞、缺 `run_id`、缺接收端、字段冲突，或效果历史无法独立阅读。`joint_observed` 为 false，并写 `unobserved_reason`。

UNKNOWN 不进入 CONFIRMED_FAILURE。RESTORED_BYTES 不进入 CONFIRMED_EFFECT 的依据。场景标签不进入 STALE_ACCEPTED。CORRECT_STOP 不进入后继失败状态。

顶层字段与 `oracle` 不一致、`receiver.physical_digest` 与顶层摘要不一致、终态 MATCH 与 `wrong_physical` true 同时出现、或文件哈希不等于 `physical_digest` 时，整行改为 UNRESOLVED。确认批实测没有这类冲突。

## 例子

`outcome_examples.jsonl` 中 `example_origin` 为 `archived` 的行来自真实记录，包括：

- `ful-order-c1-B0-S1-epochgate`：终态 MATCH、权威 STALE_ACCEPTED、物理 CONFIRMED_EFFECT，裁决 CONFIRMED_FAILURE。
- `summary.json` 中 `ful-order-c1` B0 S2：阻塞，终态 UNKNOWN，裁决 UNRESOLVED。
- `ful-order-c1-B3-S3-epochgate`：RESTORED_BYTES 且 NOT_LEGAL。
- `ful-config-c6-B3-S3-epochgate`：RESTORED_BYTES 且 CONFIRMED_EFFECT，依据不是恢复标志。
- dedicated `summary.json` 撤销 B2：CORRECT_STOP，后继 NOT_APPLICABLE，裁决 UNRESOLVED。

`example_origin` 为 `synthetic` 的四行标明为合成，用来固定同一组约束，不进入 `archived_joint.csv`。
