# 阶段 F（v1.2）实验协议、实测结果与相关工作创新性审查

日期：2026-11-02。包版本：**`1.2.0`**。代码基线快照：`8579d2b`。

v1.3 证据订正见 [v1.3-证据闭环.md](./v1.3-证据闭环.md)。下面的 F1 表和 F3 合同已按该订正改过。历史确认性 JSONL 仍是 `8579d2b` 的原始行。

---

## 一、主研究假设与四个子假设验证总览

**主假设**：当一个 Agent attempt 因外部状态变化失去执行资格时，及时终止其本地流式读取（B 臂），可以缩短失效 attempt 对 Runtime 本地执行槽位的占用，并在特定任务长度、状态变化时机及串行队列负载下，改善后续有效任务的等待和恢复时间，同时不破坏外部副作用正确性。

| 子假设 | 核心度量指标 | 阶段 F 确认性实验实测结论（仅计 `confirmatory` 测试子段，与 `dev` 严格隔离） | 结论状态 |
| --- | --- | --- | --- |
| **H1：输出假设** | 撤销后客户端可观察文本词元差 \(\Delta_{\text{text}} = T_{A,\text{post}} - T_{B,\text{post}}\) | 三个主单元里双臂都触发的 **5** 个配对，按配对等权，均值 **465.2** 词元、中位数 **473.0**。先前写出的区间 **[334.40, 567.80]** 是这 5 个配对差的重采样，不是 Holm 检验。`mid_mid` 的 seed 5451 因 A 臂 `trigger_late` 没有进入均值。这 5 对的 B 臂撤销后词元为 **0**。脚本没有计算 Holm p 值 | **均值可复核；未做显著性检验** |
| **H2：槽位假设** | Runtime 本地活跃 attempt 槽位占用缩减 \(\Delta_{\text{slot}} = T_{A,\text{slot}} - T_{B,\text{slot}}\) | 同一 5 个配对的均值 **3.6981 s**（写作 **3.70 s**），中位数 **3.9282 s**。单元均值是 `long_early` **4.7012 s**、`long_mid` **3.6609 s**、`mid_mid` **1.7663 s**（后者 n=1）。三个单元等权是 **3.3761 s**。测的是 Runtime 本地槽位，不是 GPU 槽位 | **均值可复核；未做显著性检验** |
| **H3：系统假设** | 后续排队有效任务等待时间缩减、恢复时间 \(\Delta T_{\text{recovery}}\) 与批次耗时 \(\Delta T_{\text{batch}}\) | **串行队列效应明确**：F4（6 个确认性多任务批次，共 40 个任务行）中，B 臂使后续排队任务首次等待时间平均缩短 **2.49 s**（中位数缩短 **2.37 s**，batch 聚类 95% CI **[1.26, 3.72] s**；后续任务等待中位数从 A 的 **6.92 s** 降至 B 的 **4.49 s**，P95 从 **12.11 s** 降至 **10.23 s**）。**单任务净恢复效应受重规划生成方差主导**：F2（`refresh_only`）中 `long_early` 净恢复快 **1.28–2.73 s**，但跨全条件总体 \(\Delta T_{\text{batch}}\) 均值 **+0.39 s**（95% CI **[-1.05, +1.94] s**） | **队列等待改善显著成立；单任务净恢复仅在长任务早撤销下为正** |
| **H4：正确性假设** | 安全不变量（被撤销旧 attempt 提交数、重复副作用数）vs 任务可用性 | **安全不变量 100% 保持**：阶段 F 全部 118 条确认性流式任务行 + 2 条非流式边界行中，`old_attempt_committed = 0`、`duplicate_side_effects = 0`、网关拦截记录 **85 次**。**可用性边界**：F4 串行队列完成率 B 为 `20/20 (100%)` vs A 为 `19/20 (95%)`；单任务可恢复（F1-R + F2）触发样本中 A 完成 `18/19 (94.7%)`、B 完成 `18/22 (81.8%)`（差异来自 DeepSeek 重规划轮偶发只输出解释未调工具 `model_no_action`） | **安全不变量零违规；重规划完成率受模型工具调用遵从度限制** |

