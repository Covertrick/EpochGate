# Week 1 历史数据审计

结论：**CONDITIONAL GO**。确认批 72 格里，52 格有运行级终态与效果历史的联合观察；20 格阻塞，没有效果历史，交叉格没有用边缘计数补齐。

## 产物

| 文件 | 作用 |
| --- | --- |
| `audit_report.md` | 最终审计，含 Go/No-Go |
| `outcome.schema.json` | 冻结的结果合同 |
| `archived_joint.csv` | 运行级联合观察。只有真实联合行的 `joint_observed` 为 true |
| `archived_joint_preliminary.csv` | 审计工作表，附原始字段，不填补缺失交叉 |
| `raw_manifest.csv` | 源文件清单与 SHA256 |
| `raw_manifest.sha256` | 清单文件本身的 SHA256 |
| `source_inventory.md` | 源范围与未读到的 PDF |
| `paper_evidence_map.md` | 论文页级证据。PDF 不在仓库中 |
| `data_dictionary.md` | 原始字段到合同字段的映射 |
| `run_linkage.csv` | 显式键链接 |
| `linkage_anomalies.csv` | 链接异常 |
| `linkage_report.md` | 链接与 Figure 2 可恢复性 |
| `figure2_joint_reconstruction.md` | 从运行记录重建的联合表 |
| `proposal_registry.csv` | 18 个共享 proposal 聚类 |
| `cell_status.csv` | 格状态 |
| `outcome_mapping.md` | 归一化规则 |
| `outcome_examples.jsonl` | 真实例子与标明 synthetic 的约束例子 |
| `metric_definitions.md` | EFE 与 EFE@success 的可识别条件 |
| `validation_report.json` | 机器可读校验 |
| `reconstruction/reconstruct.py` | 确定性重建程序 |
| `reconstruction/tests/test_constraints.py` | 约束测试 |

原始实验文件、PDF 搜索范围内的论文稿和已有审计文档都只读。本目录是新建产物。

## 如何再生机器可读产物

在仓库根目录执行：

```text
.\.venv\Scripts\python.exe -m pytest experiments/historical_audit/reconstruction/tests -q
.\.venv\Scripts\python.exe experiments/historical_audit/reconstruction/reconstruct.py --out .epochgate/week1-reconstruction
```

本次执行：测试 10 passed；重建程序退出码 0，`schema_valid` 为 true，决定为 CONDITIONAL GO。中文审计叙述根据这次 `validation_report.json` 写成。再跑程序会重写 CSV 与 JSON，不会改写 `results/` 里的原始记录。

`proposal_id` 列是由原始 `task_id` 与 `fault` 拼出的聚类键，原始记录里没有名为 `proposal_id` 的字段。`session_id` 为空。
