# 分阶段实验复现

各阶段的实验入口、输入和证据保存在同一阶段目录。运行时实现、协议和实验结果的对应关系见 [实验与证据](../docs/实验/README.md)。

[提交历史配对实验](effect_history/README.md) 检查终态评分是否漏掉提交时的业务违规，并和现有故障调度作等预算对照。

| 阶段 | 目录 | 历史编号 |
| --- | --- | --- |
| [历史证据审计](historical_audit/README.md) | `historical_audit/` | Week 1 |
| [冻结提案对照](frozen_proposals/week2_report.md) | `frozen_proposals/` | Week 2 |
| [故障注入回放](fault_replay/week3_report.md) | `fault_replay/` | Week 3 |
| [崩溃恢复扩展](crash_recovery/statistical_robustness.md) | `crash_recovery/` | Week 4 |
| [机制与语义验证](mechanism_validation/baseline_status.md) | `mechanism_validation/` | Week 5 |

从仓库根目录执行下列 PowerShell 命令。实验模块和子进程从 `experiments/` 查找。

```powershell
.venv/Scripts/python.exe -m pytest experiments/historical_audit/reconstruction/tests -q
Push-Location experiments
try {
    ../.venv/Scripts/python.exe -m pytest frozen_proposals/oracle_test_suite -q
    ../.venv/Scripts/python.exe -m pytest fault_replay/tests -q
    ../.venv/Scripts/python.exe -m pytest crash_recovery/tests -q
    ../.venv/Scripts/python.exe -m pytest mechanism_validation/tests -q
} finally {
    Pop-Location
}
```

归档报告和清单保留生成时记录的路径和哈希。旧的 `weekN/` 路径和 `python -m weekN.…` 命令可按上表换成当前目录名或模块名。旧报告中的 `docs/开发文档/week1-historical-audit/` 对应 `experiments/historical_audit/`，其余 `docs/开发文档/` 路径对应 `docs/实验/`。重跑实验会按当前代码计算实现哈希，一些入口还会写出新的结果。

整理期间的验证误启动了两项崩溃实验，重写了 Week 3 的 `kill_runs/w3-kill-p10-Claim-after_claim_before_sealed-r01/` 中的 `child.log`、`contract.json` 和 `memory_probe.json`，并重建了 Week 4 的 `crash_runs/w4-recv-before_receiver_accept-r01` 至 `r09`。这些目录包含本次验证产生的文件，恢复原始证据需要原始备份。