---

## 二、安全不变量表 vs 可用性与任务完成表（正式分立）

### 2.1 安全不变量表（Safety Invariants Table — 零容忍硬约束）

数据源：[`results/real/phase_f/summary/stage_f_master_summary.json`](../../results/real/phase_f/summary/stage_f_master_summary.json)。

| 安全不变量指标 | F1-M (28 行) | F1-R (20 行) | F2 (30 行) | F4 (40 行) | F3 非流式 (2 行) | 合计 (120 行) | 允许上限 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 被撤销旧 attempt 提交次数 (`old_attempt_committed`) | 0 | 0 | 0 | 0 | 0 | **0** | 0 | **PASS** |
| 单任务重复副作用次数 (`action_commits > 1`) | 0 | 0 | 0 | 0 | 0 | **0** | 0 | **PASS** |
| 半截工具参数形成非法 `CandidateAction` | 0 | 0 | 0 | 0 | 0 | **0** | 0 | **PASS** |
| Gateway 拦截过时/库存不足拒绝次数 (`action_rejects`) | 16 | 11 | 21 | 35 | 2 | **85** | — | **正常拦截** |

### 2.2 可用性与任务完成表（Availability & Task Completion Table — 统计比较）

| 实验子集 | A 臂表现 | B 臂表现 | 关键发现与统计边界说明 |
| --- | --- | --- | --- |
| **F4 串行队列任务完成率**（6 对批次，每臂 20 个任务） | `19/20 (95.0%)` | **`20/20 (100.0%)`** | B 臂在全部 6 个确认性队列批次中 100% 完成所有任务提交 |
| **F4 预设客观饥饿事件数**（`starvation_count`） | **2 次**（`F4c` 4 任务混排中末位长任务 `t3`） | **0 次** | A 臂因 `t0` 旧流占槽拖慢队列，导致 `F4c` 中后续任务触发预设客观饥饿阈值；B 臂为 0 |
| **F1-R + F2 单任务可恢复**（已触发子集） | `18/19 (94.7%)` | `18/22 (81.8%)` | B 臂有 4 次在快速重规划后模型仅输出文本未发 `reserve` 工具调用（`replan_model_no_action`），说明真实 LLM 重规划遵从度存在波动 |
| **单臂未触发与失败五分类（不合并，共 118 条流式行）** | — | — | `trigger_not_fired = 29`、`trigger_late = 5`、`model_no_action(首轮) = 3`、`transport_failed(首轮) = 10`、`runtime_failed = 0` |

---

## 三、F1：收益边界与触发概率实验（F1-M 7 格 + F1-R 3 格）

配置文件：[`results/real/phase_f/protocols/f1_protocol_frozen.json`](../../results/real/phase_f/protocols/f1_protocol_frozen.json)，汇总：[`results/real/phase_f/summary/f1_m_mechanism.json`](../../results/real/phase_f/summary/f1_m_mechanism.json)、[`f1_r_recoverable.json`](../../results/real/phase_f/summary/f1_r_recoverable.json)。

### 3.1 F1-M：7 个代表性实验单元的触发概率与条件机制效应

`t_change` 统一以 `first_delta` 为相对锚点，同时记录 `request_started_to_stock_change_s`。未触发的请求绝不标为成功取消。

