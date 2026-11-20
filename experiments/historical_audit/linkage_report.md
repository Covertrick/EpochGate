# 标识符链接

链接只用记录内的显式键：`task_id`、`arm`、`fault`、`run_id`。目录名 `{task_id}__{arm}__{fault}` 必须与 `evidence.json` 里的同名键一致。不用模糊匹配，也不用 `native.timeline[].t` 的接近程度。

## 确认批

`results/runtime/correctness/m4-confirm/summary.json` 有 72 行。其中 52 行与对应 `evidence.json` 逐字段相同，`run_id` 互不重复。这 52 行记为 `DUPLICATE_ROLLUP`：汇总文件重复了单元格记录，联合表只保留一行，来源同时指向 `summary.json#rows[i]` 和 `evidence.json`。

20 行 `blocked` 为 true，`error_type` 为 `model_no_effect`，没有 `run_id`，没有单元格目录。键仍然是该行上的 `task_id`、`arm`、`fault`。它们进入联合表时 `joint_observed` 为 false，原因 `BLOCKED_NO_EFFECT_HISTORY`。

没有孤儿 `evidence.json`。没有目录键与文件键冲突。没有汇总行与证据文件内容冲突。38 个载荷文件的 SHA256 等于 `physical_digest`，没有摘要冲突。

## dedicated

`results/runtime/correctness/m4-dedicated/summary.json` 的 4 行都没有 `run_id` 和 `task_id`。`supersede` 与臂名能对应目录 `supersede-B2`、`supersede-B3` 里的 `shipment.json`，但摘要行没有 `physical_digest` 或终态，不能把文件字节裁决成 CONFIRMED_EFFECT。两行 revoke 没有目录。四行都是 UNRESOLVED。

## 异常计数

`linkage_anomalies.csv` 共 118 行，分类如下。

| 类型 | 行数 | 含义 |
| --- | --- | --- |
| DUPLICATE_ROLLUP | 52 | 汇总与 evidence 相同，不是第二个样本 |
| MISSING_EVIDENCE_DIRECTORY | 20 | 阻塞格没有 evidence 目录 |
| MISSING_RUN_ID | 24 | 20 个阻塞格加 4 行 dedicated |
| TERMINAL_LABEL_ON_INSUFFICIENT_EVIDENCE | 20 | 阻塞行保存了 `task_terminal_match: false`，归一化时不用 |
| ARTIFACT_WITHOUT_RUN_RECORD | 2 | dedicated 的 supersede 载荷没有运行记录 |

冲突类（内容不一致、孤儿证据、摘要与文件哈希不符）为 0。

## 联合观察是否可恢复

52 个已完成格在同一条 `evidence.json` 里同时有可裁决终态和可裁决效果历史：`task_terminal_match`、`stale_credential_accepted`、`wrong_physical`、`receiver`，以及在有字节时与 `physical_digest` 一致的载荷文件。这 52 行 `joint_observed` 为 true。

20 个阻塞格只有汇总标签。终态 false 不进入联合表的 MISMATCH 边缘，过期接纳也不填成 false。缺失交叉保持缺失。

因此联合分布在确认批上部分可恢复，覆盖 52/72。Figure 2 作为会议图不存在于可读材料中，不能与图中单元格对账。能对上的是 `results/runtime/correctness/m6/summary.json`：重算的 H4 为 2/5 = 0.4，H5 配对数 5，H6 配对数 4，与该文件一致。重算时丢掉了阻塞行，与 `scripts/m6_score.py` 里 `_rows` 只保留未阻塞行的做法一致。

EFE@success 只在这 52 行的终态 MATCH 子集上可识别。分母是 16 条联合成功运行，不是 72，也不是把阻塞行算进去之后的边缘成功数。详见 `figure2_joint_reconstruction.md` 与 `metric_definitions.md`。
