# RRM-1

日期：2026-11-06。协议在 [RRM-1实验方案.md](RRM-1实验方案.md)。路径核对在 `results/experiments/rrm/path_check.json`。

**等级 C：取消路径审计。** 没有请求级调度停止证据，不做旁观者性能实验，也不把这篇写成资源回收论文。

## 1A 看到的路径

本机安装的 vLLM 是 0.32.0。`AsyncLLM.generate()` 在 `CancelledError` 或 `GeneratorExit` 时调用 `abort()`。`abort()` 再调用 `engine_core.abort_requests_async`。OpenAI `/v1/completions` 没有单独的逐请求 abort 路由。流式响应被关掉时，`merge_async_iterators` 会关闭内层的 `generate()`。

因此客户端断流和显式 `abort()` 是两种触发方式，进入的是同一次引擎中止。ClientStopOnly 不是「客户端停了、引擎还在跑」的对照。先前 59/60 次 `num_requests_running=1` 也不能解释成服务端没有中止。

显式中止是进程内的 Python API。这次没有观察到请求级调度退出，所以证据停在源代码上的 E1 路径，不是 E2，更不是 E3。`num_requests_running` 没有被写成该请求的 KV 已回收。

## 没有做的部分

阶段 1B、1C、L0/L1/L2 配对、TraceLab 和 P95 都没有跑。协议要求：两条臂若只是触发方式不同，就不能把时序差解释成一种回收了 GPU、另一种没有。传播延迟如果还要测，必须另开协议版本。

UCR 仍然收口。4.0 仍然只是理想队列反事实。5.0 的 Gateway 旧提交为 0 仍然有效，但它不证明服务端容量已经释放。

能留下的是这条路径记录、事件校验和停止决定。不能留下的是「撤销改善了独立旁观者」或「请导师为一篇资源回收论文署名」。
