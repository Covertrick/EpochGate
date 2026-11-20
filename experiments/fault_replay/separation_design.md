# 分离场景 S13–S16

物理确认和语义有效是两件不同的事。密封字节或目标摘要可以和冻结声明一致，同时语义 oracle 对声明之后的世界返回 `INVALID`。

Week 2 的 `physical_effect_state` 用“字节是否等于评分世界的合法载荷”来决定 `CONFIRMED_EFFECT` 或 `NOT_LEGAL`。那会把物理非法和语义无效收成同一个条件。本周的分离格子不这样做。

- `physical_basis = CLAIM_DIGEST`：目标字节等于冻结声明时，前向发布记为 `CONFIRMED_EFFECT`。
- 新进程从密封字节重新写出目标时，记为 `RESTORED_BYTES`，物理列是 `PRESENT_UNVERIFIED`。`RESTORED_BYTES` 不是 `CONFIRMED_EFFECT`。
- 语义列只调用 Week 2 `semantics_oracle` 1.0.0，评分世界是 `observed_world`，谓词是 `predicate.fulfillment.v1`。
- 这些场景的世界变化是 `drift`，权威 token 不变。场景标签 `NOTICE_MISSING_OR_DELAYED` 不是 `STALE_AUTHORITY`。
- 四个检查点与 B-1 对齐。Claim 和 Outbox 的主运行各做一次真实进程杀死。10 次重复只在 `crash_matrix.csv`。

## 期望矩阵

| 场景 | 检查点 | Direct / Admission | Claim / Outbox |
| --- | --- | --- | --- |
| S13 | after_claim_before_sealed | `CONFIRMED_EFFECT` + 语义 `INVALID` | 不发布。证据 `UNKNOWN`。物理 `ABSENT`。语义 `UNKNOWN` |
| S14 | after_sealed_before_replace | `CONFIRMED_EFFECT` + 语义 `INVALID` | 证据 `PUBLISH_SEALED`。`RESTORED_BYTES` + 语义 `INVALID` |
| S15 | after_replace_before_receipt | `CONFIRMED_EFFECT` + 语义 `INVALID` | 证据 `CONFIRM_TARGET`。`CONFIRMED_EFFECT` + 语义 `INVALID` |
| S16 | lost_receipt | `CONFIRMED_EFFECT` + 语义 `INVALID` | 回执缺失，证据 `PUBLISH_SEALED`。`RESTORED_BYTES` + 语义 `INVALID` |

S14 与 S16 产生 `RESTORED_BYTES` + 语义 `INVALID`。S13 的 Direct/Admission 与 S15 的四条路径产生 `CONFIRMED_EFFECT` + 语义 `INVALID`。

具体世界、`world_state_id`、oracle 版本和谓词版本写在 `scenario_manifest/S13.yaml` 到 `S16.yaml`。期望字段不是 oracle 的输入。
