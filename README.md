# EpochGate

把 Agent 的**执行资格**和 **Runtime 槽位**分开：过期动作不能提交；槽位让不让出来，是另一件可以测量的事。

当前包版本 **4.0.0**。面试时先讲旁观者测量，再讲 v1.2 的安全不变量。两套数字不要混用。

## 30 秒

共享槽上有一个长任务正在生成，外部状态中途变化。

- **G0** 把失效尾巴读完，到提交边界才拒绝。
- **G1** 看见相关信号就停，并立刻重做。
- **G2** 先问前提还在不在，只有前提为假才停并放槽。

在 4 个槽的逻辑时钟回放里，前提已经失效时，G2 相对 G0 少做 12.8 秒失效占用，下一只槽先交给后继，旁观者 P95 等待仍在 12/12 个主格跨过预先冻结的门槛，大约少 1.6–2.6 秒。前提仍成立时，差的符号跟着 restart 长短走，不是 G2 处处更好。这是这条槽模型上的传导，不是新的队列定律。Retire 已经检查过无关租户，这里的主指标是旁观者等待。客户端停读不是 GPU 已回收。

数字、区间和不能说的话在 [docs/论文4.0.md](docs/论文4.0.md)。v1.2 的真实 API 槽位和安全性在 [docs/论文初稿.md](docs/论文初稿.md)。

## 本地运行

需要 Python 3.12+。本机没有把 `uv` 放进 PATH 时，用 `python -m uv`。

```powershell
python -m uv sync --extra dev
python -m uv run pytest -q
python -m uv run epochgate demo
python -m uv run epochgate explain t-explain --trace results/explain/demo.jsonl
```

`explain` 打印的是资格标签，不是性能数字：

```text
task=t-explain
stale_observation source=inventory reason=exogenous
eligibility_revoked reason=dirty and premise failed; revoke eligibility
late_output_rejected reason=attempt not eligible
late_output_rejected reason=insufficient stock
```

## 结果在哪

| 要看的东西 | 位置 |
| --- | --- |
| 旁观者确认性主表，2400 行，只读 | `results/experiments/v2_1/confirmatory/traces.jsonl` |
| 四张表的汇总 | `results/experiments/v2_1/reports/v21_summary.json` |
| 审计、敏感性、三张图 | `results/experiments/v4_0/` |
| 重算主表、重画图、审计 P95，不重跑确认性 | 下面三条命令 |

```powershell
python -m uv run python scripts/analyze_v21.py
python -m uv run python scripts/plot_v21_figures.py
python scripts/audit_v40_p95.py
```

不要为了核对主表执行 `scripts/run_v21_pilot.py --phase confirmatory`，它会重写已封存的 JSONL。

## 文档

现行稿只负责一件事。历代文档是演进历程，原文保留。

| 现行 | 读它得到什么 |
| --- | --- |
| [docs/论文4.0.md](docs/论文4.0.md) | 旁观者测量论文：贡献边界、P95 定义、服务量传导 |
| [docs/论文初稿.md](docs/论文初稿.md) | v1.2 真实 API：旧提交为 0、本地槽位和后续等待 |
| [docs/项目开发文档4.0.md](docs/项目开发文档4.0.md) | 收尾合同：数据地图、门槛定义、审计和复现 |
| [docs/工程实现说明.md](docs/工程实现说明.md) | 模块、依赖方向、命令 |
| [docs/问题记录/开发问题.md](docs/问题记录/开发问题.md) | 从 v0.2 到 4.0 实际撞上的问题 |
| [docs/面试讲解.md](docs/面试讲解.md) | 一分钟和五分钟口述 |

| 历代 | 时期 |
| --- | --- |
| [docs/项目开发文档.md](docs/项目开发文档.md) | 初始研究设计 |
| [docs/项目开发文档2.0.md](docs/项目开发文档2.0.md)、[2.1](docs/项目开发文档2.1.md)、[2.2](docs/项目开发文档2.2.md) | v2 规划与 v2.0 证据口径 |
| [docs/项目开发文档3.0.md](docs/项目开发文档3.0.md) | 旁观者实验协议，主格不再增加 |
| [docs/工程实现说明/](docs/工程实现说明/) | v0.1–v1.0 实现说明原文 |
| [docs/测试记录/](docs/测试记录/) | 阶段一至 v2.1 的测试与证据 |
| [docs/实验协议.md](docs/实验协议.md)、[docs/第二环境.md](docs/第二环境.md) | 假后端口径、第二套环境为什么没接上 |

## 共同开发者

- [Covertrick](https://github.com/Covertrick)
- [chenjj2326](https://github.com/chenjj2326)
