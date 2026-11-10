# 阶段 E（P1.0–P1.6）结果与进入 F 阶段判定报告

日期：2026-11-02。版本：`1.1.0`。

## 一、核心结论：能否进入阶段 F？

**结论：可以进入阶段 F（判定为 `Branch_A_enter_F_continue_research`：取消有明确可测效果，本地槽位占用与排队等待显著下降，且任务正确性与可恢复性未受损）。**

机器可读判定文件：[`results/real/phase_e/summary/decision_for_stage_f.json`](../../../results/real/phase_e/summary/decision_for_stage_f.json)。

| 研究问题 | 指标与统计口径（配对 bootstrap 10,000 次） | 实测结果 | 预设门槛 | 判定 |
| --- | --- | --- | --- | --- |
| **RQ1（输出层 / 主机制）** | E1 成功触发配对的 \(\Delta_{\text{text}} = T_{A,\text{post}} - T_{B,\text{post}}\)（`tiktoken/cl100k_base`） | 均值 **355.57** 词元，中位数 **416.0** 词元，95% CI **[207.86, 500.86]**；B 臂撤销后客户端可观察词元与事件恒为 **0** | \(\delta_{\min} \ge 25\) 词元且 CI 下界 > 0 | **通过** |
| **RQ2（执行层 / 系统效应）** | E1 旧 attempt 本地槽位占用缩减 \(\Delta_{\text{slot}}\)；E3 三任务串行队列后续任务（`t1, t2`）平均排队等待缩减 | E1 \(\Delta_{\text{slot}}\) 均值 **2.53 s**（95% CI **[1.21, 3.96] s**）；E3 \(\Delta_{\text{slot}}\) 均值 **3.53 s**（95% CI **[2.86, 4.31] s**）；E3 `serial_queue_x3` 后续任务等待减少 **4.96 s**（中位数 **6.20 s**，95% CI **[1.77, 6.93] s**） | \(\delta_{\min} \ge 0.40\text{ s}\) 且 CI 下界 > 0 | **通过** |
| **RQ3（正确性层 / 硬约束）** | 被撤销旧 attempt 是否越权提交（`old_attempt_committed`）；可恢复任务恢复后是否恰好提交一次 | 全部测试行（E1+E2+E3）**0 次**被撤销旧 attempt 提交；E2 高收益可恢复格 B 臂 **2/2** 成功恢复并唯一提交（A 臂 1/2 成功、1 次重规划模型未发工具调用）；E3 三任务队列 B 臂 **3/3** 批次完成全部 3 次有效提交 | 旧提交 = 0；B 可恢复不退化 | **通过** |

---

## 二、P1.0–P1.6 交付物清单

| 阶段 | 内容 | 证据路径 |
| --- | --- | --- |
| **P1.0** | 阶段 A–D 基线冻结校验（阶段一重算 matches，阶段 C 确定性 `verdict=pass`） | [`results/real/phase_e/p1_0_freeze_manifest.json`](../../../results/real/phase_e/p1_0_freeze_manifest.json) |
| **P1.1** | 独立、定时、可重放外生事件驱动器 `ScenarioDriver` + 确定性时序 8 项不变量测试 | [`src/epochgate/scenario_driver.py`](../../../src/epochgate/scenario_driver.py)、[`tests/test_timed_scenario_driver.py`](../../../tests/test_timed_scenario_driver.py) |
| **P1.2** | Runtime `slot_released` 事件、`await_recovery_on_depletion` 恢复等待、统一 A/B 行结构与按 `task_id` 隔离的重规划统计 | [`src/epochgate/runtime.py`](../../../src/epochgate/runtime.py)、[`src/epochgate/stage_e_runner.py`](../../../src/epochgate/stage_e_runner.py) |
| **P1.3** | E1 开发种子（9 格，`10xx` 种子段）校准运行与长度/时机拉开核验 | [`results/real/phase_e/dev/e1_dev.jsonl`](../../../results/real/phase_e/dev/e1_dev.jsonl)、[`results/real/phase_e/dev/calibration_report.json`](../../../results/real/phase_e/dev/calibration_report.json) |
| **P1.4** | 正式实验协议冻结（种子隔离、随机 A/B 顺序、\(\delta_{\min}\) 门槛） | [`docs/档案/v1.2/阶段E协议.md`](阶段E协议.md)、[`results/real/phase_e/protocol_frozen.json`](../../../results/real/phase_e/protocol_frozen.json) |
| **P1.5** | 正式测试种子分批运行（E1 `20xx`、E2 `30xx`、E3 `40xx`） | [`results/real/phase_e/test/e1_cancellation.jsonl`](../../../results/real/phase_e/test/e1_cancellation.jsonl)、[`e2_correctness.jsonl`](../../../results/real/phase_e/test/e2_correctness.jsonl)、[`e3_queueing.jsonl`](../../../results/real/phase_e/test/e3_queueing.jsonl) |
| **P1.6** | 配对 bootstrap 汇总表（JSON + CSV）与 F 阶段决策文件 | [`results/real/phase_e/summary/`](../../../results/real/phase_e/summary) 下 6 组 JSON/CSV + `decision_for_stage_f.json` |

