# EpochGate

把 Agent 的**执行资格**和 **Runtime 槽位**分开：过期动作不能提交；槽位让不让出来，是另一件可以测量的事。

当前包版本 **5.0.0**。4.0 旁观者回放与 v1.2 安全证据仍有效。5.0 增加 DeepSeek V4.1-Flash 上的客户端可观察旁观者测量。5.1 是论文结果确认阶段，不另开研究方向。数字按层分读，不要混用。

## 30 秒

共享槽上有一个长任务正在生成，外部状态中途变化。

- **G0** 把失效尾巴读完，到提交边界才拒绝。
- **G1** 看见相关信号就停，并立刻重做。
- **G2** 先问前提还在不在，只有前提为假才停并放槽。

在 4 个槽的逻辑时钟回放里，前提已经失效时，G2 相对 G0 少做 12.8 秒失效占用，下一只槽先交给后继，旁观者 P95 等待仍在 12/12 个主格跨过预先冻结的门槛，大约少 1.6–2.6 秒。前提仍成立时，差的符号跟着 restart 长短走，不是 G2 处处更好。这是这条槽模型上的传导，不是新的队列定律。Retire 已经检查过无关租户，这里的主指标是旁观者等待。客户端停读不是 GPU 已回收。

在 DeepSeek-V4.1-Flash（API 名 `deepseek-flash`，thinking 关闭）上，撤权后立即停读并放本地槽，相对继续持槽到流结束：

- 中负载：旁观者槽等待少 0.891 秒（相对 21.4%），端到端少 0.873 秒（相对 12.3%）。两档都过预先登记的 10% 工程门槛。
- 高负载：槽等待少 0.863 秒（相对 11.2%，过门槛）；端到端少 0.922 秒，区间不含 0，但相对 8.6%，**没有**过完整工程门槛。

两档都是同向变短，没有稳定的 local/e2e 反转。服务端停止状态全部是 `unknown`，不能写成 GPU 或 KV 已回收。配对差定义为 \(G0-G2_{\mathrm{local}}\)，正数表示 G2-local 更短。

5.1 按合同做了独立复现和本机 vLLM 四组。高负载的正差没有在新窗口再现（槽等待相对 −0.9%，端到端相对 0.2%，区间都含 0）。RTX 4060 上的 vLLM 0.32.0 只说明：本地多握 1.5 秒槽，下一条请求就晚约 1.5 秒，而这 1.5 秒里 `num_requests_running` 已经是 0；`finished_reason=abort` 没有增加。这不能证明新机制或新算法。**不以当前主张发表。** 判决在 [docs/测试记录/v5.1-结果与定位.md](docs/测试记录/v5.1-结果与定位.md)。

4.0 回放数字在 [docs/论文/论文4.0.md](docs/论文/论文4.0.md)。5.0 黑盒结论在 [docs/论文/论文5.0.md](docs/论文/论文5.0.md)。下一阶段合同在 [docs/开发文档/项目开发文档5.1.md](docs/开发文档/项目开发文档5.1.md)。v1.2 真实 API 槽位在 [docs/论文/论文初稿.md](docs/论文/论文初稿.md)。

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
| 5.0 DeepSeek 确认性与汇总 | `results/experiments/v5_0/` |
| 5.1 审计、复现、本机 vLLM 四组 | `results/experiments/v5_1/` |
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
| [docs/论文/](docs/论文/) | [论文5.0.md](docs/论文/论文5.0.md) 是方向 A；[论文4.0.md](docs/论文/论文4.0.md) 是回放参照；[论文初稿.md](docs/论文/论文初稿.md) 是 v1.2 |
| [docs/开发文档/](docs/开发文档/) | [项目开发文档5.1.md](docs/开发文档/项目开发文档5.1.md) 是现行合同；5.0 是已跑完的方向 A；更早是历代规划 |
| [docs/工程实现说明/](docs/工程实现说明/) | [工程实现说明.md](docs/工程实现说明/工程实现说明.md) 是当前模块和命令。`v0.1.md`–`v1.0.md` 是各版原文 |
| [docs/面试/](docs/面试/) | [面试讲解.md](docs/面试/面试讲解.md) 是口述；[结果与复现.md](docs/面试/结果与复现.md) 是结果索引 |
| [docs/问题记录/](docs/问题记录/) | [开发问题.md](docs/问题记录/开发问题.md) 从 v0.2 到 4.0 实际撞上的问题 |
| [docs/测试记录/](docs/测试记录/) | 阶段一到 v2.1，以及 [v5.1-结果与定位.md](docs/测试记录/v5.1-结果与定位.md) |
| [docs/早期/](docs/早期/) | [实验协议.md](docs/早期/实验协议.md)、[第二环境.md](docs/早期/第二环境.md)、最初的 HTML 设计书 |
| [docs/figures/](docs/figures/) | 初始设计书里的八张示意图 |

## 共同开发者

- [Covertrick](https://github.com/Covertrick)
- [chenjj2326](https://github.com/chenjj2326)
