# Week 1 历史数据审计

## 1. 执行摘要

决定是 **CONDITIONAL GO**。

`results/runtime/correctness/m4-confirm/summary.json` 的 72 行与文件头一致：72 格、20 行阻塞。52 行有 `run_id`，并与同目录 `evidence.json` 逐字段相同。这 52 条在同一条运行记录里同时留下终态和效果历史，载荷文件哈希与 `physical_digest` 一致，联合观察成立。20 条阻塞行只有 `model_no_effect` 和 `task_terminal_match: false`，没有接收端日志，联合观察不成立。这 20 个交叉没有用边缘计数填上。

因此效果历史失败率只在 52 条联合运行上可描述：EFE 为 28/52，EFE@success 为 6/16。全批 72 格上的 EFE@success 不可识别。会议 PDF 不在仓库中，Figure 2 的图内联合表无法核对。

进程中断和进程杀死不是掉电持久性。见第 8 节。

## 2. 源清单

机器清单是 `raw_manifest.csv`，972 行，自身哈希见 `raw_manifest.sha256`。971 个文件按原始字节计算了 SHA256。确认批、pilot、M5、M6、dedicated、v2 与更早的 `results/runtime/correctness/after` 等目录都在清单内。论文稿与协议 Markdown、以及 `scripts/m4_matrix.py`、`scripts/m6_score.py`、`scripts/reliability_contract.py` 也在清单内。

`EpochGate-conference.pdf` 未找到，清单中该行 readable 为 false。已完成格旁的 `ledger.sqlite` 与 `sink.sqlite` 只哈希、未查询。细节见 `source_inventory.md`。原始实验文件没有被修改。

## 3. PDF 证据审计

没有可读的会议 PDF，因此没有 Figure 2、Table 1 或 S3/B3 的页码引文。`docs/档案/v1.6/设计原文.md` 的图 2 是系统边界图，不是联合分布。`docs/现行/论文/稿件.md` 没有这张联合图。

S3/B3 的操作定义在 `docs/开发文档/M4-M6独立确认协议.md`：B3 在 Claim 与接收端之间中断，恢复只重放 Claim 字节；S3 是接收端执行中断。`docs/开发文档/实验方法.md` E6 写明 `process.kill()` 的检查不包含断电。`docs/开发文档/补实验结果.md` 写明掉电未测。完整对照见 `paper_evidence_map.md`。

## 4. 日志 schema 与字段映射

已完成格的证据对象包含 `run_id`、`task_id`、`arm`、`fault`、`task_terminal_match`、`stale_credential_accepted`、`wrong_physical`、`interrupted_recovered`、`successor_completed`、`correct_stop`、`oracle`、`receiver` 和摘要字段。阻塞行的键只有 `task_id`、`family`、`arm`、`fault`、`runtime`、`blocked`、`error_type`、`end_reason`、`task_terminal_match`、`protocol_sha256`、`model`。见 `summary.json` 中 `ful-order-c1` 的 S2 四行。

`proposal_id`、`session_id`、墙钟 `timestamp` 和 `token_was_stale` 在这些记录里不存在。聚类键由同一行的 `task_id` 与 `fault` 派生，并标明不是原始字段。映射表在 `data_dictionary.md`。

## 5. 标识符链接

52 条已完成行按 `task_id`、`arm`、`fault` 连到 `evidence.json`，`run_id` 一致且不重复。汇总行与证据文件内容相同，记为重复汇总，不记成 104 个样本。20 条阻塞行连不到证据目录，也没有 `run_id`。dedicated 四行没有 `run_id`。没有孤儿证据，没有键冲突，没有载荷哈希冲突。

`linkage_anomalies.csv` 118 行的构成：重复汇总 52、缺失证据目录 20、缺失 run_id 24、阻塞行上的终态标签 20、dedicated 载荷无运行记录 2。内容冲突为 0。见 `linkage_report.md`。

## 6. Figure 2 联合分布重建

图本身缺失。从 52 条联合运行读到的终态 × 权威计数是：MATCH 且未记录过期接纳 10，MATCH 且过期接纳 6，MISMATCH 且未记录过期接纳 32，MISMATCH 且过期接纳 4。终态 × 物理：MATCH 且 CONFIRMED_EFFECT 16，MISMATCH 且 ABSENT 14，MISMATCH 且 NOT_LEGAL 22。

与 `results/runtime/correctness/m6/summary.json` 的配对边缘一致：H4 为 0.4（2/5 个 S1 任务），H5 配对数 5，H6 配对数 4，`confirm_n` 为 52。H4 的两个同终态、不同过期接纳任务是 `ful-config-c6` 与 `ful-order-c3`，与 `docs/开发文档/M4-M6结果与审计.md` 的 H4 叙述一致。阻塞行没有被加进这些边缘。表见 `figure2_joint_reconstruction.md`。

