# Week 2 固定提案基准

## 1. 决定

**CONDITIONAL_GO**。冒烟公平性通过，主比较子集可以对照；S09 是机制专用诊断，主比较里仍有 CRASHED 或未覆盖单元格，不外推、不填补。

十六条记录在 `week2/week2_validation_report.json` 的 `criteria`：

- 1. 通过：proposal bytes/hash identical across the four paths。payload_sha256 compared within each proposal; file hash rechecked after each run。
- 2. 通过：task semantics identical。task_semantic_version。
- 3. 通过：initial world state identical。world_before_hash。
- 4. 通过：authority starting state identical。authority_initial_hash。
- 5. 通过：fault schedule identical。fault_schedule_hash。
- 6. 通过：receiver identical。receiver_impl and receiver_version。
- 7. 通过：oracle versions identical。oracle_versions_canonical。
- 8. 通过：timeout/budget identical。timeout_ms and budget_units。
- 9. 通过：every cell has a unique run_id。manifest rows 72, unique 72。
- 10. 通过：receiver/effect provenance exists。provenance_ref on every non-abandoned manifest row。
- 11. 通过：failures and UNKNOWN are retained。RUN_STARTED 72, closed 72。
- 12. 通过：outbox atomicity tests pass。pytest week2/oracle_test_suite/test_outbox_atomicity.py and outbox_crash_matrix.csv。
- 13. 通过：receiver idempotency tests pass。pytest week2/oracle_test_suite/test_receiver_idempotency.py; duplicate relay logical_effect_count 1。
- 14. 通过：oracle golden tests pass。pytest week2/oracle_test_suite/test_golden.py。
- 15. 通过：oracle mutation tests pass。pytest week2/oracle_test_suite/test_mutation.py。
- 16. 通过：fairness validator passes。smoke and pilot input equality。

## 2. 仓库侦察摘要

运行时是 Python 3.12.2，数据库是 SQLite 3.46.0。Direct、Admission、Claim 分别对应现有 B0、B1、B3 的字节交付行为；Outbox 是本周新增的同库事务发件箱。B2 没有被改名为 Outbox。细节和文件路径在 `week2/repository_recon.md`。Week 1 的 `outcome.schema.json` 没有 `version` 字段，`$id` 是 `https://epochgate.local/week1/outcome.schema.json`。该文件没有被修改。

## 3. 冻结接口

`EffectRequest`、接收端、故障控制器、路径适配器、提案清单、场景清单、运行事件和 `OracleResult` 写在 `week2/experiment_contract.md`，JSON Schema 在 `week2/schemas/`。四条路径收到的都是同一份不可变提案字节。提案文件在每次运行前后做 SHA256。

## 4. 场景清单

十二个场景在 `week2/scenario_manifest/`。S01 到 S08、S10 到 S12 的 `common_core` 为 true，进入四路径主比较。S09 的 `mechanism_role` 是 `MECHANISM_SPECIFIC_DIAGNOSTIC`：故障点 `after_commit_before_send` 只有 Claim 和 Outbox 会走到。Direct 与 Admission 没有同库持久意图，这两格记为 `NOT_APPLICABLE`，不记为失败，也不进入主比较。

S06 的并发编辑打在 `after_admit_before_send`。四条路径的本地决定都发生在这个钩子之前，发送时都不再核对权威。因此 S06 不是“Outbox 优于其他路径”的证据。

## 5. 提案冻结与公平性

第一批有 15 个提案，登记在 `week2/proposal_registry.jsonl`。载荷字节在 `week2/proposals/*.bin`，生成之后路径只读这些字节。冒烟公平性：通过。不一致项：无。依据是 `week2/smoke_gate.json` 与 `week2/week2_validation_report.json` 的 `smoke`。

公平性比较的是输入：提案标识、载荷 SHA256、任务语义版本、初始世界哈希、故障日程哈希、权威初态哈希、接收端实现和版本、oracle 版本、超时和预算、代码提交、环境哈希、实现哈希。它不比较终态或效果。

## 6. 五个独立 oracle

`terminal_oracle`、`authority_oracle`、`evidence_oracle`、`semantics_oracle`、`successor_oracle` 只读观察对象，互不调用。语义 oracle 使用清单里的计分世界快照和 `predicate.fulfillment.v1`，不读路径自报的语义字段。权威 oracle 用接收端接纳序号和权威日志的序号；排不出先后时返回 `UNKNOWN`。证据 oracle 可以返回 `CONFLICT`。后继 oracle 先判断 `REQUIRED`、`NOT_REQUIRED` 或 `UNKNOWN`，再给 `COMPLETED`、`FAILED`、`CORRECT_STOP`、`INCORRECT_STOP` 或 `NOT_APPLICABLE`。`CORRECT_STOP` 对应的 Week 1 后继状态是 `NOT_APPLICABLE`，不是 `NOT_COMPLETED`。

