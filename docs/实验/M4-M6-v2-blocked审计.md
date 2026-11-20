# 已有 blocked 逐格审计

原始文件没有改。标签只根据该格已经保存的字段。`model_no_effect` 没有工具轨迹时保持 `OUTCOME_UNKNOWN`。

历史 GO 仍以 `results/runtime/correctness/m6/summary.json` 为准，这里不重判。

## m4-pilot

分配 24 格，blocked 4 格。标签计数：{"OUTCOME_UNKNOWN": 4}。

按臂的 blocked 比例：
- B0：1/6
- B1：1/6
- B2：1/6
- B3：1/6

按故障的 blocked 比例：
- S1：0/8
- S2：4/8
- S3：0/8

任务与故障上的聚集：
- ful-order-p1 / S2：4/4，臂 B0, B1, B2, B3，整组阻塞 true

逐格：
- ful-order-p1 B0 S2：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-p1 B1 S2：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-p1 B2 S2：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-p1 B3 S2：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。

## m4-confirm

分配 72 格，blocked 20 格。标签计数：{"OUTCOME_UNKNOWN": 20}。

按臂的 blocked 比例：
- B0：5/18
- B1：5/18
- B2：5/18
- B3：5/18

按故障的 blocked 比例：
- S1：4/24
- S2：8/24
- S3：8/24

任务与故障上的聚集：
- ful-config-c4 / S2：4/4，臂 B0, B1, B2, B3，整组阻塞 true
- ful-config-c5 / S1：4/4，臂 B0, B1, B2, B3，整组阻塞 true
- ful-order-c1 / S2：4/4，臂 B0, B1, B2, B3，整组阻塞 true
- ful-order-c2 / S3：4/4，臂 B0, B1, B2, B3，整组阻塞 true
- ful-order-c3 / S3：4/4，臂 B0, B1, B2, B3，整组阻塞 true

逐格：
- ful-order-c1 B0 S2：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-c1 B1 S2：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-c1 B2 S2：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-c1 B3 S2：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-c2 B0 S3：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-c2 B1 S3：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-c2 B2 S3：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-c2 B3 S3：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-c3 B0 S3：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-c3 B1 S3：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-c3 B2 S3：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-order-c3 B3 S3：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-config-c4 B0 S2：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-config-c4 B1 S2：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-config-c4 B2 S2：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-config-c4 B3 S2：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-config-c5 B0 S1：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-config-c5 B1 S1：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-config-c5 B2 S1：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。
- ful-config-c5 B3 S1：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, end_reason, error_type, family, fault, model, protocol_sha256, runtime, task_id, task_terminal_match。

## m5

分配 24 格，blocked 6 格。标签计数：{"OUTCOME_UNKNOWN": 6}。

按臂的 blocked 比例：
- R0：3/12
- R1：3/12

按故障的 blocked 比例：
- interrupt_resume：2/6
- notice_delivered：2/6
- notice_missing：0/6
- response_lost：2/6

任务与故障上的聚集：
- ful-config-m3 / interrupt_resume：2/2，臂 R0, R1，整组阻塞 true
- ful-config-m3 / notice_delivered：2/2，臂 R0, R1，整组阻塞 true
- ful-config-m3 / response_lost：2/2，臂 R0, R1，整组阻塞 true

逐格：
- ful-config-m3 R0 notice_delivered：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, claim, error_type, family, fault, runtime, task_id。
- ful-config-m3 R1 notice_delivered：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, claim, error_type, family, fault, runtime, task_id。
- ful-config-m3 R0 response_lost：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, claim, error_type, family, fault, runtime, task_id。
- ful-config-m3 R1 response_lost：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, claim, error_type, family, fault, runtime, task_id。
- ful-config-m3 R0 interrupt_resume：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, claim, error_type, family, fault, runtime, task_id。
- ful-config-m3 R1 interrupt_resume：OUTCOME_UNKNOWN。该格只保存了 model_no_effect。没有工具事件、模型原文或 finish_reason，不能区分未行动、正确弃权或工具失败。 已存字段：arm, blocked, claim, error_type, family, fault, runtime, task_id。

Coding 家族不是 `model_no_effect` 格子。原始记录是 `feasibility_failed`：没有通过可行性检查的真实仓库进入本轮，不计为模型失败。这是整族没有进入矩阵，不把它改写成模型弃权。

## 敏感性分析

当前 `scripts/m4_matrix.py` 里，字符串 `model_no_effect` 只在对话接口返回成功、且没有 `submit_effect` 时写入。这个事实属于生成器源码，不是每一格保存下来的轨迹。

若有人把这条分支当成全部格子的证据，标签会一律变成 `MODEL_DID_NOT_ACT`，而不是 `MODEL_API_FAILED` 或 `TOOL_EXECUTION_FAILED`。即使这样，四臂的 blocked 比例仍然相同：同一份提案被复制到各臂，机制本身没有分到不同的模型失败。聚集在任务×故障，不在 B0/B1/B2/B3 之间。

本审计不采用这种升级。单格标签保持 `OUTCOME_UNKNOWN`。历史确认批因此仍有一批无法解释的分配实例。零宽 bootstrap 没有覆盖这些实例，不能当作跨任务精度证明。旧 `decision` 字段不改。
