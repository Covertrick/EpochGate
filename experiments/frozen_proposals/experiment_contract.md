# Week 2 实验合同

类型名保持英文。本文件不定义 Semantic Claim，也不定义 Successor-Aware Reconciliation。

## EffectRequest

不可变。四条路径适配器收到同一对象，只允许原样传递 `payload` 字节。

字段：`proposal_id`、`task_id`、`scenario_id`、`payload`、`payload_sha256`、`idempotency_key`、`outbox_event_id`、`authority_token`、`authority_epoch`、`task_semantic_version`、`timeout_ms`、`budget_units`。

后继字节留在提案登记里，不放进 `EffectRequest`。路径因此不会另写一份后继载荷。

运行前和运行后都计算提案文件的 SHA256。与登记不一致则该次运行为 `INVALID`，该提案簇不再作为正式比较。

## Receiver

接口是 `SharedReceiver.accept(effect_key, attempt_id, payload) -> {status, applied, digest, receipt}`。实现类是 `reliability_contract.SharedReceiver`，版本字符串 `m4-shared-receiver-1`。四条路径使用同一个包装实例的规则：重复载荷不产生第二次逻辑效果，不同载荷记为冲突且不覆盖。

## FaultController

`hit(point)` 按冻结日程在该点应用动作。动作只来自日程：`revoke`、`supersede`、`drift`、`stale_epoch`、`concurrent_edit`、`duplicate_delivery`、`conflict_delivery`、`receiver_fail`、`crash`。`crash` 抛出进程内 `FaultCut`。这不是 `process.kill`，也不是掉电。

共享钩子，四条路径按同一顺序调用：`before_effect`、`authority_gate`、`after_admit_before_send`、`before_send`、`after_send_before_receipt`、`after_receipt_before_ack`、`after_local_ack`。

Claim 和 Outbox 在提交意图时额外调用 `before_db_transaction`、`inside_transaction`、`after_commit_before_send`。只使用这些额外钩子的场景是机制专用诊断。

## Path adapter

`authorize(ctx) -> bool`、`blob(ctx) -> bytes`、`has_durable(ctx) -> bool`、`mark_sending(ctx)`、`acknowledge(ctx, receipt)`。

- `Direct`：不读权威，对应 B0。
- `Admission`：在门闩处读权威并锁存，对应 B1。窗口内的权威变化不再读取。
- `Claim`：同库提交字节 Claim，恢复时重放这些字节，对应 B3，不是语义声明。
- `Outbox`：同库提交业务行和 outbox 行，提交后中继。不是 B2，也不声称优于其他路径。

B2（同一次决定里核对版本）不在这四条路径里。

## 提案清单

`proposal_registry.jsonl` 的每一行包含：`proposal_id`、`scenario_id`、`task_id`、载荷路径与 SHA256、任务语义版本、故障前世界与哈希、故障后世界与哈希、权威 token 与 epoch、后继义务、故障日程与哈希、种子、超时、预算、oracle 版本、代码提交、环境哈希、实现哈希。载荷字节只在冻结时由 `predicate.fulfillment.v1` 生成一次。

## 场景清单

每个 `week2/scenario_manifest/Sxx.json` 包含：`scenario_id`、`research_hypothesis`、`initial_state`、`fault_injection_point`、`fault_transition`、期望权威状态、期望物理效果状态、期望证据状态、期望语义状态、`successor_obligation`、`expected_stop_condition`、`oracle_fixture`、`applicable_paths`、`common_core`、`seed`。

期望字段是研究假设，不是 oracle 的输入。oracle 不读取它们。

## 运行事件

`run_events.jsonl` 的事件有 `RUN_STARTED`、`FAULT_APPLIED`、`ORACLE_RESULT`、`RUN_TERMINAL`、`RUN_ABANDONED`。公共字段是 `run_id`、`cohort`、`proposal_id`、`path`。`cohort` 只能是 `SMOKE` 或 `PILOT_FIRST_BATCH`（弃跑探测除外）。旧的 `run_id` 不覆盖。`RUN_STARTED` 之后没有终态事件的运行标为 `RUN_ABANDONED`，重试使用新的 `run_id`。

## OracleResult

五个 oracle 各自返回自己的值，互不覆盖。

- `terminal_oracle`：`MATCH`、`MISMATCH`、`UNKNOWN`
- `authority_oracle`：`STALE_ACCEPTED`、`NO_STALE_ACCEPTANCE_RECORDED`、`UNKNOWN`
- `evidence_oracle`：`SUFFICIENT`、`INSUFFICIENT`、`UNKNOWN`、`CONFLICT`
- `semantics_oracle`：`VALID`、`INVALID`、`UNKNOWN`
- `successor_oracle`：先 `REQUIRED`、`NOT_REQUIRED` 或 `UNKNOWN`，再 `COMPLETED`、`FAILED`、`CORRECT_STOP`、`INCORRECT_STOP`、`NOT_APPLICABLE` 或 `UNKNOWN`

物理效果状态和恢复状态按 Week 1 合同从观察直接计算：`CONFIRMED_EFFECT`、`NOT_LEGAL`、`ABSENT`、`PRESENT_UNVERIFIED`、`UNKNOWN`；恢复是 `RESTORED_BYTES`、`NOT_RECOVERED`、`NOT_APPLICABLE`、`UNKNOWN`。

裁决 `CONFIRMED_FAILURE` 只来自 `STALE_ACCEPTED`、`NOT_LEGAL` 或重复逻辑效果。`UNKNOWN` 得到 `UNRESOLVED`，不是失败。

Schema 文件：

- `week2/schemas/effect_request.schema.json`
- `week2/schemas/receiver.schema.json`
- `week2/schemas/fault_controller.schema.json`
- `week2/schemas/path_adapter.schema.json`
- `week2/schemas/proposal_manifest.schema.json`
- `week2/schemas/scenario_manifest.schema.json`
- `week2/schemas/run_event.schema.json`
- `week2/schemas/oracle_result.schema.json`