物理状态和恢复状态是从同一观察直接计算的合同字段，不读取其他 oracle 的返回值。`RESTORED_BYTES` 不会自动变成 `CONFIRMED_EFFECT`。

## 7. Same-DB Outbox 与故障矩阵

业务行和 outbox 行在同一个 SQLite 事务里提交，中继在提交之后。持久性声明只有这一句：same-database transactional insert of business row + outbox row; relay after commit; at-least-once delivery with receiver idempotency; in-process fault injection only。

矩阵在 `week2/outbox_crash_matrix.csv`。`process_kill` 全部为 false，`power_loss_claim` 全部为 false。本周没有使用 `process.kill`。进程内异常不是掉电。第一批运行读到的 `journal_mode` 是 delete，`synchronous` 是 2:FULL。没有声称任意 HTTP 恰好一次，也没有声称跨库原子性。

## 8. 冒烟门结果

冒烟是 `SMOKE`，使用 S01、S03、S08 的提案 `p01`、`p04`、`p10`，共 12 次运行。公平性通过。冒烟行不计入第一批指标。

## 9. 第一批 pilot 计数

这些数都来自 `week2/week2_replay_joint.csv`，标签是 `PILOT_FIRST_BATCH`。

- 提案：15。这是聚类单位。
- 单元格：60。每个提案有四条路径，所以单元格数是提案数的四倍，不是四倍的独立提案。
- 运行：60 个 `run_id`。
- 主比较提案：14。主比较单元格：56。S09 的 4 个单元格排除在主比较之外。
- 冒烟另有 12 次运行，不并入上面的单元格。
- 全部清单行 72，唯一 `run_id` 72，其中 `ABANDONED` 0。

一次运行每条路径只跑一次。60 个单元格不是 60 个独立提案，也没有统计功效声明。

## 10. 分项指标

主比较分母是 `in_main_comparison` 为 true 的行。联合观察是 `joint_observed` 为 true 的主比较行。

- 终态 MATCH：26。终态 MISMATCH：30。终态 UNKNOWN：0。
- 过期权威被接纳：10。没有记录到过期接纳：46。权威 UNKNOWN：0。`NO_STALE_ACCEPTANCE_RECORDED` 只表示没有记录到接纳了过期凭证。
- 非法物理效果：17。`CONFIRMED_EFFECT`：17。`ABSENT`：22。
- 可裁定的已观察效果里，语义 INVALID 17 / 34，比率 0.5。没有物理效果的行语义是 `UNKNOWN`，不进这个分母。
- 证据分布：{"SUFFICIENT": 52, "CONFLICT": 4}。
- 后继义务为 REQUIRED 的单元格 4，其中 COMPLETED 0，比率 0.0。本基准没有实现后继感知重写，所以这里的完成数可以是 0。
- 应当停止的单元格 20，其中 `CORRECT_STOP` 12，准确率 0.6。
- 主比较里任一主维度为 UNKNOWN 的单元格比率：0.392857（22 / 56）。UNKNOWN 不是失败。
- 主比较 CONFLICT 比率：0.071429（4 行）。CONFLICT 不是 UNKNOWN 和 VALID 之间的等级，也不单独构成 EFE 分子。
- 主比较平均延迟毫秒：{"Direct": 35.414, "Admission": 33.636, "Claim": 81.224, "Outbox": 83.179}。
- 主比较平均本地开销毫秒：{"Direct": 29.203, "Admission": 30.285, "Claim": 77.798, "Outbox": 80.117}。本地开销是路径时间减去接收端时间。

EFE 只在主比较的联合观察上计算：分子是 `adjudication` 为 `CONFIRMED_FAILURE` 的行，分母是联合观察行。依据只来自过期接纳、非法物理或重复逻辑效果。终态不匹配、恢复字节、正确停止、场景标签和 UNKNOWN 都不单独进分子。结果：18/56。联合覆盖率（联合观察 / 主比较单元格）是 1.0。

EFE@success 只在上述联合观察里终态为 MATCH、且裁决不是 UNRESOLVED 的行上计算。UNKNOWN 不进入分母，也不当成“没有失败”。结果：1/26。主比较终态 MATCH 共 26 行，其中未能独立裁决的 0 行。覆盖率（可裁决成功行 / 终态 MATCH 行）是 1.0。分子运行：w2-PILOT_FIRST_BATCH-p06-Direct-1。这些运行嵌在提案聚类里，不是独立任务。没有把 S3/B3 式的恢复率当成 EFE@success。`RESTORED_BYTES` 仍只是恢复字段。

