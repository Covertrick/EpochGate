# v5.1 vLLM 四组

引擎是本机 Windows 轮子 vLLM 0.32.0，模型 `Qwen/Qwen2.5-1.5B-Instruct`，RTX 4060 8GB。四组各跑一次，不是确认性矩阵。脚本注入的残余不在这里。

| 组 | 断流 | 本地槽再持有 | 断流后 running>0 的观测秒数 | 焦点放槽时刻 | bystander-0 拿到槽 |
| --- | --- | --- | --- | --- | --- |
| G0 | 否 | 0.000 | — | 1.875 | 1.875 |
| G2-local | 是 | 0.000 | 0.047 | 0.141 | 0.141 |
| G2-residual | 是 | 0.000 | 0.047 | 0.141 | 0.141 |
| G2-hold-slot | 是 | 1.500 | 1.688 | 1.641 | 1.641 |

`observed_residual_s` 来自 `/metrics` 里 `vllm:num_requests_running` 在断流之后仍大于 0 的时间。采样间隔约 0.15 秒。没有 per-request abort 状态接口时，不把这个 gauge 写成 L2-a 的 aborted 回执。
KV 占用只在 `vllm:gpu_cache_usage_perc` 实际出现并变化时才能谈回收。
