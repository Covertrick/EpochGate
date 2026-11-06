# RRM-1 实验方案

冻结日：2026-11-06。机器可读副本在 `results/experiments/rrm/protocol.json`，哈希在 `protocol.sha256`。UCR 已收口，本协议不使用 CQR、probe 或 F13–F16 的估计器。4.0 只作为理想队列反事实。5.0 只作为 Gateway 和客户端撤销的实现基础。

这一版先回答取消路径，不跑性能赢家，也不计算 P95。

## 问题

RQ1：同一次请求上，客户端断流、显式中止调用、调度停止和资源状态分别有没有请求级证据。

RQ2：只有调度停止达到 E2，并且独立旁观者能返回真实 `queue_time_ms` 之后，才测量旁观者排队。应用层并发上限不叫 GPU 槽。

RQ3：PredicateAbort 只读运行时状态和 plan epoch。harmful/harmless 不进入在线策略。

## 对照臂

- CommitOnly：不提前中止，Gateway 拒绝过期提交。不预设它最慢。
- ClientStopOnly：客户端断流，RRM 不额外调用显式中止。不预设服务端没有自动取消。
- PredicateAbort：谓词失效后走已核实的显式中止。返回成功不等于资源已回收。
- SignalAbort：信号到达后显式中止，不检查谓词。不预设误撤一定有性能损失。
- DelayedAbort：同一显式中止，只是延后。不预设旁观者一定变差。

显式 `abort()` 是 Python 引擎 API。OpenAI 兼容 HTTP 有没有同等的逐请求中止，由阶段 1A 核对。两条路径在核对之前不能当成同一种机制。

## 证据

- E0：客户端断流。只能写本地撤销。
- E1：请求级中止调用进入引擎路径。只能写引擎收到或处理了中止命令。
- E2：请求级调度事件证明该请求不再被继续调度。
- E3：请求级资源释放或复用有独立验证。`num_requests_running` 和 `nvidia-smi` 不能充当 E3。

\(t_4\) 拆成请求不再持有 KV block、block 可以再分配、以及后续请求测到的容量变化。前两个不是第三个。

## 负载和终点

L0 是不拥塞的负对照，不要求 P95 改善。L1 是主拥塞等级。L2 只检验 L1 能否复现。只有两档同号才写跨拥塞等级复现。

主终点是一次独立运行内旁观者 `queue_time_ms` 的 P95，再对同种子配对差做运行级 bootstrap。主比较是失效条件下 PredicateAbort 相对 CommitOnly。机制比较是 PredicateAbort 相对 ClientStopOnly。全局混合 P95 可以另报，不能替换主终点。目标请求没有 usage chunk 时，queue time 不得补成 0。超时不得从 P95 里删掉。

阶段 1 每次只有一个旁观者，不算 P95。确认性种子从 11000 起，与试点种子 9100–9199 不重叠。统计脚本在读取确认性结果之前冻结。

## 停止

阶段 1A 如果断流和显式 abort 进入同一个 `EngineCore.abort`，ClientStopOnly 就不是未中止对照。不进入把两者解释成两种资源回收的请求级性能实验。

只有 E1 时不做阶段 2。E2 成立而 E3 未知时，可以讨论调度停止，不得写 KV 已回收。主比较不显著时，不得改用机制臂当主结果。

零次违规不是形式化保证。60 次独立试验里零次违规，单侧 95% 违规率上界仍约为 4.9%。

差异核对在 [RRM-1_novelty.md](RRM-1_novelty.md)。
