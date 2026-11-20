# EpochGate

**面向工具调用型 Agent 的单机执行控制面。** 用 fencing token 管理提交资格，用幂等键和回执记录副作用，进程中断后根据目标端证据恢复。

*A single-node execution control plane for tool-using agents, with fenced commits, idempotent effects, and crash reconciliation.*

## 快速演示

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。两个演示均无需 API key 或 Docker。

```sh
uv run --locked --package epochgate-runtime egctl demo --scenario stale-write --offline
uv run --locked --package epochgate-runtime egctl demo --scenario crash-recovery --offline
```

- **旧执行失效**。控制面接收状态变化后，旧尝试显示 `REVOKED`，新尝试显示 `COMMITTED`。
- **写入后进程中断**。工作进程以退出码 86 中断；恢复从目标端确认效果，第二次恢复返回 `replay`。

两个演示都验证回执，并报告零次重复接受。终端输出报告路径，同目录的 `report.html` 可以直接打开查看时间线。

## 设计与边界

![代理、控制面和执行边界](docs/assets/architecture.png)

| 实现入口 | 职责 |
| --- | --- |
| [RunLedger](packages/runtime/src/epochgate_runtime/ledger.py) | 用 SQLite 持久化执行资格、提交状态和事件 |
| [文件 Publisher](packages/runtime/src/epochgate_runtime/files.py) / [EffectSink](packages/runtime/src/epochgate_runtime/sink.py) | 发布目标效果，核对幂等键、内容摘要与回执 |
| [恢复](packages/runtime/src/epochgate_runtime/recovery.py) | 根据目标端证据确认效果，证据不足时保留 `OUTCOME_UNKNOWN` |
| [调度](packages/runtime/src/epochgate_runtime/scheduler.py) | 管理执行槽位，等待进程实际退出后释放 |

资格检查依赖控制面收到相关状态变化。SQLite 事务覆盖账本操作，文件写入由发布与恢复流程对账。幂等和恢复保证限于实现了相应契约的目标。

## 验证

```sh
uv run --locked pytest packages/runtime/tests -q
```

这条命令验证现行控制面。[CI](.github/workflows/ci.yml) 另跑容器集成和历史研究契约。

[效果历史实验](experiments/effect_history/README.md)给出了一个可复核的例子。27 个受控场景的终态均匹配，其中 4 格曾落地业务无效效果，后来才由合法后继修正。

- [面试演示与设计取舍](PORTFOLIO.md)
- [完整架构与仓库布局](docs/现行/架构/仓库布局.md)
- [实验协议与复现索引](experiments/README.md)
