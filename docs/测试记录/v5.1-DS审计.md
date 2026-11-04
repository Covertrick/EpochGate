# v5.1 DS 审计

日期：2026-11-04。只读 `results/experiments/v5_0/ds/confirm/traces.jsonl`。本文件不改那份 JSONL。

封存文件 sha256：`f5277e8f61945f50db68267f0ff78fbd648cb553833ec51ca86d6d0a89c9bc39`。
分析实现 sha256：`953127e76727c5c2a6f0bfb212c7109902e4f52198bc4a3ca586dd57f2183e46`。
硬门槛：通过。未通过时不得开复现。

## 硬门槛

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| no_duplicate_keys | 通过 | duplicate keys=0 |
| complete_7000_7049 | 通过 | missing=0 extra=0 |
| bystander_workflows | 通过 | bad_role_traces=0 bad_id_traces=0 count_hist={8: 200} |
| p95_recompute | 通过 | mismatched_traces=0; linear interpolation at rank (n-1)*0.95 |
| backend_unknown | 通过 | non_unknown=0 residual_filled=0 |
| model_and_thinking | 通过 | model_bad=0 thinking_bad=0 |
| event_order | 通过 | G0_closed_early=0 G2_not_closed=0 G2_order_bad=0 G0_stop_marked=0 |
| arm_order_balanced | 通过 | G0_first_pairs=56 G2_first_pairs=44 orders={'G2-localG0': 88, 'G0G2-local': 112} |
| failures_recorded | 通过 | failed_traces=0/200 rate_limit_markers=0 errors={} |
| fifty_independent_pairs_per_load | 通过 | usable={'medium': 50, 'high': 50}. Same seed integer is reused across loads and must not be pooled as 100 iid pairs. Bootstrap resamples these pairs, not the 8 bystanders. |
| published_numbers | 通过 | match |

## 记录里没有写死的配置

model and thinking are on every trace; max_tokens and temperature are not. Protocol says max_tokens=512 temperature=0.0. Runner prompt uses 'about {words}'; protocol template is 'Write a detailed inventory restocking plan in exactly {words} English words. Do not use tools.'. No wall-clock timestamp, so time-of-day confounding cannot be tested from the JSONL. The runner does not record retries.

失败 trace 0 条，限流标记 0 条。输出 token 有值的行 1900/2000。臂序：G0 先跑 56 对，G2-local 先跑 44 对。每条 trace 的 bystander 数分布：{8: 200}。
Bystander 是各自的 task，但共用 2 个本地槽，所以不是 8 个独立样本。Bootstrap 只重抽 (seed, load) 配对。

## 按封存 JSONL 重算

Bootstrap 10000 次，种子 20261104，单位是配对。`scripts/analyze_v50.py` 把次数截到 2000，本表不沿用那个截断。
相对值 = 配对差均值 / 该负载 G0 的 P95 均值。正数表示 G2-local 更短。

| 负载 | n | ΔW 均值 [2.5%, 97.5%] | 相对 | 槽门槛 | Δe2e 均值 [2.5%, 97.5%] | 相对 | 端到端门槛 | 同向 / 分离 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| medium | 50 | 0.891 [0.697, 1.074] | 21.4% | 过 | 0.873 [0.669, 1.070] | 12.3% | 过 | 43 / 2 |
| high | 50 | 0.863 [0.664, 1.066] | 11.2% | 过 | 0.922 [0.698, 1.153] | 8.6% | 未过 | 40 / 2 |

高负载端到端相对值没有过 10% 门槛。这里只复述重算结果，不把它写成过门槛。
槽等待与端到端多数同向变短。分离计数不是稳定的 D2 分叉。服务端状态仍全部是 `unknown`。

## 敏感度

Pearson r，配对差对配对差。它描述伴随变化，不区分解释 A–D。

- medium：Δe2e 与输出 token 差 r=—；与焦点端到端差 r=0.289；Δ槽等待与 ΔU_client r=0.247。
- high：Δe2e 与输出 token 差 r=—；与焦点端到端差 r=-0.207；Δ槽等待与 ΔU_client r=-0.145。

## 复现样本量

n = min(50, max over loads of the normal approximation n whose 95% half-width is at most 0.25s). Planning SD comes from sealed 7000-7049 and is not mixed into the replication estimate. Do not add seeds after seeing replication outcomes.

冻结 n=33。种子 8100–8132，与 6000–6014、7000–7049 不相交。
- medium：规划 SD=0.670 秒，该负载单独需要 n=28。
- high：规划 SD=0.730 秒，该负载单独需要 n=33。

## 预登记模型

M0 在 DS 上可拟合：ΔU_client 是焦点本地槽占用差，不是 token；ΔT 是 bystander P95 的配对差；α、β 只在中负载估计，再预测高负载。
M1、M2 需要可观测的后端残余。DS 没有这项，不把残余写成 0，也不拟合。

