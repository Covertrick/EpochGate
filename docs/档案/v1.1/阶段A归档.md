# 阶段 A：证据归档

日期：2026-11-02。快照提交 `1c795c5`。归档目录 `results/archive/phase1/`。

## 做了什么

- 原始 60 行仍在 `results/real/matrix.jsonl`，数字未改。中断文件保留。
- `manifest.json` 写明：行内 `code_version=6de2721` 与实际生成快照 `1c795c5` 不一致；`uv.lock` 的 sha256；提示词版本；重算命令。
- `row_index.jsonl` 为每行补上 `pair_id`、`request_id`、`run_key`，不改原行度量。
- `prompts/phase1-words-v1.txt` 保存系统提示与 goal 模板。
- `scripts/recompute_phase1_summary.py` 只读原始行，写出 `matrix_summary.recomputed.json`，并与 `results/real/matrix_summary.json` 比对。

## 探索与正式分开

| 路径 | 性质 |
| --- | --- |
| `results/real/matrix*.jsonl`、`preflight.jsonl`、`paired*.jsonl`、`baseline.jsonl` | 探索 / 早期个案 |
| `results/real/phase_c/`、`results/real/phase_d/` | 正式探针（阶段 C/D） |
| `results/archive/phase1/` | 可复现元数据，不是新实验 |

## 重算

```text
uv run python scripts/recompute_phase1_summary.py
```

成功时打印 `branch=uncertain` 和 `matches results/real/matrix_summary.json`。
