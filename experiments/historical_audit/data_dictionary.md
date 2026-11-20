# 数据字典与字段映射

映射只使用记录里实际出现的键。没有的概念标 ABSENT，不新造标识。

## 标识

| 合同概念 | 原始字段 | 映射 |
| --- | --- | --- |
| run_id | `run_id` | 已完成格有，例如 `ful-order-c1-B0-S1-epochgate`。阻塞行与 dedicated 四行没有此键，记空 |
| proposal_id | ABSENT | 确认批记录没有 `proposal_id`。聚类键由同一行上的 `task_id` 与 `fault` 拼接为 `task_id\|fault`，来源标 `DERIVED_COMPOSITE_TASK_FAULT`。生成器 `scripts/m4_matrix.py` 的 `run_phase` 以 `(task_id, fault)` 缓存同一份模型提案并复制到四臂，但缓存键没有写入 JSON |
| task_id | `task_id` | 确认批六值：`ful-order-c1`、`ful-order-c2`、`ful-order-c3`、`ful-config-c4`、`ful-config-c5`、`ful-config-c6`。dedicated 行没有 |
| path | `arm` | 实验路径是臂：`B0`、`B1`、`B2`、`B3`。来源标 `ORIGINAL_FIELD_arm`。单元格目录名 `{task_id}__{arm}__{fault}` 由这三个键组成 |
| session_id | ABSENT | 确认批与 dedicated 的顶层键里没有 `session_id`。列为空，来源 `ABSENT` |
| timestamp | ABSENT | 没有墙钟字段。`native.timeline[].t` 是单调计数，本重建不用它做链接 |
| attempt_id | `attempt_id` | 已完成格有，形如 `{run_id}-old`。不单独当作第二样本 |

## 结果

| 合同维度 | 原始字段 | 说明 |
| --- | --- | --- |
| 终态 | `task_terminal_match`，并在 `oracle` 中重复 | 已完成且与 `oracle` 一致时，true 为 MATCH，false 为 MISMATCH。阻塞行也写了 false，证据不足，归一化为 UNKNOWN |
| 权威决定 | `stale_credential_accepted` | true 为 STALE_ACCEPTED。false 为 NO_STALE_ACCEPTANCE_RECORDED。`token_was_stale` 未入库，false 不改写成“已拒绝过期凭证”。`fault` 不参与此列 |
| 物理效果 | `physical_digest`、`legal_digest`、`wrong_physical`、`verifiable_effect`、`receiver.physical_digest`、旁路 `shipment.json` 或 `release.json` | 文件哈希等于 `physical_digest`、且该摘要等于 `legal_digest`、且 `verifiable_effect` 为 true、且 `wrong_physical` 为 false 时，为 CONFIRMED_EFFECT。`wrong_physical` 为 true 且两摘要不同，为 NOT_LEGAL。接收端摘要为 null 且 `wrong_physical` 为 false，为 ABSENT |
| 证据 | `receiver`、`blocked`、`error_type` | 已完成格有 `receiver` 对象则为 SUFFICIENT。阻塞行没有接收端日志，为 INSUFFICIENT |
| 语义 | `task_terminal_match` | 仅在终态可裁决时：MATCH 对应 VALID，MISMATCH 对应 INVALID。阻塞行的 false 不进入 INVALID |
| 恢复 | `interrupted_recovered` | true 为 RESTORED_BYTES，false 为 NOT_RECOVERED，null 为 NOT_APPLICABLE，缺键为 UNKNOWN。此字段不提升物理状态 |
| 后继义务 | `successor_applicable` | true 为 OBLIGATED，false 为 NOT_OBLIGATED |
| 后继状态 | `successor_completed` 与 `successor_applicable`、`correct_stop` | 完成后为 COMPLETED。义务为假时为 NOT_APPLICABLE。有义务且未完成为 NOT_COMPLETED。枚举里没有 FAILURE |
| 停止正确性 | `correct_stop` | true 为 CORRECT_STOP，false 为 NOT_CORRECT_STOP |
| 场景标签 | `fault` | S1 为 TIMELY_NOTICE，S2 为 NOTICE_MISSING_OR_DELAYED，S3 为 INTERRUPT_BEFORE_ACCEPT。只作标签 |
| 预言/评分 | `oracle` 与顶层同名键 | 顶层是 `oracle` 的展开。两者不一致则整行 CONFLICT，裁决 UNRESOLVED |

`scripts/reliability_contract.py` 的 `score_business` 在同一次调用里同时写下终态和 `stale_credential_accepted`。归档 JSON 把两列都留下了。本重建分别阅读这两列，不用其中一列重算另一列。法律字节原文和 `token_was_stale` 没有另存，所以物理确认是“与归档 `legal_digest` 及文件字节一致”，不是重新执行业务函数。

## 统计单位

一个共享 proposal 是一个 `(task_id, fault)`。确认批实测 18 个，不是 72 个独立样本。一格是 proposal 再乘 `arm`。一次 run 是有 `run_id` 的已完成格。session 计数为 0。
