# 阶段 2 流式假后端

新增 `StreamingFakeBackend`，没有改现有 `FakeBackend`。增量种类是 `token_delta` 和 `tool_call_delta`。运行时只把完整的 `tool_call` 或 `completed` 交给网关。词元计数的 `usage_source` 是 `local`。

这个假后端的 `resource_reclaim_observable` 为 true。`reclaimed` 只在本地生成循环因取消退出后才为 true，证据原文是「本地循环已停」。

四条测试都在 `tests/test_streaming_cancel.py`，完整套件 91 passed、0 failed。

- 正常流：4 个本地词元之后提交预约，库存从 3 减到 2，有 `action_committed`。
- 第五个词元之后把库存减到 0。轨迹顺序是 `attempt_superseded`、`cancel_requested`、`cancel_acknowledged`、`resource_reclaimed`。取消时流已关闭。被撤销的那次尝试只有 5 个 `token_delta`，都在 `cancel_requested` 之前。该尝试再等一秒没有新的 `token_delta`。整次运行没有 `action_committed`。
- 工具参数还停在半截 `PARTIAL-7f3a` 时取消。这个标记只出现在 `tool_call_delta`。`action_committed` 和 `action_rejected` 里都没有它。
- 取消后旧循环仍交出完整预约。旧尝试被拒绝，原因是 `attempt not eligible`。`explain_task` 把它标成 `late_output_rejected`。这条路径没有「本地循环已停」。

被取消的尝试若不再产出动作，运行时不会把它记成后端失败。资格已经撤销时，空结果直接返回。随后的新尝试若再生成，词元记在重规划一侧，不记成被取消那次尝试的增量。
