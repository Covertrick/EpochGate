# Week 5 预注册分析

本次是 Week 5 的预注册，以及 Week 3/4 原始表的独立重算。Week 5 的新样本尚未分析。下面所有 Week 5 估计都空着，数字只来自已经冻结的 Week 3 联合表和 Week 4 的原始对照。

统计计划写在 `week3/statistical_plan.md`。该文件当前 SHA256 为 `cdc83a6031bb5c5138d6f1815cf52e5f6496cbd4ddb6028ca9c69a25282544aa`，与 `week3/statistical_plan.sha256` 一致。主程序使用计划里的种子 `20261120` 和 `B = 4000`。计划值与本次要求的敏感性种子、抽样次数相同，因此主程序只有这一套。种子 `20261120` 到 `20261124` 的重复只作为主比较的敏感性。

## 估计目标

### 主终点

主终点是 Claim 对 Direct 的过期权威接受配对差。

格子指标 `authority` 只在 `authority_decision == STALE_ACCEPTED` 时为 1，其余记录值，包括 `UNKNOWN`，为 0。簇内配对差是 `I(Claim) - I(Direct)`。点估计是主比较簇上的平均配对差。正值表示 Claim 更常接受过期权威。

单位是提案-场景簇，键为 `cluster_key`（`proposal_id` 与 `scenario_id`）。格子留在簇内，不作为独立样本。崩溃重复、RSR 重复和其他重复运行不进入主终点分母，也不被当成独立提案。

主编码是 `UNKNOWN_AS_ZERO`。`UNKNOWN`、`BLOCKED` 和未观测格子留在分母里。主分析不删除这些行来缩小分母。

### 区间

区间是簇 bootstrap 的 95% 百分位区间。本计划不使用 Wilson 区间。

簇按 `cluster_key` 字典序进入抽样列表。每次比较都从给定种子重新构造 `random.Random`。每次重抽有放回地抽取与簇数相同的簇，计算平均配对差。`B = 4000`。排序后的下标是 `floor(0.025 × 3999) = 99` 与 `floor(0.975 × 3999) = 3899`。

McNemar 精确双侧概率只作补充，使用不一致对 `b`（仅 Claim 为 1）和 `c`（仅对照为 1）。它不决定主终点。

### 三种 UNKNOWN 政策

各指标把下列记录视为 UNKNOWN：

- `authority`：`authority_decision == UNKNOWN`
- `semantic`：`semantic_validity == UNKNOWN`
- `successor`：`successor_status == UNKNOWN`
- `outdated`：`authority_decision == UNKNOWN` 或 `semantic_validity == UNKNOWN`

三种政策：

1. `UNKNOWN_AS_ZERO` 是主编码。UNKNOWN 格子的指标为 0，格子留在簇里。
2. `UNKNOWN_AS_FAILURE` 只作敏感性。该指标的 UNKNOWN 格子改记为 1，格子仍留在簇里。
3. `UNKNOWN_EXCLUDED` 只作敏感性。UNKNOWN 格子同时离开分子和分母。Claim 与对照任一侧未知，整个簇离开这对比较。缺的一侧不填 0，也不填 1。

Week 3 计划里另外两个计分指标仍按原定义重算，供历史核对。它们不是 Week 5 的新增确认性终点。

- `semantic`：仅 `semantic_validity == INVALID` 时为 1。
- `successor`：仅 `successor_status` 为 `FAILED` 或 `NOT_COMPLETED` 时为 1。`CORRECT_STOP`、`COMPLETED`、`COMPLETED_VALID`、`NOT_APPLICABLE` 都不是 1。
- `outdated`：`semantic_validity == INVALID`，并且 `authority_decision == NO_STALE_ACCEPTANCE_RECORDED`，并且物理上能看见字节。看见字节指 `physical_effect_state` 为 `CONFIRMED_EFFECT`、`NOT_LEGAL` 或 `PRESENT_UNVERIFIED`，或者 `recovery_state == RESTORED_BYTES`。

### 次要终点

次要终点在 Week 5 文件冻结后按同一单位和同一 bootstrap 计算。本次不填这些估计。