| 单元 (`cell_id`) | 角色 | 计划配对 | 配对触发四状态 (`both / A_only / B_only / neither`) | 单请求触发概率 \(P(\text{trigger})\) | 未触发率 (`not_fired`) | 晚触发率 (`late`) | 条件 \(\Delta_{\text{text}}\) 均值 (SD) | 条件 \(\Delta_{\text{slot}}\) 均值 (SD) | 策略整体 \(\Delta T_{\text{wall}}\) (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `F1M_long_early` (450w, 0.25s) | **Primary Confirmatory** | 2 | `2 / 0 / 0 / 0` | **1.00** | 0.00 | 0.00 | **+552.0** (111.72) | **+4.7012 s** (0.3422s) | **+3.3365 s** |
| `F1M_long_mid` (450w, 0.80s) | **Primary Confirmatory** | 2 | `2 / 0 / 0 / 0` | **1.00** | 0.00 | 0.00 | **+498.5** (68.59) | **+3.6609 s** (0.3779s) | **+2.7534 s** |
| `F1M_mid_mid` (200w, 0.80s) | **Primary Confirmatory** | 2 | `1 / 0 / 1 / 0` | **0.75** | 0.00 | 0.25 | **+225.0**（n=1，无 SD） | **+1.7663 s**（n=1） | **+2.8277 s** |
| `F1M_long_late` (450w, 1.80s) | Exploratory Boundary | 2 | `2 / 0 / 0 / 0` | **1.00** | 0.00 | 0.00 | **+322.0** (9.90) | **+2.2386 s** (0.1739s) | -47.0629 s |
| `F1M_short_early` (50w, 0.25s) | Exploratory Boundary | 2 | `1 / 0 / 0 / 1` | **0.50** | 0.00 | 0.00 | **+61.0**（n=1） | **+0.8714 s**（n=1） | +0.1475 s |
| `F1M_short_mid` (50w, 0.80s) | Exploratory Boundary | 2 | `0 / 0 / 1 / 1` | **0.25** | 0.25 | 0.50 | —（无 `both_triggered`） | — | -2.9149 s |
| `F1M_short_late` (50w, 1.80s) | Exploratory Boundary | 2 | `0 / 0 / 0 / 2` | **0.00** | **0.75** | 0.25 | —（无 `both_triggered`） | — | -1.2919 s |

条件均值只含 `both_triggered` 配对。主结果 465.2 / 3.6981 s 是三个主单元里 5 个这种配对的等权均值。三个单元均值再等权是 425.1667 词元 / 3.3761 s。`long_late` 的墙钟均值被 seed 5752 的 **-97.6831 s** 拉开，不能单独当成稳定的单元效应。

**F1-M 收益边界**：
1. 450 词任务的早、中、晚三格，单请求触发率都是 **1.00**。50 词任务从 `early` 的 **0.50** 降到 `mid` 的 **0.25** 和 `late` 的 **0.00**。
2. `short_mid` 与 `short_late` 没有双臂都触发的配对，条件词元差和槽位差为空。它们的策略整体墙钟差是 **-2.9149 s** 和 **-1.2919 s**。

---

## 四、F2：观察成本隔离（`refresh_only`）与净收益实验

配置文件：[`results/real/phase_f/protocols/f2_protocol_frozen.json`](../../results/real/phase_f/protocols/f2_protocol_frozen.json)，汇总：[`results/real/phase_f/summary/f2_net_benefit.json`](../../results/real/phase_f/summary/f2_net_benefit.json)。

### 4.1 观察延迟作用域隔离验证

F2 主实验固定 `observe_delay_scope = "refresh_only"`：
- 实测全部 30 条确认性行中，首次初始观察耗时 `actual_initial_observe_latency_s` 中位数仅为 **0.00018 秒**（完全不受 `0.5s / 2.0s` 配置延迟影响）；
- 状态变脏后的重观察耗时 `actual_refresh_observe_latency_s` 精确匹配配置档位：基线档 `0.0002s`、低延迟档 `0.505s`、高延迟档 `2.005s`。

### 4.2 各条件下的槽位节省、恢复净收益与批次净收益

| 实验单元 (`cell_id`) | 配置重观察延迟 | `both_triggered` 配对数 | 槽位占用缩减 \(\Delta_{\text{slot}}\) (s) | 机制解释项 \(G\) (s) | 实测恢复完成差 \(\Delta T_{\text{recovery}}\) (s) | 实测批次总耗时差 \(\Delta T_{\text{batch}}\) (s) |
| --- | --- | --- | --- | --- | --- | --- |
| `F2_long_early_F2_0_base` | 0.0 s | 2/2 | **+4.168** | +4.158 | **+2.731** | **+2.603** |
| `F2_long_early_F2_low_0.5s` | 0.5 s | 2/2 | **+4.322** | +4.312 | **+2.260** | **+1.000** |
| `F2_long_early_F2_high_2.0s` | 2.0 s | 2/2 | **+3.833** | +3.823 | **+1.275** | **+0.873** |
| `F2_mid_mid_F2_0_base` | 0.0 s | 1/2 | +1.856 | +0.918 | -0.137 | -0.780 |
| `F2_mid_mid_F2_low_0.5s` | 0.5 s | 2/2 | **+2.492** | +2.482 | **+0.313** | **+0.237** |
| `F2_mid_mid_F2_high_2.0s` | 2.0 s | 2/2 | **+2.060** | +2.050 | **+1.555** | **+1.568** |
| `F2_short_early_F2_0_base` | 0.0 s | 1/1 | +0.071 | +0.061 | +0.419 | -0.282 |
| `F2_short_early_F2_low_0.5s` | 0.5 s | 1/1 | +0.340 | +0.330 | -4.581 | -4.619 |
| `F2_short_early_F2_high_2.0s` | 2.0 s | 1/1 | +0.879 | +0.869 | -0.038 | +0.023 |

**F2 净收益核心发现**：
1. 在 `long_early` 下，即便重观察延迟高达 `2.0s`，B 臂节省的约 `3.83–4.32s` 本地槽位时间依然使实测恢复时间 \(\Delta T_{\text{recovery}}\) 保持正净收益（**+1.28 s 至 +2.73 s**）。
2. 由于 A 臂在旧流结束后同样需要执行一次重观察才能发起重规划，在单次外生变化下，重观察延迟对 A、B 两臂是对称加性成本；真正侵蚀单任务净收益的是**重规划新 attempt 本身的 LLM 生成长度波动**（例如 `short_early` 下 B 节省的槽位仅 `0.07–0.88s`，远小于新一轮 LLM 生成 3–5 秒的随机波动）。

---

## 五、F4：FIFO 串行队列压力与公平性边界实验

配置文件：[`results/real/phase_f/protocols/f4_protocol_frozen.json`](../../results/real/phase_f/protocols/f4_protocol_frozen.json)，汇总：[`results/real/phase_f/summary/f4_queue_and_fairness.json`](../../results/real/phase_f/summary/f4_queue_and_fairness.json)。

### 5.1 冻结的 FIFO 队列公平性合同

- **调度器**：`FifoAdmissionScheduler(replan_to_tail=True)`，`max_inflight = 1`（串行单槽位）。
- **入队与重规划规则**：初始任务严格按 `arrival_mono` FIFO 入队；被取消后的重规划 attempt 按其创建时刻 `attempt.created_mono` 进入就绪队列**队尾**（A/B 两臂使用完全相同的重规划优先级）。
- **预设客观饥饿阈值**：`first_queue_wait_s > 25.0s` 或 `overtaken_by_later_tasks_count > 2` 或 `timed_out`。

### 5.2 全部 6 个确认性批次的队列等待分布与饥饿统计（按 `batch_id` 聚类）

| `batch_id` | 场景 | 顺序 | A 各位置首次等待 (s) | B 各位置首次等待 (s) | 后续任务平均等待差 (s) | A/B 最大等待 (s) | A/B 批次总耗时 (s) | \(\Delta T_{\text{batch}}\) (s) | A/B 提交数 | A/B 饥饿数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `F4a-seed7151` | 平稳混合 (3) | `AB` | `[0.00, 6.13, 9.16]` | `[0.00, 1.79, 7.76]` | **+2.865** | `9.16 / 7.76` | `29.36 / 26.16` | **+3.205** | `3 / 3` | `0 / 0` |
| `F4a-seed7152` | 平稳混合 (3) | `BA` | `[0.00, 2.17, 6.31]` | `[0.00, 2.24, 5.30]` | **+0.475** | `6.31 / 5.30` | `30.85 / 26.54` | **+4.308** | `3 / 3` | `0 / 0` |
| `F4b-seed7251` | 突发 1长+2短 (3) | `BA` | `[0.00, 5.68, 8.32]` | `[0.00, 1.96, 8.28]` | **+1.884** | `8.32 / 8.28` | `23.30 / 29.34` | -6.040 | `3 / 3` | `0 / 0` |
| `F4b-seed7252` | 突发 1长+2短 (3) | `BA` | `[0.00, 6.27, 8.93]` | `[0.00, 1.68, 4.49]` | **+3.888** | `8.93 / 4.49` | `24.01 / 20.85` | **+3.164** | `3 / 3` | `0 / 0` |
| `F4c-seed7351` | 长短混排 (4) | `AB` | `[0.00, 6.92, 9.40, 12.11]` | `[0.00, 2.28, 5.28, 8.08]` | **+4.265** | `12.11 / 8.08` | `30.58 / 39.49` | -8.909 | `3 / 4` | **`1 / 0`** |
| `F4c-seed7352` | 长短混排 (4) | `BA` | `[0.00, 4.80, 7.23, 9.90]` | `[0.00, 1.88, 6.99, 10.23]` | **+1.571** | `9.90 / 10.23` | `42.76 / 32.21` | **+10.551** | `4 / 4` | **`1 / 0`** |

**F4 跨批次汇总（n=6 独立批次聚类 bootstrap）**：
- **后续任务首次等待时间缩减**：在全部 6 个批次中 **6/6 均为正值**，批次均值缩减 **2.491 s**，中位数缩减 **2.375 s**，95% CI **[1.264, 3.719] s**。
- **队列第 2 位置任务（`t1`，紧随被撤销长任务 `t0` 之后的短任务）队头阻塞消除最显著**：A 臂 `t1` 等待中位数为 **5.91 s**，而 B 臂因 `t0` 在 `0.25s` 撤销后立即释放槽位并按 `replan_to_tail` 退入队尾，使 `t1` 等待中位数降至 **1.92 s**（平均缩短 **3.36 s**）。
- **分布分位数（全部 14 个后续任务位置）**：
  - A 臂：中位数 **6.92 s**，P90 **9.90 s**，P95 **12.11 s**，最大值 **12.11 s**；
  - B 臂：中位数 **4.49 s**，P90 **8.28 s**，P95 **10.23 s**，最大值 **10.23 s**。

---

## 六、F3 后端取消能力合同与 F5/F6 审查

汇总文件：[`results/real/phase_f/summary/f3_capability_contract.json`](../../results/real/phase_f/summary/f3_capability_contract.json)。

### 6.1 取消能力合同（客户端断流与供应商取消分开）

`stop_on_cancel=False` 是流式可取消后端上的**策略对照开关（Arm A）**，绝不等同于后端不支持取消：

`response.aclose()` 只记入 `client_stream_close_issued` 和 `local_read_abort`。它不调用供应商取消 API，也收不到服务端 ACK。`provider_cancel_request` 与 `provider_cancel_ack_semantics` 因此不再把 B 臂写成已确认的供应商取消。历史 JSONL 里的旧六字段没有改写。

| 后端配置 / 剖面 | `local_read_abort` | `client_stream_close_issued` | `provider_cancel_api_invoked` | `provider_cancel_ack_received` | `provider_cancel_ack_semantics` | 服务端生成 / 资源回收可观察 | Gateway 资格门闩 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `HttpStreamingBackend`（B 臂，`stop_on_cancel=True`） | `True` | `True` | `False` | `False` | `UNKNOWN` | `False` / `False` | 代码路径强制走门闩 |
| `HttpStreamingBackend`（A 臂，`stop_on_cancel=False`） | `False` | `False` | `False` | `False` | `UNKNOWN` | `False` / `False` | 代码路径强制走门闩 |
| `HttpToolBackend`（非流式 HTTP POST） | `False` | `False` | `False` | `False` | `UNKNOWN` | `False` / `False` | 实测 seed 8101、8102 旧提交 = 0 |
| `FakeBackend(supports_cancel=False)` | `False` | `False` | `False` | `False` | `NOT_SUPPORTED` | `False` / `False` | 不支持取消时仍走门闩 |

### 6.2 F5 归因审查与 F6 条件判定

- **F5（A/B 处理变量与资格门闩归因审查）**：在 E 与 F 的全部流式配对中，A 臂与 B 臂共享完全相同的 `gateway.linearize_revoke` 资格撤销门闩、相同的初始观测、相同的 `ScenarioDriver` 时间表与相同的 `FifoAdmissionScheduler(replan_to_tail=True)` 队尾重规划规则。因此：
  - **安全性（`old_attempt_committed == 0`）归因于 Gateway 版本化准入与资格门闩**；
  - **本地槽位提前释放（\(\Delta_{\text{slot}}\)）与后续排队任务等待缩短（F4 \(\Delta_{\text{wait}}\)）归因于 B 臂的在途本地流中断（`local_read_abort`）**。无需重复跑冗余消融实验。
- **F6（依赖过滤刷新消融条件判定）**：F2 实测表明，在单源外部状态变化下，A 臂与 B 臂在重规划前均执行相同次数的重观察（`refresh_observe_count = 1`），观察成本在两臂间对称；刷新策略并非取消机制的混淆变量。按计划**在本轮暂缓 F6**，保持论文主线聚焦在“外生失效触发的在途 attempt 取消与本地槽位排队效应”。

---

## 七、相关工作逐项对比与论文创新性边界审查（对应修订要求 9）

为确保论文主张严谨，以下将 EpochGate 与 5 类最新相关工作按 **7 个正交维度** 逐项对比：

| 工作 / 方向 | 1. 研究对象 | 2. 停止/触发条件 | 3. 取消粒度 | 4. 取消后任务是否继续 | 5. 核心资源指标 | 6. 外部副作用正确性约束 | 7. 队列与多任务槽位指标 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **AgentStop** (*Terminating Local AI Agents Early to Save Energy in Consumer Devices*) | 端侧/消费级设备上的本地 AI Agent | 预测任务无法成功或陷入无效循环（内生轨迹特征） | **整个任务级（Task-level）** 提前终止 | **否**（直接放弃任务以节省端侧能耗） | 设备能耗（Joules）、端侧推理时长 | 无版本化外部状态条件提交门闩 | 不研究多任务共享槽位排队与队头阻塞 |
| **Runaway is Ashamed, But Helpful** (*Early-Exit Behavior of LLM Agents in Embodied Environments*) | 具身环境中的 LLM Agent 决策行为 | 模型自身判断环境受阻或任务不可行时主动触发 early-exit | **Agent 步级 / 轨迹级** 自我退出行为 | 视策略退出或求助，非版本驱动的自动重规划 | 交互步数、轨迹 token 数 | 关注具身动作安全，但无多会话 CAS 版本门闩 | 不涉及 Runtime 推理槽位竞争与队列等待 |
| **When Agent Context Goes Stale** (*Incoherence in Volatile Agent Context*) | 易变外部环境下 Agent 上下文陈旧性 | 外部环境数据随时间变化导致上下文与真值不一致 | **诊断与上下文刷新分析**（无在途流式中断） | 重试或刷新上下文 | 上下文不一致率、回答准确率 | 揭示陈旧上下文危害，未结合流式取消释放执行槽 | 无本地执行槽位占用与队列传递效应度量 |
| **Stale Does Not Mean Unsafe** (*Guard Precision for Tool-Using LLM Agents under Infrastructure State Races*) | 基础设施状态竞态下的工具调用 Agent 防护（Guard Precision） | 提交前检查外部状态改变是否真正破坏动作前提（`premise_holds`） | **提交门闩 / 动作级验证**（区分良性陈旧与危险陈旧） | 前提未破则放行提交；前提破坏则拒绝并重规划 | 误拒率（False Rejection）、有效通过率 | **高**（精确前提守卫防止危险副作用） | 聚焦提交点守卫精度，未量化**在途流式生成期提前断流对本地槽位与后续排队短任务等待时间的改善** |
| **LLM Serving Cancellation / Preemption** (*vLLM, SGLang, Orca 等推理引擎*) | 自托管 GPU 推理服务端引擎（KV Cache 与 Continuous Batching） | 客户端断开连接、超时或高优请求抢占（Preemption） | **服务端请求 / Token 迭代级**（分离式推理中取消传播可能不完整） | 服务端只管中止解码，不管 Agent 级重观察与重规划 | 服务端 GPU 算力、KV 显存块、TTFT/TPOT | 无业务层工具副作用或观测版本概念 | 服务端 batch 吞吐，不区分 Agent 任务的旧流槽位阻塞与重规划队尾公平性 |
| **EpochGate（本文 v1.2）** | **商业黑盒流式 API 之上的多任务 Agent Runtime 准入与取消层** | **外部状态变化 + 前提探测失败（`dirty & premise_holds=False`）触发线性化资格撤销** | **在途 Attempt 级流式读取中断（`local_read_abort`）** | **是**（旧 attempt 释放槽位并按 FIFO 入队尾重观察、重规划，任务继续直至恢复提交） | **客户端可观察失效词元（`tiktoken`）+ 本地执行槽位持有时间（`slot_hold_s`）**（诚实标服务端用量 `UNKNOWN`） | **严格不变量**：Gateway 锁内线性化撤销 + 版本条件提交（实测旧提交 = 0） | **核心系统指标**：串行槽位下队头阻塞消除、后续任务等待分布（Median/P90/P95）与客观饥饿计数 |

### 创新性边界声明（什么不能写，什么可以写）

1. **绝不作为独立创新主张的内容**：
   - 普通 HTTP 客户端 `response.aclose()` 断流操作本身。它只证明本地停止读取，不证明供应商收到取消请求或确认停止生成；
   - 通用 Agent Early Exit（如 AgentStop / Runaway 的内生停止）；
   - 单纯的提交前版本乐观锁（CAS）或上下文刷新。
2. **本文经实证支持的候选研究贡献**：
   - **机制耦合与分层取证**：在商业黑盒流式 LLM API（服务端停止与计费不可观测）约束下，将「外部状态变化前提探测 → 网关锁内线性化资格撤销 → 在途 attempt 本地流式中止与槽位释放 → 队尾重观察重规划」闭环结合，并严格分离客户端可观察词元、本地槽位占用与服务端 `UNKNOWN` 用量。
   - **系统队列效应实证与边界刻画**：通过受控配对实验证明，在途 attempt 级取消的系统价值不仅在于减少客户端接收的無效输出（H1），更在于**缩短失效 attempt 对 Runtime 本地执行槽位的占用（H2：5 个双臂都触发的主单元配对，均值 3.6981 s），并在指定的 FIFO 单槽位合同下缩短后续任务等待（H3）**。短任务上双臂常常不能同时触发（F1），重观察延迟下的净收益见 F2。这些数字没有经过 Holm 检验。
