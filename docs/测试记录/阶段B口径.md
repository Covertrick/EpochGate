# 阶段 B：测量口径

日期：2026-11-02。包版本进入 1.1。阶段一 60 行不回写。

## 代码改动

- `linearize_revoke` 成功时发出独立的 `eligibility_revoked`。
- 流式后端 `resource_reclaim_observable=False`。客户端停读只发 `local_read_stopped`；`aclose` 时回执带 `connection_closed`，运行时另发 `connection_closed`。
- 流式增量 `usage` 使用 `event_count=1`，不再写 `tokens:1`。
- Tokenizer 冻结为 `tiktoken` / `cl100k_base`（`src/epochgate/text_tokens.py`）。只统计客户端收到的文本，不是 DeepSeek 账单。
- 矩阵行新增：`post_revocation_delta_events`、`post_revocation_tool_call_delta_events`、`post_revocation_observed_text_tokens`、`provider_completion_tokens`（不能归因则为 `"UNKNOWN"`）、`cancel_latency_s`、`connection_closed_at`、`attempt_terminal_at`、`pair_id`、`request_id`。
- 检测延迟 = `eligibility_revoked_at - stock_change_applied_at`。取消延迟 = `local_read_stopped_at - cancel_requested_at`。二者分开。

## 分子分母

| 指标 | 分子 / 定义 | 单位 |
| --- | --- | --- |
| 撤销后文本事件 | 资格撤销时刻及之后的 `token_delta` 条数 | 事件 |
| 撤销后工具事件 | 同期 `tool_call_delta` 条数 | 事件 |
| 可观察文本词元 | 对上述文本内容做 tiktoken 编码 | 词元（客户端） |
| 供应商 completion | 该 attempt 的 completed usage；否则 UNKNOWN | 供应商词元或未知 |

旧字段 `post_revoke_local_deltas` 仍是两类事件之和，只为兼容阶段一汇总脚本。

## 离线样例

`tests/test_phase_b_metrics.py` 用合成轨迹核对：文本事件与工具事件分开、usage 缺失为 UNKNOWN、检测延迟与取消延迟不混用。