配对次要终点使用与主终点相同的 `authority` 指标和 `UNKNOWN_AS_ZERO`：

- Claim 对 Admission
- Claim 对 Outbox

边际次要终点是主比较簇上、簇内格子指标的平均。簇大小相同时，它等于主比较格子的比例。分母保留 UNKNOWN 格子。

- 无效语义发布：`semantic_validity == INVALID`
- 重复逻辑效果：`duplicate_effect` 为真
- 被取代的决定：计分状态含 `SUPERSED`，或证据状态为 `SEMANTICALLY_SUPERSEDED`
- 完成：`runner_status == COMPLETED`
- 未解决结局：`adjudication == UNRESOLVED`，或 `terminal_outcome == UNKNOWN`，或 `joint_observed` 为假

### 探索性终点

下列项目是探索性的，不参与确认性决定。本次不计算它们的 Week 5 估计：

- RSR 开销
- 版本栅栏开销
- 提供者合同切片
- 第二生成器差异

## 主比较过滤

过滤以 `week3/week3_replay_joint.csv` 的 `in_main_comparison` 为准。原始表与“排除 S06 和 S09”这一规则一致：被标为排除的场景是 S06 与 S09。主比较是 25 个簇、100 个格子。四条路径各 25 格。正式联合表共 108 行、27 个簇，队列都是 `WEEK3_FORMAL`，四条路径各 27 格。原始表与预期的 25 簇、100 格没有分歧。

## Week 5 主分析的阻断

Week 5 主分析在下面两个文件都存在并且已经冻结之前不做。冻结指分析开始前记录内容散列，分析期间文件内容保持不变。

- `week5/experiments/replay_joint.csv`
- `week5/semantic/ablation_results.csv`

本次两个文件都不存在。Week 5 的主终点、次要终点和探索性终点都没有估计。CSV 里的数字全部是 Week 3/4 的历史重算。

## Week 3 主编码重算

来源是 `week3/week3_replay_joint.csv`。算法按上面的定义独立实现，没有调用 Week 3 的统计函数，也没有把已发表汇总表当作输入。算完之后才与 `week3/week3_validation_report.json` 和 `week4/metrics_sensitivity.csv` 比较。

审计规则：配对差绝对差大于 0.01，或区间任一端点绝对差大于 0.02，记为审计不一致。簇数或不一致簇数不同也记为不一致。本次 12 个主编码配对差都没有触发。

| 比较 | 指标 | 配对差 | 95% 下界 | 95% 上界 | 簇数 | 不一致簇 |
| --- | --- | --- | --- | --- | --- | --- |
| Claim vs Direct | authority | -0.440000 | -0.640000 | -0.240000 | 25 | 11 |
| Claim vs Direct | semantic | -0.440000 | -0.640000 | -0.240000 | 25 | 11 |
| Claim vs Direct | successor | -0.160000 | -0.320000 | -0.040000 | 25 | 4 |
| Claim vs Direct | outdated | -0.040000 | -0.120000 | 0.000000 | 25 | 1 |
| Claim vs Admission | authority | -0.160000 | -0.320000 | -0.040000 | 25 | 4 |
| Claim vs Admission | semantic | -0.200000 | -0.360000 | -0.040000 | 25 | 5 |
| Claim vs Admission | successor | -0.160000 | -0.320000 | -0.040000 | 25 | 4 |
| Claim vs Admission | outdated | -0.040000 | -0.120000 | 0.000000 | 25 | 1 |
| Claim vs Outbox | authority | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim vs Outbox | semantic | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim vs Outbox | successor | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim vs Outbox | outdated | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |

authority 与 semantic 的 Claim vs Direct 差都是 `-11/25`。不一致的 11 个簇都是对照为 1、Claim 为 0。

Claim vs Outbox 在四个计分指标上的配对差都是 0，不一致簇数是 0。这是这 25 个簇的描述。它不构成等价性检验，也不构成 Claim 优于 Outbox 的结论。`week4/independence_decision.json` 记录的机制判断是 `PARTIALLY_SHARED`。该判断来自共享运行器、共享接收端和共享恢复入口的审计，不是由这个零配对差证明的。

