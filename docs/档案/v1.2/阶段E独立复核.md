# 阶段 E 独立复核报告（F0 里程碑交付）

日期：2026-11-02。对应复核脚本：[`scripts/audit_stage_e.py`](../../../scripts/audit_stage_e.py)，机器可读审计产物：[`results/real/phase_e/summary/evidence_audit.json`](../../../results/real/phase_e/summary/evidence_audit.json)。

## 1. 复核范围与一致性结论

| 检查项 | 复核方法 | 结果 |
| --- | --- | --- |
| 原始行重算一致性 | 不读取 `summary/*.json`，仅从 `results/real/phase_e/test/*.jsonl` 重新计算配对差与 10,000 次 bootstrap | `recomputed_matches_published_e1_summary = true`（均值 `355.5714`、CI `[207.8571, 500.8571]` 完全一致） |
| 开发种子与测试种子隔离 | 比对 `dev/e1_dev.jsonl` 与 `test/*.jsonl` 全部 `seed` 集合 | 开发种子 `1011–1091`（9 个），测试种子 `2011–4023`（27 个），交集为空（`overlap = []`） |
| A/B 执行顺序随机化 | 检查每对 `arm_order` 字段 | 同时包含 `"AB"` 与 `"BA"`，按种子确定性伪随机交替 |
| Bootstrap 重采样单位 | 核查 `analyze_stage_e.py` 与 `audit_stage_e.py` | E1/E2 严格按 `pair_id` 重采样；E3 严格按 `batch_id`（即配对批次）重采样，未把单次流事件或同批内 3 个任务当作独立样本 |
| 配对触发四状态分类 | 区分 `both_triggered`、`A_only_triggered`、`B_only_triggered`、`neither_triggered` | E1 共 9 对：`both_triggered = 7`、`A_only_triggered = 1`（`short_mid` `seed=2021`）、`neither_triggered = 1`（`short_late` `seed=2031`） |

---

## 2. E1 全部 7 组成功触发配对的差值明细（`both_triggered`）

不仅报告总体均值与 CI，逐条列出全部 7 组 `both_triggered` 配对的原始观测值与配对差：

