# 阶段 E 协议（P1.4 正式冻结版）

日期：2026-11-02。机器可读冻结清单见 [`results/real/phase_e/protocol_frozen.json`](../../../results/real/phase_e/protocol_frozen.json)，A–D 基线冻结见 [`results/real/phase_e/p1_0_freeze_manifest.json`](../../../results/real/phase_e/p1_0_freeze_manifest.json)。

## 1. 研究问题（三层固定）

对一个依赖外部状态的 LLM 工具调用任务，当外部状态变化使当前 attempt 失去执行资格时，立即停止本地流式读取（B 臂），相比继续读取并在提交时拒绝旧结果（A 臂），能否减少客户端可观察的失效输出，并改善后续任务执行？这种效果如何受到任务长度、撤销时机和串行队列负载影响？

| 层次 | 核心问题 | 主要证据指标 | 地位 |
| --- | --- | --- | --- |
| **RQ1：输出层** | B 是否比 A 少接收失效 attempt 的客户端输出？ | `post_revocation_observed_text_tokens` 配对差、`post_revocation_delta_events`、`post_revocation_tool_call_delta_events` | **主机制结果** |
| **RQ2：执行层** | B 是否更早释放本地执行槽位，使重规划与后续排队任务更早推进？ | `old_attempt_slot_hold_s`、`slot_release_latency_s`、三任务队列后续任务等待时间 `first_queue_wait_s`、`batch_wall_clock_s` | **系统效应结果** |
| **RQ3：正确性层** | B 是否在减少旧输出的同时保留正确任务结果？ | `old_attempt_committed == False`、`action_commits`、`side_effect_once`、`correctness_ok` | **不可妥协约束** |

## 2. 对照臂定义与唯一处理变量

- **共同前置**：A、B 两臂使用相同模型与端点、相同提示词（`phase1-words-v1`）、相同工具 Schema、相同初始库存、相同独立定时 `ScenarioDriver` 外生事件计划、相同 Runtime 配置与 `max_inflight=1` 本地槽位、且**两臂都在线性化点先撤销旧 attempt 的执行资格（`eligibility_revoked`）**。
- **唯一处理变量**：资格撤销后是否关闭客户端响应流（`stop_on_cancel`）：
  - **A 臂**：只撤销资格，不关闭旧流，继续读取直到旧流结束，旧结果交 Gateway 拒绝后进入重规划。
  - **B 臂**：撤销资格并关闭响应流（`response.aclose()`），本地读取循环停止，半截工具参数不拼成 `CandidateAction`，立即释放本地槽位并进入重观察/重规划。
- **配对公平性**：每个 `pair_id` 使用两份独立的 `InventoryEnvironment` 副本重放同一 `ScenarioSpec`；A/B 执行顺序按配对种子伪随机化（`AB` / `BA`）。

## 3. 定时外生事件驱动（`ScenarioDriver`）

正式时序实验通过 [`src/epochgate/scenario_driver.py`](../../../src/epochgate/scenario_driver.py) 按相对锚点（`first_delta`）独立定时触发外生变化：
- `t_change`：库存由初始值清零（如 `1 -> 0` 或 `3 -> 0`）；
- `t_restore`（仅可恢复任务）：按固定相对时间加回库存（如 `0 -> 1` 或 `0 -> 3`），**绝不依赖 A/B 何时结束旧流**。
- 阶段 C 同步剧本保留在 `tests/test_deterministic_validity.py` 作状态谓词验收；定时可重放剧本在 `tests/test_timed_scenario_driver.py` 作时序行为验收。

## 4. 统一时间戳与派生公式

所有流式行输出完全一致的 JSON 键集合：

- `stock_change_applied_at`：环境真正改变库存时刻
- `eligibility_revoked_at`：资格撤销线性化完成时刻
- `cancel_requested_at`：Runtime 发出取消请求时刻
- `connection_closed_at`：客户端响应流关闭时刻（A 为 `null`）
- `local_read_stopped_at`：本地读取循环停止时刻（A 为 `null`）
- `old_attempt_slot_release_at`：旧 attempt 释放本地推理槽位时刻（`slot_released` 事件）
- `replan_started_at`：新 attempt 启动时刻
- `replan_committed_at`：新 attempt 有效提交确认时刻
- `batch_completed_at`：同批任务全部进入终态时刻

派生量：
\[
T_{\text{detect}} = t_{\text{eligibility\_revoked}} - t_{\text{stock\_changed}},\quad
T_{\text{cancel-local}} = t_{\text{local\_read\_stopped}} - t_{\text{cancel\_requested}}
\]
\[
T_{\text{slot-release}} = t_{\text{slot\_release}} - t_{\text{eligibility\_revoked}},\quad
T_{\text{recovery}} = t_{\text{new\_commit}} - t_{\text{eligibility\_revoked}}
\]

## 5. 三块实验结构（避免 54 格全因子盲跑）

1. **E1（取消机制主实验）**：失效任务（`1 -> 0` 不恢复），单任务 `max_inflight=1`，长度 3 档（`short=50`、`mid=200`、`long=450` 词）× 撤销时机 3 档（`early=0.25s`、`mid=0.80s`、`late=1.80s`），共 9 格。
2. **E2（任务正确性与可恢复性实验）**：基于开发种子校准锁定的两个代表性条件——高收益空间（`long_early`：`450` 词，`t_change=0.25s`，`t_restore=1.25s`）与低收益边界（`short_late`：`50` 词，`t_change=1.80s`，`t_restore=2.60s`），覆盖 `static`、`invalidate`、`recoverable` 三类任务。
3. **E3（串行队列压力与系统影响实验）**：在 `max_inflight=1` 下对比 `low_single`（单任务）与 `serial_queue_x3`（一次登记 3 个任务串行排队），明确称为**串行槽位队列压力**，不称为多请求并行推理。

## 6. 种子隔离、预设门槛与失败处理规则

- **种子隔离**：开发种子固定在 `10xx` 段（`results/real/phase_e/dev/`）；正式测试种子固定在 `20xx`（E1）、`30xx`（E2）、`40xx`（E3）段（`results/real/phase_e/test/`），绝不混算。
- **预设最小有意义差异（\(\delta_{\min}\)）**：
  - \(\Delta_{\text{text}} = T_{A,\text{post}} - T_{B,\text{post}} \ge 25\) 客户端可观察词元；
  - \(\Delta_{\text{slot}} = T_{A,\text{slot}} - T_{B,\text{slot}} \ge 0.40\text{ s}\) 本地槽位占用缩减。
- **触发与失败分类（`trigger_outcome`）**：`triggered` / `static_control` / `trigger_not_fired` / `trigger_late` / `transport_failed` / `runtime_failed` / `model_no_action`。未触发与失败行全部保留在原始 JSONL 与 `failures_and_nontriggers.json` 中，不伪造 0 差值、不静默删除。