## 三种 UNKNOWN 政策下的 Claim vs Direct

主比较里 `authority_decision == UNKNOWN` 的格子是 0，所以 authority 的三种政策是同一组数。semantic 的 UNKNOWN 格子是 31。

| 指标 | 政策 | 配对差 | 95% 下界 | 95% 上界 | 簇数 | 不一致簇 |
| --- | --- | --- | --- | --- | --- | --- |
| authority | UNKNOWN_AS_ZERO | -0.440000 | -0.640000 | -0.240000 | 25 | 11 |
| authority | UNKNOWN_AS_FAILURE | -0.440000 | -0.640000 | -0.240000 | 25 | 11 |
| authority | UNKNOWN_EXCLUDED | -0.440000 | -0.640000 | -0.240000 | 25 | 11 |
| semantic | UNKNOWN_AS_ZERO | -0.440000 | -0.640000 | -0.240000 | 25 | 11 |
| semantic | UNKNOWN_AS_FAILURE | -0.040000 | -0.160000 | 0.080000 | 25 | 3 |
| semantic | UNKNOWN_EXCLUDED | -0.142857 | -0.357143 | 0.000000 | 14 | 2 |

已发表的 Claim vs Direct authority 与 semantic 在 `UNKNOWN_AS_ZERO` 下约为 -0.44，区间约为 [-0.64, -0.24]。重算结果就是 -0.440000 与 [-0.640000, -0.240000]，落在 0.01 与 0.02 的阈值内。

semantic 的点估计在三种政策下仍为负。`UNKNOWN_AS_ZERO` 的区间整体低于 0。`UNKNOWN_AS_FAILURE` 的区间是 [-0.160000, 0.080000]，跨过 0。`UNKNOWN_EXCLUDED` 的上界是 0，簇数从 25 降到 14。semantic 上“区间整体低于 0”依赖把 UNKNOWN 记为 0。这条区间结论对把 UNKNOWN 记为失败不稳健。

同一阈值下，三种政策、三对比较、四个指标的 36 行都与 `week4/metrics_sensitivity.csv` 一致。其中 outdated 的 Claim vs Direct 在 `UNKNOWN_AS_FAILURE` 下是 0.360000，区间 [0.200000, 0.560000]。这组正数只属于敏感性编码，不进入主终点。

## 留一场景与种子

留一场景每次去掉一个场景的全部主比较簇，重算 Claim vs Direct 的 authority 与 semantic，政策为 `UNKNOWN_AS_ZERO`，种子 20261120，抽样 4000 次。S01 到 S18 都做了。点估计没有变成非负。S06 与 S09 本来就不在主比较里，去掉它们以后估计仍是 -0.440000，区间仍是 [-0.640000, -0.240000]。36 行与 `week4/leave_one_scenario_out.csv` 的差没有超过审计阈值。

同时去掉 S17 与 S18 是敏感性重算，不是新的确认性比较。剩下 21 个簇。Claim vs Direct 的 authority 与 semantic 都是 -0.333333，区间 [-0.523810, -0.142857]。符号仍为负。

主比较 Claim vs Direct 在种子 20261120、20261121、20261122、20261123、20261124 上各抽 4000 次。authority 与 semantic 的估计都是 -0.440000，区间都是 [-0.640000, -0.240000]。两端极差都是 0。没有端点相对种子 20261120 的移动超过 0.05。与 `week4/bootstrap_seed_stability.csv` 的差没有超过审计阈值。

## 历史边际计数

这些数是 Week 3 主比较 100 格的重算，用来固定次要终点的定义。它们不是 Week 5 估计。

- authority 15/100，semantic 48/100，successor 16/100，outdated 34/100
- 无效语义发布 48/100
- 重复逻辑效果 0/100
- 被取代的决定 0/100
- 完成 96/100
- 未解决结局 0/100
- 四项主 oracle 至少一项为 UNKNOWN 的格子 31/100

完整行在 `week5/statistics/recomputed_results.csv`。三种政策在 `week5/statistics/sensitivity_analysis.csv`。五种种子在 `week5/statistics/bootstrap_results.csv`。
