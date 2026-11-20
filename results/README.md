# 结果索引

`results/` 只放**能支撑结论的汇总**：summary JSON、逐格 CSV、报告和少量冻结账本。原始运行目录不在这里堆。

| 目录 | 内容 | 对应实验 |
| --- | --- | --- |
| `runtime/correctness/` | M4–M6 确认批、v2 编码批、E4 重复批 | 控制面正确性 |
| `runtime/benchmarks/` | 在线 B0/B1/B2 与提交段开销 | 运行时开销 |
| `runtime/stale_world*/` | 过期世界与通知条件 | 过期写入 |
| `experiments/effect-history/` | 27 格提交历史配对 | 终态评分盲区 |
| `experiments/configuration-selection/` | 18 格验收 + 96 格先导 | 配置选择与通知交互 |
| `experiments/recovery-obligation/` | 20 格恢复义务复核 | 恢复与业务有效性 |
| `experiments/git-effect-evidence/` | 24 格 Git 引用证据 + 2 次真实重试 | 真实工具的效果归属 |
| `experiments/package-withdrawal/` | Twine 重试与 yank 语义 | 显式撤回工具 |
| `archive/` | 旧版本汇总 | 历史 |

## 读结果的顺序

1. 先看每个实验目录的 `README.md`，它写了问题、协议和结论边界。
2. 再看 `report.json` / `summary.json`，所有主表都能从逐格证据重算。
3. 原始 `formal_runs/`、`replay_work/` 已在 `.gitignore` 中排除；需要时用对应实验的 `run` 命令重跑。

## 口径

- 不同批次的格子不是独立样本，不合并成一个成功率。
- 构造故障的发生频率不外推到生产。
- `UNKNOWN` 保留在分母里，不当作 0。
