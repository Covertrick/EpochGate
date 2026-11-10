# v5.0 确认性 — vLLM（机制识别）

状态：**not_run**。这是 5.0 确认性矩阵，没有在 vLLM 上跑。2026-11-04 的本机四组是 5.1 的另一次探测，不是这张确认性表，见 [v5.1-vLLM四组.md](../v5.1/弃用-vLLM四组.md)。

## 预登记

- 兼容性：`scripts/run_v50_vllm_compat.py` 必须 L2-a 或 L2-b 可用
- 臂：可含 G2-residual；观测级逐格标注 L2-a/b/c
- 与 DS 数值**禁止**并排因果对比

## 复跑（GPU 机）

见 [v5.0-vLLM台架.md](弃用-vLLM台架.md)。

## Go 草稿

`vllm_go=not_run` ⇒ **总 Go = no**（机制知识增量未成立）。完整交付路径保留：换机后补跑即可改写本节与 `v50_summary.json`。
