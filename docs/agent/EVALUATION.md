# Evidence-aware Agent 评测与复现

## 真实编码闭环

使用仓库内的运费计算 Issue 和测试仓库。DeepSeek 生成 DAG，并选择读文件、修改代码、跑测试和修复计划。模型写入限定在临时 Git 仓库的 shipping.py。测试由 PowerShell 桥接到 Docker Linux，使用只读挂载、禁网、非 root 用户和超时。PowerShell 可以访问引擎，现有 Runtime 的 Python 子进程依然报告 Docker 不可用。这条验收路径尚未替代通用 DSH 执行路径。

首次测试通过后，外部维护者修改 rules.py。执行器按模型声明的依赖确定失效节点，保留与配置无关的工作。模型提出失效节点的步骤更新，执行器保留既有 DAG 依赖。完成要求重新跑过测试，并核对测试时的源文件摘要与最终产物。写入有待确认日志与内容摘要，已确认的内容不会自动回写。

验收记录来自一次完整运行。DeepSeek Flash 共调用 18 次，服务端报告 21,257 Token，工具请求 14 次，端到端约 50 秒。三轮 Docker 测试退出码为 1、0、0，最终 4 项通过。配置变化后保留 inspect，局部修复 implement 和 test。两次写入的字节不同，两次重复内容请求均跳过。 [全部事件](coding/trace.json) · [代码补丁](coding/agent.patch) · [外部变更](coding/world-change.patch) · [独立重算](coding/report.json)

需要已配置的 DeepSeek 密钥和可启动 Linux 容器的 Docker。用新的输出目录运行：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File experiments/coding_loop/run.ps1 -OutputDirectory .epochgate/coding-evaluation
~~~

## 40 个离线任务

40 个任务覆盖四个夹具族与十种固定安排。履约作为留出夹具族。各策略使用同一任务、计划、预算和故障安排。工具失败、响应丢失、可查或不可查的 UNKNOWN、取消和后继对账均记录在案。 [实验协议](../../experiments/agent_decision/expanded/PROTOCOL.md)

| 策略 | 成功 | 已观测有效效果 | 重规划 | 工具请求 | 保留节点 | UNKNOWN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full Replan | 16/40 | 60/60 | 56 | 96 | 0/132 | 20 |
| Version-Based | 16/40 | 60/60 | 12 | 96 | 16/40 | 20 |
| Predicate-Based 强基线 | 16/40 | 60/60 | 8 | 96 | 0/28 | 20 |
| Partial Repair | 16/40 | 60/60 | 8 | 96 | 8/28 | 20 |
| Partial Repair + Evidence Recovery | 32/40 | 76/76 | 12 | 136 | 8/32 | 4 |

局部修复与强语义基线达到同样的成功数、重规划次数和工具请求数，并在重规划期间保留更多未失效节点。证据恢复通过更多查询与后续执行提高了完成数。离线回放没有模型调用，Token 报告为不可用。0 次已观测违规或重复只描述本次回放。

重算器从目标端记录检查版本、业务前置条件和回执摘要，并报告端到端及恢复耗时。保留比例以重规划时尚未提交的节点为分母。四个取消任务在各策略中均单独记录。

强语义基线和局部修复的平均端到端耗时分别约 261 ms 和 275 ms。完整证据策略约 328 ms，其中触发接管的任务平均恢复阶段约 195 ms。离线结果尚未建立 Token 节省或延迟优势。真实编码任务只验证了单个闭环，跨仓库、模型和任务族的在线消融仍待开展。

~~~powershell
uv run --locked --package epochgate-runtime python experiments/agent_decision/expanded/run.py .epochgate/expanded-check/raw.jsonl
uv run --locked --package epochgate-runtime python experiments/agent_decision/expanded/audit.py .epochgate/expanded-check/raw.jsonl .epochgate/expanded-check/report.json
~~~

[原始记录](expanded/raw.jsonl.gz) · [逐任务重算](expanded/report.json) · [上一轮先导](VERIFICATION.md)