按场景的主比较计数：

| 场景 | 提案 | 单元格 | 过期接纳 | 非法物理 | 终态 MATCH | 语义 INVALID | CONFLICT | 正确停止 | 后继 FAILED | CRASHED | 联合观察 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S01 | 2 | 8 | 2 | 2 | 6 | 2 | 0 | 6 | 0 | 0 | 8 |
| S02 | 1 | 4 | 1 | 1 | 0 | 1 | 0 | 3 | 0 | 0 | 4 |
| S03 | 2 | 8 | 0 | 8 | 0 | 8 | 0 | 0 | 0 | 0 | 8 |
| S04 | 1 | 4 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 4 |
| S05 | 1 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 4 |
| S06 | 1 | 4 | 4 | 4 | 0 | 4 | 0 | 0 | 0 | 0 | 4 |
| S07 | 1 | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 4 |
| S08 | 2 | 8 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 4 | 8 |
| S10 | 1 | 4 | 0 | 0 | 4 | 0 | 4 | 0 | 0 | 0 | 4 |
| S11 | 1 | 4 | 1 | 1 | 3 | 1 | 0 | 3 | 0 | 0 | 4 |
| S12 | 1 | 4 | 1 | 1 | 0 | 1 | 0 | 0 | 4 | 0 | 4 |

## 11. 与 Week 1 的边界