| `pair_id` | 格子 (`cell_id`) | 种子 | 顺序 | A 撤销前词元 | B 撤销前词元 | A 撤销后词元 | B 撤销后词元 | \(\Delta_{\text{text}}\) (A−B) | A 槽位占用 (s) | B 槽位占用 (s) | \(\Delta_{\text{slot}}\) (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `E1_cancellation-short_early-seed2011` | `E1_short_early` | 2011 | `AB` | 21 | 26 | 63 | 0 | **+63.0** | 2.6632 | 2.1565 | **+0.5067** |
| `E1_cancellation-mid_early-seed2041` | `E1_mid_early` | 2041 | `BA` | 33 | 10 | 293 | 0 | **+293.0** | 4.0058 | 2.8114 | **+1.1944** |
| `E1_cancellation-mid_mid-seed2051` | `E1_mid_mid` | 2051 | `AB` | 104 | 118 | 416 | 0 | **+416.0** | 5.7905 | 4.0686 | **+1.7218** |
| `E1_cancellation-mid_late-seed2061` | `E1_mid_late` | 2061 | `BA` | 254 | 268 | 113 | 0 | **+113.0** | 5.1331 | 4.5085 | **+0.6247** |
| `E1_cancellation-long_early-seed2071` | `E1_long_early` | 2071 | `AB` | 63 | 24 | 430 | 0 | **+430.0** | 5.4483 | 2.0278 | **+3.4205** |
| `E1_cancellation-long_mid-seed2081` | `E1_long_mid` | 2081 | `BA` | 47 | 89 | 680 | 0 | **+680.0** | 8.6530 | 2.8058 | **+5.8472** |
| `E1_cancellation-long_late-seed2091` | `E1_long_late` | 2091 | `AB` | 254 | 228 | 494 | 0 | **+494.0** | 8.0016 | 3.6306 | **+4.3710** |

未触发的 2 组计划配对（保留在分母中计入触发率 `7/9 = 77.78%`，不伪造 0 差值）：
- `E1_cancellation-short_mid-seed2021`（`A_only_triggered`）：A 臂 `pre=90, post=5`；B 臂在 `0.80s` 变化前已生成 67 词元并完成（`trigger_not_fired`）。
- `E1_cancellation-short_late-seed2031`（`neither_triggered`）：A 臂（98 词元）与 B 臂（168 词元）均在 `1.80s` 变化前自然完成（`trigger_not_fired`）。

---

## 3. E3 全部 6 个批次（含 3 个三任务串行批次）原始等待差与完成时间差

| `batch_id` | 队列条件 | 种子 | 顺序 | A 各任务首次等待 (s) | B 各任务首次等待 (s) | 后续任务 (`t1,t2`) 平均等待差 (s) | `t0` 槽位占用差 (s) | A 批次总耗时 (s) | B 批次总耗时 (s) | \(\Delta T_{\text{batch}}\) (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `E3-low_single-seed4011` | `low_single` (1) | 4011 | `AB` | `[0.001]` | `[0.001]` | — | +2.3738 | 11.2529 | 16.3606 | -5.1078 |
| `E3-low_single-seed4012` | `low_single` (1) | 4012 | `BA` | `[0.001]` | `[0.001]` | — | +3.9937 | 15.1298 | 9.8094 | +5.3205 |
| `E3-low_single-seed4013` | `low_single` (1) | 4013 | `BA` | `[0.001]` | `[0.001]` | — | +5.1701 | 12.9914 | 15.9069 | -2.9154 |
| `E3-serial_queue_x3-seed4021` | `serial_queue_x3` (3) | 4021 | `AB` | `[0.001, 12.44, 21.78]` | `[0.001, 10.60, 20.08]` | **+1.7704** | +3.6494 | 61.3806 | 43.4373 | **+17.9433** |
| `E3-serial_queue_x3-seed4022` | `serial_queue_x3` (3) | 4022 | `BA` | `[0.001, 13.79, 19.51]` | `[0.001, 6.22, 14.68]` | **+6.1975** | +2.9261 | 45.0029 | 42.1642 | **+2.8387** |
| `E3-serial_queue_x3-seed4023` | `serial_queue_x3` (3) | 4023 | `BA` | `[0.001, 12.56, 23.90]` | `[0.001, 6.95, 15.66]` | **+6.9251** | +3.0858 | 54.0545 | 52.8816 | **+1.1729** |

**统计边界说明**：E3 的 `serial_queue_x3` 仅有 3 个独立批次，虽然 3 个批次的后续任务平均等待差与批次总耗时差全部为正，但样本量 \(n=3\) 不支持对尾部 P99 做稳健推断；因此必须在 F4 中扩充独立串行批次并按任务位置报告完整分布。

---

## 4. 未触发与失败记录分类清单（严格分列，不合并）

| 分类 (`trigger_outcome` / 阶段) | 条数（含 dev+test） | 明细与归因 |
| --- | --- | --- |
| `trigger_not_fired` | 12 | 请求在外生状态变化时刻前已自然结束流式生成（全部发生在 `short_mid` 与 `short_late` 50 词短任务中） |
| `trigger_late` | 3 | `E1_dev-short_mid-seed1021:A`、`E1_dev-short_late-seed1031:B`、`E2-low_headroom-recoverable-seed3081:B`（外生变化发生时首 attempt 已进入 `tool_call_delta`） |
| `model_no_action`（重规划阶段） | 12 | 首 attempt 被撤销后，重规划新 attempt 模型只输出解释文本而未发出 `reserve` 工具调用（如 `E2-high_headroom-recoverable-seed3031:A`、`E3-serial_queue_x3-seed4021:A:t2`） |
| `transport_failed`（重规划阶段） | 1 | `E1_dev-long_late-seed1091:B` 重规划 attempt 遭遇偶发 `httpx.ConnectError` |
| `runtime_failed` | 0 | 0 次 Runtime 自身异常 |

---

## 5. 安全不变量表 vs 可用性与任务完成表（正式分立）

### 5.1 安全不变量表（Safety Invariants Table — 零容忍约束）

| 安全不变量指标 | 阶段 E 正式测试集（64 任务行） | 允许上限 | 状态 |
| --- | --- | --- | --- |
| 被撤销旧 attempt 成功提交次数 (`old_attempt_committed`) | **0** | 0 | **通过** |
| 单任务重复外部副作用次数 (`action_commits > 1`) | **0** | 0 | **通过** |
| 半截工具参数形成非法 `CandidateAction` 次数 | **0** | 0 | **通过** |
| Gateway 拦截并记录的过时/库存不足拒绝次数 (`action_rejects`) | **49** | —（正常拦截记账） | **通过** |

### 5.2 可用性与任务完成表（Availability & Task Completion Table — 统计比较）

| 实验子集 | 样本规模 | A 臂新 attempt / 任务完成率 | B 臂新 attempt / 任务完成率 | 恢复耗时 \(T_{\text{recovery}}\) (s) | 统计解释限制 |
| --- | --- | --- | --- | --- | --- |
| E2 静态控制 (`static_control`) | 4 对 | `4/4 (100%)` | `4/4 (100%)` | — | 流式健康基线 |
| E2 可恢复（`high_headroom` 触发对） | **2 对（小样本）** | `1/2 (50%)` | `2/2 (100%)` | A: `[9.225s]`；B: `[10.506s, 14.082s]` | **明确标为小样本（n=2），不作统计非劣结论；需在 F1-R 与 F2 中扩样** |
| E3 三任务串行队列 (`serial_queue_x3`) | 3 批（每臂 9 任务） | `7/9 (77.8%)`，超时 0 | `9/9 (100%)`，超时 0 | — | 需在 F4 中扩充独立批次 |
