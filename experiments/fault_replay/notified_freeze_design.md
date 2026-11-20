# 通知冻结场景 S17–S18

权威通知在声明之后、物理发布之前到达。下面的行为只属于这两个场景，不另立协议名称。这里没有 Semantic Claim，没有 Successor-Aware Reconciliation，也没有类似 Cordon 的任务事务。

Claim 与 Outbox：

- 把旧声明冻在库里，`publish_allowed = 0`。
- 不把旧字节交给接收端。
- S17 的通知是 `supersede`。场景行为生成观察世界的合法载荷，并由同一个接收端接受。`successor_required = true`，`successor_status = COMPLETED_VALID`。
- S18 的通知是 `revoke`。合法载荷为空。路径停止，不发布。`successor_status = CORRECT_STOP`。正确停止不是后继失败，Week 1 后继状态记为 `NOT_APPLICABLE`。

Direct 与 Admission：

- 不读取发布前的通知。
- 继续发布旧字节。
- `successor_status = FAILED`。

S06 不是这个场景。S06 保持 `NON_DISCRIMINATIVE`，四条路径都会在 `after_admit_before_send` 之后交付旧字节，不进入主比较。S09 保持 `MECHANISM_SPECIFIC_DIAGNOSTIC`，也不进入主比较。

## 期望矩阵

| 场景 | Direct / Admission | Claim / Outbox |
| --- | --- | --- |
| S17 | 发布旧字节。语义 `INVALID`。权威 `STALE_ACCEPTED`。后继 `FAILED` | 不发布旧字节。接受合法后继。语义 `VALID`。后继 `COMPLETED_VALID` |
| S18 | 发布旧字节。语义 `INVALID`。后继 `FAILED` | 不发布。语义 `UNKNOWN`。后继 `CORRECT_STOP` |

`NOT_APPLICABLE` 不是失败。`COMPLETED_VALID` 不是 `CORRECT_STOP`。清单里的 `world_state_id`、oracle 版本和 `predicate.fulfillment.v1` 写在 `scenario_manifest/S17.yaml` 与 `S18.yaml`。
