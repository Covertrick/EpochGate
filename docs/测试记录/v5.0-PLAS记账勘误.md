# v5.0：Runtime-PLAS 取消后 attained 记账勘误

日期：2026-11-04。不覆盖冻结确认性 JSONL。

## 问题

生成主表的提交 `cb80009276ff32fe8fb4a627bed31a6de6ff5909` 与当前 `src/epochgate/replay.py` 在撤销路径上相同：

1. `_apply_event` 在 G1/G2 立即撤销时先把 `focal.remaining = 0.0`。
2. `release` 再以 `served = service_demand - remaining` 累加到 `attained[workflow_id]`。

因此，原服务需求 16 秒、事件在 3.2 秒、实际只跑过约 3.2 秒的取消任务，会把 **16 秒**记进工作流已执行服务，而不是约 3.2 秒。这与「Runtime-PLAS 使用实际已完成服务」的叙述不一致。

`G2-delayed-release` 与 G0 自然结束路径上，`remaining` 随时间递减后再释放，记账与实际服务一致。

## 对冻结主表的影响范围

- 有害早时机主格上，G0 与 G2 的焦点放槽时刻差由政策决定；稿件已指出早时机许多比较未产生调度排序差异。
- 本勘误**不宣告**全部结果无效，也**不重跑、不改写** `results/experiments/v2_1/confirmatory/traces.jsonl`。
- 5.0 及以后的回放若继续使用 PLAS，应改为在撤销时用 `service_demand - remaining_before_zeroing`（或等价的已服务墙钟）累加 attained，并在新协议中单独版本化；旧表仍以 `cb80009` 解释器为准。

## 核对命令（只读）

```powershell
git show cb80009276ff32fe8fb4a627bed31a6de6ff5909:src/epochgate/replay.py
```