## 7. 共享 proposal 重建

确认批任务是六个履约实例，故障是 S1、S2、S3，臂是 B0–B3。`(task_id, fault)` 共 18 个，每个有 4 条臂记录，合计 72 格。与文件头 72 一致。协议文本里的 144 含编码家族；本清单没有编码 `task_id`。`m5/summary.json` 写明编码家族可行性未通过。本审计不为未出现的编码任务补 NOT_RUN 行。清单网格内 NOT_RUN 为 0，MISSING_LOG 为 0，PARTIAL 为 0。

整组阻塞的 proposal 有 5 个、20 格：`ful-config-c4|S2`、`ful-config-c5|S1`、`ful-order-c1|S2`、`ful-order-c2|S3`、`ful-order-c3|S3`。与 `docs/开发文档/M4-M6结果与审计.md` 列出的阻塞任务—故障相同，也与 `docs/开发文档/M4-M6-v2-blocked审计.md` 的 20 格 `OUTCOME_UNKNOWN` 相同。状态写入 `proposal_registry.csv` 与 `cell_status.csv`：52 格 COMPLETED，20 格 BLOCKED。

`proposal_id` 作为聚类键使用，原始字段仍是缺席的。18 个 proposal 不是 72 个独立样本。

## 8. S3/B3 恢复重分类

有证据的 S3/B3 四格来自对应 `evidence.json`：

| 运行 | 恢复 | 物理 | 终态 | 裁决 |
| --- | --- | --- | --- | --- |
| `ful-order-c1-B3-S3-epochgate` | RESTORED_BYTES | NOT_LEGAL | MISMATCH | CONFIRMED_FAILURE，依据 WRONG_PHYSICAL |
| `ful-config-c4-B3-S3-epochgate` | RESTORED_BYTES | NOT_LEGAL | MISMATCH | CONFIRMED_FAILURE，依据 WRONG_PHYSICAL |
| `ful-config-c5-B3-S3-epochgate` | RESTORED_BYTES | NOT_LEGAL | MISMATCH | CONFIRMED_FAILURE，依据 WRONG_PHYSICAL |
| `ful-config-c6-B3-S3-epochgate` | RESTORED_BYTES | CONFIRMED_EFFECT | MATCH | NO_CONFIRMED_FAILURE |

`ful-config-c4__B3__S3/evidence.json` 中 `physical_digest` 等于 `old_digest`，不等于 `legal_digest`，`interrupted_recovered` 为 true，`verifiable_effect` 为 true。`verifiable_effect` 只说明物理字节与回执摘要一致。重放出来的字节不是归档法律摘要，所以物理状态是 NOT_LEGAL，恢复状态仍是 RESTORED_BYTES。

`ful-order-c2` 与 `ful-order-c3` 的 S3/B3 没有运行记录，恢复与物理都是 UNKNOWN。

`scripts/reliability_contract.py` 在中断时返回 `crashed_before_accept`，`resume_execution` 只重放 Claim 字节。该返回字符串不在 evidence 里。`docs/开发文档/实验方法.md` E6 与 `docs/开发文档/补实验结果.md` 把进程杀死和掉电分开，并写明掉电未测。这些 S3 记录不能当作掉电持久性实验。

dedicated 的撤销行在 `m4-dedicated/summary.json`：`correct_stop` 为 true，`successor_completed` 为 false，`successor_applicable` 为 false。归一化后停止为 CORRECT_STOP，后继为 NOT_APPLICABLE，裁决为 UNRESOLVED。没有把正确停止记成后继失败。这四行不在 72 格内。

## 9. Outcome Contract

冻结合同是 `outcome.schema.json`。映射说明是 `outcome_mapping.md`。例子在 `outcome_examples.jsonl`：五条来自上述真实记录，四条标为 synthetic，用来固定“阻塞终态 false 仍是 UNRESOLVED”“场景标签不写成过期接纳”“恢复字节不等于 CONFIRMED_EFFECT”“正确停止不写成后继失败”。

76 条写入 `archived_joint.csv` 的记录（72 条确认批加 4 条 dedicated）均通过该 schema。`schema_errors` 为 0。

## 10. 缺失与未解决证据

