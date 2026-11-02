# 阶段一归档

探索性结果（不得与正式探针混写）：

- `results/real/matrix.jsonl`：正式 60 行
- `results/real/matrix_summary.json`：脚本汇总
- `results/real/preflight.jsonl`：不计分预检
- `results/real/matrix_aborted.jsonl`、`matrix_aborted_422.jsonl`：中断行，保留
- `results/real/baseline.jsonl`、`paired.jsonl`、`paired_short.jsonl`、`static_runs.jsonl`：更早真实 API 记录

正式探针（阶段 C/D 及以后）：

- `results/real/phase_c/`
- `results/real/phase_d/`

本目录只服务可复现：`manifest.json`、`row_index.jsonl`、`matrix_summary.recomputed.json`、提示词与锁哈希。

重算：

```text
uv run python scripts/recompute_phase1_summary.py
```
