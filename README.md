# EpochGate

## 三条线

- **4.0：队列现象测量。** 共享槽上，失效占用怎么变成旁观者等待。数字在 [docs/论文/论文4.0.md](docs/论文/论文4.0.md)。
- **5.0：安全 Gateway 和复现失败。** 已知 \(C/S\) 的切换没有形成可投稿结果；DeepSeek 那条复现也失败了，原文在 [docs/舍弃分支/](docs/舍弃分支/README.md)。审计在 [docs/论文/论文5.0.md](docs/论文/论文5.0.md)。
- **UCR-1：非 Oracle 不确定性策略未被有效评估。** 生成器要求模型在训练支持域外外推，谓词时刻和工具类型没有进入成本标签。Gate A/B/D 不通过，Gate C 通过。封存在 [docs/论文/ucr_v1_diagnosis.md](docs/论文/ucr_v1_diagnosis.md)。
- **UCR-2.1：插值 Gate A/B 不通过，算法线收口。** 生成器审计通过，但 F16 的剩余服务覆盖率是 0.79，区间加 probe 的后悔高于点估计。没有跑外推，也没有为此再跑 vLLM。结论在 [docs/论文/论文UCR-2.md](docs/论文/论文UCR-2.md)。

**现在不投 ArXiv。**

把 Agent 的**执行资格**和 **Runtime 槽位**分开：过期动作不能提交；槽位让不让出来，是另一件可以测量的事。

当前包版本 **5.0.0**。可引用的结果仍是 4.0 旁观者回放和 v1.2 安全证据。

## 30 秒

共享槽上有一个长任务正在生成，外部状态中途变化。

- **G0** 把失效尾巴读完，到提交边界才拒绝。
- **G1** 看见相关信号就停，并立刻重做。
- **G2** 先问前提还在不在，只有前提为假才停并放槽。

在 4 个槽的逻辑时钟回放里，前提已经失效时，G2 相对 G0 少做 12.8 秒失效占用，下一只槽先交给后继，旁观者 P95 等待仍在 12/12 个主格跨过预先冻结的门槛，大约少 1.6–2.6 秒。前提仍成立时，差的符号跟着 restart 长短走，不是 G2 处处更好。这是这条槽模型上的传导，不是新的队列定律。Retire 已经检查过无关租户，这里的主指标是旁观者等待。客户端停读不是 GPU 已回收。

4.0 回放数字在 [docs/论文/论文4.0.md](docs/论文/论文4.0.md)。5.0 审计在 [docs/论文/论文5.0.md](docs/论文/论文5.0.md)。现行合同在 [docs/开发文档/项目开发文档5.0.md](docs/开发文档/项目开发文档5.0.md)。v1.2 真实 API 槽位在 [docs/论文/论文初稿.md](docs/论文/论文初稿.md)。

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
| 5.0 回放区间和 240 个 attempt | `results/experiments/followup/comparisons.json`、`runtime_summary.json` |
| UCR 留出评估和安全闭环 | `results/experiments/ucr/summary.json`、`runtime_summary.json` |
| 舍弃的 DeepSeek / vLLM 数据 | `results/experiments/v5_0/`、`results/experiments/v5_1/` |
| 重算主表、重画图、审计 P95，不重跑确认性 | 下面三条命令 |

```powershell
python -m uv run python scripts/analyze_v21.py
python -m uv run python scripts/plot_v21_figures.py
python scripts/audit_v40_p95.py
```

不要为了核对主表执行 `scripts/run_v21_pilot.py --phase confirmatory`，它会重写已封存的 JSONL。

## 文档

`docs/` 按用途分文件夹。现行稿各管一件事。历代文档是演进历程，原文保留。

| 文件夹 | 现在读哪一份 |
| --- | --- |
| [docs/论文/](docs/论文/) | [论文UCR-2.md](docs/论文/论文UCR-2.md) 收口 UCR-2.1；[论文UCR.md](docs/论文/论文UCR.md) 封存 UCR-1；[论文5.0.md](docs/论文/论文5.0.md) 是已知 \(C/S\) 的审计；[论文4.0.md](docs/论文/论文4.0.md) 是回放参照 |
| [docs/开发文档/](docs/开发文档/) | [项目开发文档UCR.md](docs/开发文档/项目开发文档UCR.md) 是 UCR 记录；[项目开发文档5.0.md](docs/开发文档/项目开发文档5.0.md) 是上一条合同 |
| [docs/舍弃分支/](docs/舍弃分支/) | DeepSeek 黑盒和 vLLM 四组，不再作为论文主线 |
| [docs/工程实现说明/](docs/工程实现说明/) | [工程实现说明.md](docs/工程实现说明/工程实现说明.md) 是当前模块和命令。`v0.1.md`–`v1.0.md` 是各版原文 |
| [docs/面试/](docs/面试/) | [面试讲解.md](docs/面试/面试讲解.md) 是口述；[结果与复现.md](docs/面试/结果与复现.md) 是结果索引 |
| [docs/问题记录/](docs/问题记录/) | [开发问题.md](docs/问题记录/开发问题.md) 从 v0.2 到 4.0 实际撞上的问题 |
| [docs/测试记录/](docs/测试记录/) | 阶段一到 v2.1 |
| [docs/早期/](docs/早期/) | [实验协议.md](docs/早期/实验协议.md)、[第二环境.md](docs/早期/第二环境.md)、最初的 HTML 设计书 |
| [docs/figures/](docs/figures/) | 初始设计书里的八张示意图 |

## 共同开发者

- [Covertrick](https://github.com/Covertrick)
- [chenjj2326](https://github.com/chenjj2326)