---

## 三、开发种子校准结果（P1.3）

在 `10xx` 开发种子上对 E1 9 格运行配对试验（见 [`calibration_report.json`](../../../results/real/phase_e/dev/calibration_report.json)）：

1. **长度档是否真正拉开**：
   - `short`（目标 50 词）：A 臂完整流平均 **98.7** 词元（中位数 105）；
   - `mid`（目标 200 词）：A 臂完整流平均 **411.7** 词元（中位数 374）；
   - `long`（目标 450 词）：A 臂完整流平均 **702.0** 词元（中位数 633）。三档实际输出长度显著拉开。
2. **撤销时机是否形成区分**：
   - `early`（`0.25s`）：撤销前中位词元 **26**（生成进度中位数 `28.2%`），触发率 **6/6**；
   - `mid`（`0.80s`）：撤销前中位词元 **100**（生成进度中位数 `64.7%`），在 `short_mid` 出现 `trigger_not_fired` / `trigger_late`；
   - `late`（`1.80s`）：撤销前中位词元 **237**，在 `short_late` 全部在 1.80s 前已完成生成或已进入工具调用，直接暴露了“短任务 + 晚撤销”的自然收益边界。
3. **开发集配对方差与代表性条件锁定**：
   - 7 对成功触发配对中，\(\Delta_{\text{text}}\) 均值 **378.14**（标准差 `245.27`），\(\Delta_{\text{slot}}\) 均值 **2.69 s**（标准差 `1.89 s`）。
   - 据此锁定 E2 的两个代表性条件：**高收益空间 `long_early`（450 词，0.25s）** 与 **低收益边界 `short_late`（50 词，1.80s）**。

---

## 四、正式测试集结果（P1.5–P1.6）

### 4.1 E1：取消机制主实验（[`e1_cancellation_effect.json`](../../../results/real/phase_e/summary/e1_cancellation_effect.json)）

- **计划配对数**：9 对（18 次流式请求）；**两臂均成功触发配对数**：7 对（触发率 `77.8%`）。
- **未触发分布**：仅出现在 `short_mid`（B 臂 67 词元在 0.8s 前完成，`trigger_not_fired`）与 `short_late`（A/B 两臂均在 1.8s 前完成，`trigger_not_fired`）。所有 `mid_*` 与 `long_*` 以及 `short_early` 均 100% 触发。
- **配对效应（n=7 触发配对）**：
  - 客户端可观察失效文本词元减少量 \(\Delta_{\text{text}}\)：均值 **355.57**，中位数 **416.0**，95% bootstrap CI **[207.86, 500.86]**。
  - 旧 attempt 本地槽位占用缩减 \(\Delta_{\text{slot}}\)：均值 **2.53 s**，中位数 **1.72 s**，95% bootstrap CI **[1.21, 3.96] s**。
  - 各格明细：`long_mid` 减少 **680** 词元 / **5.85 s** 槽位占用；`long_late` 减少 **494** 词元 / **4.37 s**；`long_early` 减少 **430** 词元 / **3.42 s**；`mid_mid` 减少 **416** 词元 / **1.72 s**；`short_early` 减少 **63** 词元 / **0.51 s**。
  - **正确性**：全部 18 次请求 `old_attempt_committed = False`。

### 4.2 E2：任务正确性与可恢复性实验（[`e2_task_correctness.json`](../../../results/real/phase_e/summary/e2_task_correctness.json)）

使用独立定时 `ScenarioDriver`（`t_change` 清零、`t_restore` 定时恢复，不依赖 A/B 完成时刻）：