- 会议 PDF 缺失，Figure 2 与 Table 1 没有页级证据。
- 20 格没有 `run_id`、证据目录和接收端日志。它们的 `task_terminal_match: false` 保留在 `archived_joint_preliminary.csv` 的原始列，归一化终态为 UNKNOWN。
- `token_was_stale` 未归档。过期接纳为 false 只表示“没有记录到过期接纳”。
- SQLite 账本未解码，不能声称已从账本独立复核时效。
- 法律业务字节没有以独立文件保存，CONFIRMED_EFFECT 是与归档 `legal_digest` 及载荷文件哈希的一致，不是重跑 `score_business`。
- dedicated 的 supersede 载荷没有运行级终态。
- `session_id` 不存在，session 样本量为 0。

## 11. 指标可识别性

EFE 与 EFE@success 的定义在 `metric_definitions.md`。联合子集上：EFE 分子 28、分母 52；EFE@success 分子 6、分母 16。6 条成功上的失败都是过期接纳，分布在 3 个 proposal 的 B0 与 B1。16 条成功运行分布在 6 个 proposal 聚类上。

全批 72 格的联合分布不可恢复，因为 20 格没有效果历史。不估计这 20 格与 52 格的交叉。历史 H5 的 5 对、H6 的 4 对可以在未阻塞任务上重算，但 H6 比较的是恢复标志。

## 12. 统计单位评估

| 单位 | 本次计数 | 不与其他单位互换 |
| --- | --- | --- |
| proposal | 18 个 `(task_id, fault)`，其中 5 个整组阻塞 | 不是 72 |
| cell | 72，其中 BLOCKED 20、COMPLETED 52 | 一格是 proposal 的一条臂 |
| run | 52 个 `run_id`；20 格没有 run | 52 次运行嵌在 13 个已完成 proposal 中 |
| session | 0 | `session_id` 缺失 |
| H4 任务对 | 5 个未阻塞 S1 任务 | 不是 20 条 S1 运行 |
| H5 / H6 配对 | 5 与 4 | 任务配对，不是臂的独立样本 |
| pilot | 文件头 24 格、阻塞 4 | 不加入确认批 |
| M5 | 文件头 24 格、阻塞 6 | 不加入确认批 |
| dedicated | 4 行 | `in_matrix` 为 false |

`docs/开发文档/M4-M6结果与审计.md` 已写明确认批 6 个实例、4 臂、3 个故障。本清点与该结构一致，并保持这些 N 分开。

## 13. 验证结果

测试命令：

```text
.\.venv\Scripts\python.exe -m pytest docs/开发文档/week1-historical-audit/reconstruction/tests/test_constraints.py -q --tb=short
```

结果：10 passed。覆盖缺标识、阻塞 UNKNOWN、字段冲突、恢复但非法的载荷、正确停止、过期场景标签、重复记录，以及只有边缘计数时不得制造联合行。

重建命令：

```text
.\.venv\Scripts\python.exe docs/开发文档/week1-historical-audit/reconstruction/reconstruct.py --workspace c:\Project\EpochGate --out c:\Project\EpochGate\docs\开发文档\week1-historical-audit
```

结果：退出码 0。`validation_report.json` 中 `schema_valid` 为 true，`decision` 为 CONDITIONAL GO，确认批联合行 52，未解决 20，共享 proposal 18，阻塞格 20。

## 14. Go/No-Go 决定

**CONDITIONAL GO。**

原始运行记录支持一个可指明的子集：52/72 格可以在同一条记录上阅读终态和效果历史，并可用载荷文件核对物理摘要。20/72 格只有阻塞标签，效果历史不能独立裁决。缺失交叉保持缺失。会议图的联合表无法核对。过期接纳布尔值不能从缺失的 `token_was_stale` 或未解读的 SQLite 重新推导。

这不是 NO-GO：并不只剩下边缘汇总。`m6/summary.json` 虽是边缘配对，其背后的 52 条运行记录仍在。这也不是全批 GO：20 格联合观察不存在，Figure 2 页级证据不存在。

`docs/开发文档/M4-M6结果与审计.md` 里的历史 GO 是当时 H5 与 M5 规则下的结论。本决定不改写该文件，也不把历史 GO 扩大成 72 格上的 EFE@success。

## 15. Week 2 建议

下一步实证应是固定提案的反事实重放，对象是联合观察不存在的 5 个 proposal、20 格。重放要留下 `run_id`、接收端日志、法律字节和 `token_was_stale`，使终态与效果历史再次出现在同一条运行上。不要用本周的 52 条边缘去填这 20 格。

已完成的 13 个 proposal 可以继续当作描述性证据，推断时仍以 proposal 为聚类，而不是把 52 或 72 当成独立样本。若要复核过期接纳，需要解读或重放账本，而不是再读一遍现成布尔值。S3 重放若继续使用进程中断，报告里应继续把它与掉电持久性分开。不需要为了这份归档另立 Semantic Claim 或 Successor-Aware 协议。
