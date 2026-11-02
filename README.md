# EpochGate

多会话智能体运行时的**版本化准入与取消层**：把资源执行状态和结果资格分开。

> 当前版本：**1.2.0**。已完成阶段 A–F（证据归档、测量口径修正、确定性与真实可恢复验收、并发传输基线、定时外生驱动 `ScenarioDriver`、阶段 E 主实验与阶段 F 收益边界/观察成本/FIFO 串行队列/六维取消能力合同）。最新里程碑与实测结论见 [docs/测试记录/v1.2.md](docs/测试记录/v1.2.md) 与 [docs/测试记录/阶段F实验协议与结果.md](docs/测试记录/阶段F实验协议与结果.md)。

## 快速开始

在仓库根目录，离线演示与完整单元测试不需要接口密钥：

```powershell
python -m uv sync --extra dev
python -m uv run pytest -q
python -m uv run epochgate demo
python -m uv run epochgate explain t-explain --trace results/explain/demo.jsonl
```

本机没有把 `uv` 加进路径时，用 `python -m uv`。需要 Python 3.12 及以上。

`explain` 读一条不含密钥的轨迹。命令的真实输出是：

```text
task=t-explain
stale_observation source=inventory reason=exogenous
eligibility_revoked reason=dirty and premise failed; revoke eligibility
late_output_rejected reason=attempt not eligible
late_output_rejected reason=insufficient stock
```

上面四行是程序打印的标签，不是性能数字。

## 复算与验证命令（只读重算，无需 API 密钥）

```powershell
# 阶段一 60 行归档只读重算
python -m uv run python scripts/recompute_phase1_summary.py

# 阶段 C 确定性剧本（静态 / 失效 / 可恢复）验收报告生成
python -m uv run python scripts/run_task_validity.py --deterministic

# 阶段 E 独立复核（输出安全不变量表、可用性表与配对差明细）
python -m uv run python scripts/audit_stage_e.py

# 阶段 F 确认性实验汇总重算（F1-M / F1-R / F2 / F4 / F3）
python -m uv run python scripts/analyze_stage_f.py
```

## 核心实证结论（v1.2.0，`deepseek-chat` 流式配对实验）

1. **外部副作用安全不变量（H4）**：在阶段 E（64 行）与阶段 F 确认性测试集（120 行）共 **184 条真实 API 测试任务行**及全部离线确定性剧本中，被撤销旧 attempt 越权提交次数 **`old_attempt_committed = 0`**，重复副作用次数 **`0`**。
2. **客户端失效输出与本地槽位占用缩减（H1 & H2）**：在 F1-M 主要确认性单元（`long_early`、`long_mid`、`mid_mid`）上，资格撤销后立即中断本地流式读取（Arm B）相比继续读取并在网关拒绝旧结果（Arm A），减少客户端可观察失效文本词元均值 **465.2**（95% CI **[334.4, 567.8]**，`tiktoken/cl100k_base`），缩短旧 attempt 对本地执行槽位的占用均值 **3.70 s**（95% CI **[2.63, 4.55] s**）。
3. **串行队列队头阻塞消除（H3 / F4）**：在 `max_inflight=1` 与严格 `FifoAdmissionScheduler(replan_to_tail=True)` 下，跨平稳混合、突发到达与 4 任务长短混排共 6 个确认性批次，Arm B 使后续排队有效任务的首次等待时间 **6/6 批次全部下降**，批次聚类均值缩减 **2.49 s**（95% CI **[1.26, 3.72] s**），后续任务等待中位数从 **6.92 s** 降至 **4.49 s**。
4. **严格边界声明**：客户端 `response.aclose()` 仅代表本地读取停止与本地槽位释放，不代表商业 API 服务端停止 GPU 计算或减少计费（断流后供应商用量记为 `UNKNOWN`）。

## 文档

| 文档 | 用途 |
| --- | --- |
| [docs/工程实现说明.md](docs/工程实现说明.md) | v1.2.0 模块职责、依赖方向与运行入口 |
| [docs/项目开发文档2.1.md](docs/项目开发文档2.1.md) | 阶段 A–F 完成状态与研究演进规划 |
| [docs/测试记录/v1.2.md](docs/测试记录/v1.2.md) | v1.2 里程碑交付与关键实测数字摘要 |
| [docs/测试记录/阶段F实验协议与结果.md](docs/测试记录/阶段F实验协议与结果.md) | 阶段 F（F1–F5）完整实验结果与 5 类相关工作七维对比 |
| [docs/测试记录/阶段E独立复核.md](docs/测试记录/阶段E独立复核.md) | F0 对阶段 E 原始数据的独立复算、安全不变量表与可用性表 |
| [docs/测试记录/阶段E结果与F决策.md](docs/测试记录/阶段E结果与F决策.md) | 阶段 E（P1.0–P1.6）主实验报告与进入阶段 F 的判定 |
| [docs/测试记录/实验就绪审计.md](docs/测试记录/实验就绪审计.md) | 阶段 A–D 证据就绪审计与 Gateway 竞态验收 |
| [docs/论文初稿.md](docs/论文初稿.md) | v1.2 实证收敛版论文初稿 |
| [docs/实验协议.md](docs/实验协议.md) | 假后端主矩阵指标口径 |
| [docs/项目开发文档2.0.md](docs/项目开发文档2.0.md) | 2.0 规划存档 |
| [docs/项目开发文档.md](docs/项目开发文档.md) | 初始研究设计原文 |
| [docs/第二环境.md](docs/第二环境.md) | 第二套公开环境说明 |
| [docs/问题记录/开发问题.md](docs/问题记录/开发问题.md) | 开发与实验问题记录 |
| [docs/工程实现说明/](docs/工程实现说明/) | v0.1–v1.0 各版实现说明原文 |

## 共同开发者

- [Covertrick](https://github.com/Covertrick)
- [chenjj2326](https://github.com/chenjj2326)
