# 阶段 D：并发传输基线

日期：2026-11-02。原始行 `results/real/phase_d/baseline.jsonl`。汇总 `baseline_summary.json`。阶段一失败行（种子 402/403）保留在 `results/real/matrix.jsonl`，未删除。

## 代码侧已知事实

- 矩阵与本基线均 `max_inflight=1`：同一 Runtime 里流式请求串行，不是多连接打满本地池。
- `HttpStreamingBackend` 每次 `events()` 新建 `httpx.AsyncClient`，请求结束后关闭。
- 失败 detail 现含 `exception_type`、`status_code`、`timeout`、`closed_by_cancel`、`message`、`body` 前缀。空的 `http_error:` 不应再出现。

## 阶段一失败回顾

| 种子 | 臂 | 当时 detail | 当时能断定的 |
| --- | --- | --- | --- |
| 402 | B | `http_error:`（空） | 传输失败，类型与状态码未知 |
| 403 | B | `http_error:` + `backend returned no action` | 传输失败后还有无工具调用 |
| 403 | A | `stream_closed` | 取消标志未置位时的读错误/协议错误 |

当时不能区分限流、超时、服务端断开。本地连接池打满可排除（串行 + 每请求新客户端）。

## 本轮基线

条件：120 词、延迟 0.5 秒、臂 B、单任务 5 次、三任务 5 次。

| 探针 | 次数 | 分类计数 |
| --- | --- | --- |
| single | 5 | ok=5 |
| triple | 5 | ok=5 |

`counts`: `{"ok": 10}`。本轮没有复现 402/403 类传输失败。

## 判定

- 在当前 `max_inflight=1`、短探针负载下，传输基线稳定。
- 阶段一的 2/5 三任务失败更像偶发供应商/网络问题，而不是本地连接池。
- **允许**在阶段 E 把三任务格留下作为负载轴，但必须继续记录增强后的失败 detail；若正式主实验失败率再次升高，并发格降为负例，不宣称调度改善吞吐。
- 仍不能把「本轮 10/10 ok」外推成生产级并发能力。
