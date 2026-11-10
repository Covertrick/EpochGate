# RRM-1 与近邻工作的差异

写于任何性能数字之前。核对日：2026-11-06。

不能写的句子：首次把撤权和执行分开，首次发现 Agent 取消不可靠，首次用 Gateway 拒绝过期提交，首次研究共享资源上的尾延迟，首次为队列外部性定价。

## Retire

论文是 Serving a Revisable World，arXiv:2610.01160。它在 vLLM 里做版本化执行：请求拥有调度和内存，执行版本拥有是否可以发布输出。流程是 ADVANCE、FENCE、ALIGN、RESUME、RECLAIM。公开摘要报告的是修订之后继请求的 TTFT，以及回放中没有过期输出。

公开文本没有报告这项测量：在同一台未改过的 vLLM 上，把客户端断流和显式 `AsyncLLM.abort` 配成两条臂，再把差值接到独立旁观者的 `queue_time` P95。后继请求的 TTFT 不是独立旁观者的排队尾延迟。因此「测出了某个中止时刻」本身不是新发现；Retire 也没有替 RRM 回答 ClientStopOnly 和显式 abort 在库存引擎里是不是同一次 `EngineCore.abort`。

## Stop Means Stop

论文是 Stop Means Stop，arXiv:2607.14166。它测六个 Agent 框架的审批、取消和超时是不是副作用屏障，并给出 SOUNDGATE。它没有测 vLLM 0.32.0 的请求级调度退出，也没有测独立旁观者的 queue time。

## 本协议还被允许问的问题

在这台固定的 vLLM 0.32.0 上，客户端断流和显式 abort 是不是进入同一个引擎中止，以及这个事实能不能按请求和独立旁观者的排队连起来。

如果 1A 显示两者进入同一个 `EngineCore.abort`，那么「一条路径停在客户端、另一条回收了 GPU」这个对照不再成立。剩下的传播延迟如果要测，必须另开协议版本，不能沿用当前主比较。
