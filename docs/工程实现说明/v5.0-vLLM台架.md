# v5.0 vLLM 台架

GPU 机部署备忘。机制确认性前必须通过 `scripts/run_v50_vllm_compat.py`。

## 环境变量

| 变量 | 含义 |
| --- | --- |
| `VLLM_BASE_URL` | OpenAI 兼容 API，如 `http://127.0.0.1:8000/v1` |
| `VLLM_ADMIN_URL` | 可选管理面根 URL |
| `VLLM_METRICS_URL` | 可选 Prometheus 指标，如 `http://127.0.0.1:8000/metrics` |
| `VLLM_MODEL` | 模型名 |
| `VLLM_API_KEY` | 默认 `EMPTY` |

## 观测级

| 级别 | 含义 | 可主张 |
| --- | --- | --- |
| L2-a | 请求 aborted/finished | 生命周期终止 |
| L2-b | 队列快照 / 出队 | 不再占该调度队列位 |
| L2-c | GPU/KV 指标变化 | 才可讨论资源回收 |

禁止把 a/b 写成 c。

## 换机流程

```powershell
git pull
python -m uv sync --extra dev
python -m uv run python scripts/run_v50_vllm_compat.py
# 通过后再跑 pilot / confirm（编排与 DS 对称，backend_kind=vllm）
```

## 与 DeepSeek 的边界

数值不得并排当作因果对比。DS = 客户端可观察外部有效性；vLLM = 机制识别。