Week 1 的决定仍是历史审计里的 CONDITIONAL GO：联合观察 52/72，EFE 28/52，EFE@success 6/16。那些数字不是本周结果。本周行的 `cohort` 只有 `SMOKE` 或 `PILOT_FIRST_BATCH`。没有把本周行写入 `docs/开发文档/week1-historical-audit/archived_joint.csv`。该文件 SHA256 为 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`，与开跑前记录一致：true。缺失的历史格子保持缺失。

## 12. 科学边界声明

- 不实现 Semantic Claim，不实现 Successor-Aware Reconciliation，不实现类似 Cordon 的任务事务。
- 不声称 Outbox 普遍优于其他路径。S03 上四条路径都会交付过时字节；S06 上四条路径都会在决定之后的窗口里交付旧字节。
- 不声称任意 HTTP 恰好一次，不声称跨库原子性。
- 不把进程内故障或 `process.kill` 说成掉电。本周没有 `process.kill`。
- UNKNOWN 不是 FAILURE。RESTORED_BYTES 不是 CONFIRMED_EFFECT。OUTDATED 场景标签不是 STALE_AUTHORITY。CORRECT_STOP 不是后继失败。NOT_APPLICABLE 不是 FAILURE。CONFLICT 不是 UNKNOWN 与 VALID 之间的有序点。
- 每个提案只跑一次每条路径。提案是统计单位。

## 13. 复现命令

环境哈希是 Python 版本、平台、SQLite 版本和 `week2/hashing.py` 里列出的包版本的规范 JSON 的 SHA256。实现哈希是 `week2/**/*.py` 的 SHA256。Git HEAD 不包含本周新增的 `week2/` 树。

```text
.\.venv\Scripts\python.exe -m pytest week2/oracle_test_suite -q --tb=short
.\.venv\Scripts\python.exe -m week2.frozen_proposal_runner
```

第二条命令会再跑一遍测试，写入 `week2/pytest_output.txt`，生成冻结清单、冒烟、第一批、`week2/week2_replay_joint.csv`、`week2/week2_validation_report.json` 和本报告。测试输出摘录：

```text
.....................                                                    [100%]
21 passed in 20.42s
```

## Post-audit correction

本节更正解释，不删除上面的原文。第 1 节的决定仍是 **CONDITIONAL_GO**。计数从 `week2/week2_replay_joint.csv` 重算，过滤是 `cohort=PILOT_FIRST_BATCH` 且 `in_main_comparison=True` 且 `scenario_id` 不是 `S09`，共 56 行。定义、拆分表和汇总写在 `week2/metric_definitions.md` 与 `week2/week2_correction_report.md`。

### 指标拆分

第 10 节的 `EFE@success = 1/26` 更正为 `EFE_authority@success`。分子是成功门内 `authority_decision=STALE_ACCEPTED` 的 1 行，`run_id` 为 `w2-PILOT_FIRST_BATCH-p06-Direct-1`，依据是 stale credential accepted。分母是终态 `MATCH` 且权威字段不是 `UNKNOWN` 的 26 行。这个 1/26 只描述过期凭证被接纳。该行 `semantic_validity=VALID`，`physical_effect_state=CONFIRMED_EFFECT`。

`EFE_semantic@success` 是终态 `MATCH` 且 `semantic_validity=INVALID`。分子 0，分母是语义为 `VALID` 或 `INVALID` 的 17 行，结果 0/17。`UNKNOWN` 的 9 行排除在分子和分母之外。`UNKNOWN` 不是失败，也不是“没有失败”。分子为 0，该指标未被证据化。

`EFE_successor@success` 是终态 `MATCH` 且 `successor_obligation=REQUIRED` 且后继未完成。`CORRECT_STOP` 不是后继失败，`NOT_APPLICABLE` 不是 `FAILURE`。成功门里适用分母是 0，分子是 0，结果 0/0，该指标未被证据化。

`EFE_outdated@success` 只在场景定义了 `outdated_scenario_label` 时计算。分子是 `NOTICE_MISSING_OR_DELAYED`，本次 0。分母是 `TIMELY_NOTICE` 或 `NOTICE_MISSING_OR_DELAYED` 的 9 行，结果 0/9。`UNKNOWN` 标签的 17 行排除在外。分子为 0，该指标未被证据化。`S03` 的 8 行过时标签终态是 `MISMATCH`，不进入成功门。`TIMELY_NOTICE` 不改写成 `STALE_ACCEPTED`。

四项是主结果。拆分表之后的汇总 `COMPOSITE_EFE` 是 `adjudication=CONFIRMED_FAILURE` 的 18/56，依据为仅 `STALE_ACCEPTED` 1、仅 `WRONG_PHYSICAL` 8、二者同时 9、`DUPLICATE_EFFECT` 0。它不是上面的 1/26。`RESTORED_BYTES` 不是 `CONFIRMED_EFFECT`。`CONFLICT` 不是 `UNKNOWN` 与 `VALID` 之间的有序点。

### 物理状态与语义结果

`week2/physical_semantic_crosstab.csv` 是上述 56 行的 `physical_state` × `semantic_result` 交叉表，并带 `recovery_state` 与 `coupling`。配对只有 `ABSENT` × `UNKNOWN` 22、`NOT_LEGAL` × `INVALID` 17、`CONFIRMED_EFFECT` × `VALID` 17。`CONFIRMED_EFFECT` 或 `RESTORED_BYTES` 与 `semantic_result=INVALID` 同时出现的行数是 0。

本周基准未产生物理有效但语义无效的分离案例。

`S03`、`S06` 以及其余主比较场景 `S01`、`S02`、`S04`、`S05`、`S07`、`S08`、`S10`、`S11`、`S12` 的观察都落在这三种配对上，56 行的 `coupling` 都是 `PHYSICAL_AND_SEMANTIC_COUPLED`。该标记不能当作可识别性证据。

### S06：NON_DISCRIMINATIVE

`week2/scenario_manifest/S06.json` 与 `index.json` 中 `S06` 的 `mechanism_role` 更正为 `NON_DISCRIMINATIVE`。运行记录里的历史值仍是 `MAIN_COMPARISON`。`p08` 的 `Direct`、`Admission`、`Claim`、`Outbox` 都在 `after_admit_before_send` 触发 `concurrent_edit`，四行都是 `NOT_LEGAL`、`INVALID`、`STALE_ACCEPTED`、`MISMATCH`。四条路径在该钩子之后都交付了旧字节。`S06` 不能支持任何一条路径优于另一条，也不能进入 Claim advantage 叙述。

### S08：IN_PROCESS_FAULT

主比较里历史 `runner_status=CRASHED` 的 4 格都在 `S08`，路径是 `p10` 与 `p11` 的 `Direct` 和 `Admission`，`crash_point=after_send_before_receipt`。更正表把它们记为 `IN_PROCESS_FAULT`。`run_manifest.jsonl` 里的 `runner_status` 仍是 `CRASHED`。这是进程内故障，不是操作系统级 process kill，也不是 power loss。四行 `recovery_state=NOT_APPLICABLE`，不能支持恢复证据结论。`week2/outbox_crash_matrix.csv` 每一行的 `fault_kind` 为 `IN_PROCESS`。`process_kill` 保持 false，`power_loss_claim` 保持 false。

### 后继维度

主比较里 `successor_obligation=REQUIRED` 的单元格是 4，`COMPLETED` 是 0。4 行都是 `S12` 的 `p15`，终态 `MISMATCH`，`successor_status=FAILED`。本周没有实现 successor-aware rewrite，所以完成数是 0。这不是“后继普遍失败”。该维度本周没有判别力，不得写入贡献部分。`CORRECT_STOP` 的 12 行不是后继失败。