| 条件 | 任务类型 | 配对数 | 触发情况 | A 臂结果 | B 臂结果 | 旧 attempt 越权提交 |
| --- | --- | --- | --- | --- | --- | --- |
| `high_headroom` (`long_early`) | `static` | 2 | `static_control` (2/2) | 2/2 提交 1 次，库存 1→0 | 2/2 提交 1 次，库存 1→0 | 0 |
| `high_headroom` (`long_early`) | `invalidate` | 2 | `triggered` (2/2) | 2/2 零提交，\(\Delta_{\text{text}}=652, 585\) | 2/2 零提交，撤销后 0 词元 | 0 |
| `high_headroom` (`long_early`) | `recoverable` | 2 | `triggered` (2/2) | 1/2 成功恢复提交（`seed=3031` 重规划未发工具调用） | **2/2 成功恢复并唯一提交**（`side_effect_once=True`） | 0 |
| `low_headroom` (`short_late`) | `static` | 2 | `static_control` (2/2) | 2/2 提交 1 次 | 2/2 提交 1 次 | 0 |
| `low_headroom` (`short_late`) | `invalidate` | 2 | `trigger_not_fired` (2/2) | 50 词短请求在 1.8s 变化前已正常完成提交 | 同左（属于未触发边界，非旧资格越权） | 0 |
| `low_headroom` (`short_late`) | `recoverable` | 2 | `trigger_not_fired` / `trigger_late` | 在 1.8s 变化前已完成或已出工具调用 | `seed=3081` B 臂在 `trigger_late` 下仍正确重规划并完成唯一提交 | 0 |

### 4.3 E3：串行队列压力与系统影响实验（[`e3_queueing_impact.json`](../../../results/real/phase_e/summary/e3_queueing_impact.json)）

在 `max_inflight=1`（串行单槽位）下对比 `low_single`（3 对）与 `serial_queue_x3`（3 对批次，每批 3 个任务 `t0, t1, t2` 竞争同一本地推理槽位，`t0` 在生成期遭遇外生清零并在 `1.0s` 后恢复）：

- **首任务旧 attempt 槽位占用缩减（\(\Delta_{\text{t0\_slot}}\)，n=6 批次）**：均值 **3.53 s**，中位数 **3.37 s**，95% CI **[2.86, 4.31] s**。
- **三任务串行队列中后续任务（`t1, t2`）平均排队等待缩减（n=3 批次聚类）**：
  - `seed=4021`：A 臂后续任务平均等待 **17.11 s** vs B 臂 **15.34 s**（B 减少 **1.77 s**；批次总耗时 A **61.38 s** vs B **43.44 s**，B 快 **17.94 s**）；
  - `seed=4022`：A 臂后续任务平均等待 **16.65 s** vs B 臂 **10.45 s**（B 减少 **6.20 s**；批次总耗时 A **45.00 s** vs B **42.16 s**，B 快 **2.84 s**）；
  - `seed=4023`：A 臂后续任务平均等待 **18.23 s** vs B 臂 **11.31 s**（B 减少 **6.93 s**；批次总耗时 A **54.05 s** vs B **52.88 s**，B 快 **1.17 s**）。
  - 后续任务等待缩减均值 **4.96 s**（中位数 **6.20 s**，95% CI **[1.77, 6.93] s**）。
- **有效完成数**：`serial_queue_x3` 的 3 个批次中，B 臂全部达成 **3/3** 有效提交（共 9/9 任务成功）；A 臂在 `seed=4021` 与 `seed=4023` 各有 1 个任务因重规划模型未输出工具调用而未提交（共 7/9 成功）。

---

## 五、进入阶段 F 的具体工作范围与边界约束

依据四分支决策规则，当前满足 **分支 A（`Branch_A_enter_F_continue_research`）**，正式进入阶段 F。阶段 F 的范围固定如下：

1. **收益边界（F1）**：围绕 `short_mid` / `short_late` 等高 `trigger_not_fired` / `trigger_late` 区域，量化任务长度与状态变化频率的盈亏平衡点。
2. **观察成本（F2）**：注入可控的观察延迟/刷新开销，检验高观察成本是否吞噬 B 臂提前释放槽位带来的重规划与排队收益。
3. **后端能力边界（F3）**：以不支持取消或仅支持非流式响应的后端作为能力边界对照（控制其他协议差异）。
4. **负载与公平边界（F4）**：在突发任务到达与串行队列压力下检验长短任务混排的等待分布。
5. **机制消融 1（F5）**：不提前停止本地流但保留 Gateway 资格门闩（即 A 臂机制）vs 提前停流 + 门闩（B 臂机制），分离安全性与及时性贡献。
6. **机制消融 2（F6）**：按依赖来源过滤的刷新 vs 任意来源变脏即盲目刷新，量化不必要重规划的减少量。
7. **明确延后**：**J1 联合打分调度器暂不进入阶段 F**，直到上述边界与消融实验完成且证明临近准入无法解释剩余瓶颈。
