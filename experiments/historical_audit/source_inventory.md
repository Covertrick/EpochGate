# 源清单

盘点范围是 Week 1 要审计的确认批、与之对照的同目录实验归档、论文稿和字段来源脚本。清单写在 `raw_manifest.csv`，共 972 行：971 个可读文件做了 SHA256，另有 1 行记录缺失的 PDF。清单自身的哈希在 `raw_manifest.sha256`。

哈希按原始字节计算。JSON 能解析的标为 readable。`.sqlite` 与个别 `.yml` 能读到字节并计算哈希，内容未解码，备注为 `binary_or_non_text`。本审计没有把这些 SQLite 账本当成已经解读过的 token 时效证据。

## 确认批清单

正式联合重建使用：

- `results/runtime/correctness/m4-confirm/summary.json`。文件头 `n` 为 72，`blocked` 为 20，`rows` 长度 72，与逐行计数一致。
- 同目录下 52 个 `evidence.json`。阻塞格没有单元格目录。
- 同目录的 `shipment.json` 或 `release.json`。有物理字节的 38 个文件，其 SHA256 与记录里的 `physical_digest` 一致。14 个已完成格的 `physical_digest` 为 null，旁边没有载荷文件，与空的接收端日志一致。
- 已完成格旁的 `ledger.sqlite` 与 `sink.sqlite` 只进入字节清单，没有打开查询。

专用撤销对照在 `results/runtime/correctness/m4-dedicated/summary.json`，4 行，`in_matrix` 为 false。`supersede-B2` 与 `supersede-B3` 下有 `shipment.json`，撤销两行没有对应目录。

## 不并入 72 格的邻近归档

这些文件在清单里，联合表不把它们加进确认批：

- `results/runtime/correctness/m4-pilot/summary.json` 文件头 `n` 24、`blocked` 4。
- `results/runtime/correctness/m5/summary.json` 文件头 `n` 24、`blocked` 6。编码家族字段写明可行性未通过，不计为模型失败。
- `results/runtime/correctness/m6/summary.json` 是由未阻塞行算出的配对汇总，`confirm_n` 为 52。
- `results/runtime/correctness/v2-blocked-audit/audit.json` 与 v2 coding 目录是后续扩展，不进入本周联合表。
- `results/runtime/correctness/after/`、`before/`、`e4-*` 是更早的正确性批次，用于核对“进程杀死”与“掉电”的写法，不与 72 格相加。

## 论文与协议文本

读过的文本包括 `docs/现行/论文/稿件.md`、`docs/现行/论文/数据/01-主实验/主数字.md`、`docs/档案/v1.6/设计原文.md`、`docs/开发文档/M4-M6独立确认协议.md`、`docs/开发文档/M4-M6结果与审计.md`、`docs/开发文档/实验方法.md`、`docs/开发文档/补实验结果.md`、`docs/开发文档/正确性修复与补充实验.md`。字段来源脚本是 `scripts/m4_matrix.py`、`scripts/m6_score.py`、`scripts/reliability_contract.py`。

## 未找到或未解读

全仓库递归搜索没有发现任何 `.pdf`，也没有名为 `EpochGate-conference.pdf` 的文件。`c:\Project` 下按文件名搜索同样没有这份 PDF。因此没有页码，也没有从 PDF 抽出的图注。

`ledger.sqlite` 与 `sink.sqlite` 的字节已哈希，模式未解读。不能把“文件存在”写成“已经从账本独立复核了 token 是否过期”。
