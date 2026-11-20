# Week 4 统计敏感性

## 结论

主比较是 25 个提案-场景簇、100 个格子。S06 与 S09 不在主比较里。统计单位是簇。Bootstrap 重抽的是簇。预注册比较只有 Claim vs Direct、Claim vs Admission、Claim vs Outbox。主口径是 `UNKNOWN_AS_ZERO`：UNKNOWN 留在分母里，指标记为 0。`UNKNOWN_AS_FAILURE` 与 `UNKNOWN_EXCLUDED` 只作敏感性。

Week 3 的主结果是 Claim vs Direct 的 authority 与 semantic 配对差都是 −0.440000，95% 簇 bootstrap 区间都是 [−0.640000, −0.240000]。按同一算法重算，12 个预注册估计的配对差、区间、簇数和不一致簇数与 Week 3 报告一致。

Claim vs Direct 的方向按点估计是否离开负数来判断。点估计大于或等于 0，记为方向翻转。

- authority 在三种 UNKNOWN 政策下都是 -0.440000，区间 [-0.640000, -0.240000]。主比较里 `authority_decision = UNKNOWN` 的格子是 0，三种政策是同一组数。方向不翻转。authority 的这条主结论对 UNKNOWN 处理稳健。
- semantic 的点估计在三种政策下仍为负，方向不翻转。`UNKNOWN_AS_ZERO` 是 -0.440000，区间 [-0.640000, -0.240000]，整体低于 0。`UNKNOWN_AS_FAILURE` 是 -0.040000，区间 [-0.160000, 0.080000]，跨过 0。`UNKNOWN_EXCLUDED` 是 -0.142857，区间 [-0.357143, 0.000000]，上界是 0，簇数从 25 降到 14。semantic 上“区间整体低于 0”依赖把 UNKNOWN 记为 0。semantic 的主结论对 UNKNOWN 处理不稳健。
- 把 UNKNOWN 记为非失败，和把 UNKNOWN 记为失败，是这条配对差的两侧界。authority 的两侧界重合。semantic 的两侧界不能同时把配对差钉在 0 以下。
- 去掉 S17 与 S18 之后剩 21 个簇、84 个格子。Claim vs Direct 的 authority 与 semantic 都是 -0.333333，区间 [-0.523810, -0.142857]，仍整体低于 0。点估计不再是 −0.440000，符号仍为负，方向不翻转。
- outdated 的主口径仍是 `UNKNOWN_AS_ZERO`。它的 Claim vs Direct 是 −0.040000，区间 [−0.120000, 0.000000]。`UNKNOWN_AS_FAILURE` 下同一比较是 0.360000，区间 [0.200000, 0.560000]，点估计变为正。这组正数只属于敏感性编码，不进入主指标。

Claim vs Outbox 在主口径的四个指标上配对差都是 0，不一致簇数是 0。这个 0 是本次样本的描述。它不是等价检验，也不表示 Claim 与 Outbox 一样好。机制解释属于独立性审计。

终态 MATCH 上的四个分项是 `EFE_authority@success` 1/34、`EFE_semantic@success` 0/21、`EFE_successor@success` 0/4、`EFE_outdated@success` 0/17。后三项分子为 0，未被证据化。四项分开报，不合成为一个复合指标。

种子 20261120、20261121、20261122 下，没有任何区间端点相对 20261120 的移动超过 0.05。种子稳定性标记：否。

归档 `archived_joint.csv` 的 SHA256 是 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`，与要求的 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda` 一致。本分析没有修改 `week2/` 或 `week3/`。

## 样本

联合表 `week3/week3_replay_joint.csv` 有 108 行，`cohort` 全部是 `WEEK3_FORMAL`。`in_main_comparison` 为真的格子是 100，簇是 25。每个主比较簇都有 Direct、Admission、Claim、Outbox 四条路径。

被排除的 8 格是 S06 与 S09，各 4 格。S06 的机制角色是 `NON_DISCRIMINATIVE`，S09 是 `MECHANISM_SPECIFIC_DIAGNOSTIC`。这和统计计划、Week 3 报告里的主比较过滤一致。

主比较 100 格的 `runner_status` 是 COMPLETED 96 格、CRASHED 4 格、BLOCKED 0 格。`joint_observed` 为假的格子是 0。4 个 CRASHED 格是 S08 的 p10 与 p11 上的 Direct 和 Admission，留在分母里。没有为了缩小分母删掉 CRASHED、BLOCKED 或 UNKNOWN 行。

至少一项主 oracle 为 UNKNOWN 的格子是 31/100。主 oracle 指 `terminal_outcome`、`authority_decision`、`physical_effect_state`、`semantic_validity`。按指标分开数，UNKNOWN 格是 authority 0、semantic 31、successor 0、outdated 31。outdated 的 31 格就是 semantic 为 UNKNOWN 的那些格。

全部分母上的四个失败率仍是 authority 15/100、semantic 48/100、successor 16/100、outdated 34/100。主口径下 UNKNOWN 记为 0，所以这四个分子没有把未知格算进去。

## 估计

配对差是簇内 Claim 指标减对照指标的平均。正值表示 Claim 更常出现该指标。簇按 `cluster_key` 字典序进入抽样列表。每次比较都从给定种子重新构造 `random.Random`。抽样次数 B = 4000。排序后的下标是 `floor(0.025 × 3999) = 99` 与 `floor(0.975 × 3999) = 3899`。区间是这一对百分位，不是 Wilson 区间。

主口径下指标为 1 的条件与 Week 3 统计计划相同。

- authority：`authority_decision = STALE_ACCEPTED`。
- semantic：`semantic_validity = INVALID`。
- successor：`successor_status` 为 `FAILED` 或 `NOT_COMPLETED`。`CORRECT_STOP`、`COMPLETED`、`COMPLETED_VALID`、`NOT_APPLICABLE` 都不是 1。
- outdated：`semantic_validity = INVALID`，并且 `authority_decision = NO_STALE_ACCEPTANCE_RECORDED`，并且物理上能看见字节。看见字节指 `physical_effect_state` 为 `CONFIRMED_EFFECT`、`NOT_LEGAL` 或 `PRESENT_UNVERIFIED`，或者 `recovery_state = RESTORED_BYTES`。

各指标把下面的记录视为 UNKNOWN。

- authority：`authority_decision = UNKNOWN`。
- semantic：`semantic_validity = UNKNOWN`。
- successor：`successor_status = UNKNOWN`。
- outdated：`authority_decision = UNKNOWN` 或 `semantic_validity = UNKNOWN`。

`UNKNOWN_AS_FAILURE` 把该指标的 UNKNOWN 格改记为 1，格子仍留在簇里。`UNKNOWN_EXCLUDED` 让该格同时离开分子和分母。簇配对只在 Claim 与对照两侧都已观测时计算；任一侧未知，整个簇离开这对比较。缺的一侧不填 0，也不填 1。

`n_discordant` 是两侧指标不相等的簇数。这里只计数，没有另做检验。家族内的配对差，以及去掉 S17/S18 之后的重算，都是描述性敏感性，不是这三对以外的确认性比较。

## UNKNOWN 敏感性

下表是 25 个主比较簇上的三对比较、四个指标、三种政策。主口径是每个指标的 `UNKNOWN_AS_ZERO` 行。

| comparison | metric | unknown_policy | paired_diff | ci_low | ci_high | n_clusters | n_discordant |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Claim_vs_Direct | authority | UNKNOWN_AS_ZERO | -0.440000 | -0.640000 | -0.240000 | 25 | 11 |
| Claim_vs_Direct | authority | UNKNOWN_AS_FAILURE | -0.440000 | -0.640000 | -0.240000 | 25 | 11 |
| Claim_vs_Direct | authority | UNKNOWN_EXCLUDED | -0.440000 | -0.640000 | -0.240000 | 25 | 11 |
| Claim_vs_Direct | semantic | UNKNOWN_AS_ZERO | -0.440000 | -0.640000 | -0.240000 | 25 | 11 |
| Claim_vs_Direct | semantic | UNKNOWN_AS_FAILURE | -0.040000 | -0.160000 | 0.080000 | 25 | 3 |
| Claim_vs_Direct | semantic | UNKNOWN_EXCLUDED | -0.142857 | -0.357143 | 0.000000 | 14 | 2 |
| Claim_vs_Direct | successor | UNKNOWN_AS_ZERO | -0.160000 | -0.320000 | -0.040000 | 25 | 4 |
| Claim_vs_Direct | successor | UNKNOWN_AS_FAILURE | -0.160000 | -0.320000 | -0.040000 | 25 | 4 |
| Claim_vs_Direct | successor | UNKNOWN_EXCLUDED | -0.160000 | -0.320000 | -0.040000 | 25 | 4 |
| Claim_vs_Direct | outdated | UNKNOWN_AS_ZERO | -0.040000 | -0.120000 | 0.000000 | 25 | 1 |
| Claim_vs_Direct | outdated | UNKNOWN_AS_FAILURE | 0.360000 | 0.200000 | 0.560000 | 25 | 9 |
| Claim_vs_Direct | outdated | UNKNOWN_EXCLUDED | 0.000000 | 0.000000 | 0.000000 | 14 | 0 |
| Claim_vs_Admission | authority | UNKNOWN_AS_ZERO | -0.160000 | -0.320000 | -0.040000 | 25 | 4 |
| Claim_vs_Admission | authority | UNKNOWN_AS_FAILURE | -0.160000 | -0.320000 | -0.040000 | 25 | 4 |
| Claim_vs_Admission | authority | UNKNOWN_EXCLUDED | -0.160000 | -0.320000 | -0.040000 | 25 | 4 |
| Claim_vs_Admission | semantic | UNKNOWN_AS_ZERO | -0.200000 | -0.360000 | -0.040000 | 25 | 5 |
| Claim_vs_Admission | semantic | UNKNOWN_AS_FAILURE | -0.080000 | -0.200000 | 0.000000 | 25 | 2 |
| Claim_vs_Admission | semantic | UNKNOWN_EXCLUDED | -0.142857 | -0.357143 | 0.000000 | 14 | 2 |
| Claim_vs_Admission | successor | UNKNOWN_AS_ZERO | -0.160000 | -0.320000 | -0.040000 | 25 | 4 |
| Claim_vs_Admission | successor | UNKNOWN_AS_FAILURE | -0.160000 | -0.320000 | -0.040000 | 25 | 4 |
| Claim_vs_Admission | successor | UNKNOWN_EXCLUDED | -0.160000 | -0.320000 | -0.040000 | 25 | 4 |
| Claim_vs_Admission | outdated | UNKNOWN_AS_ZERO | -0.040000 | -0.120000 | 0.000000 | 25 | 1 |
| Claim_vs_Admission | outdated | UNKNOWN_AS_FAILURE | 0.080000 | 0.000000 | 0.200000 | 25 | 2 |
| Claim_vs_Admission | outdated | UNKNOWN_EXCLUDED | 0.000000 | 0.000000 | 0.000000 | 14 | 0 |
| Claim_vs_Outbox | authority | UNKNOWN_AS_ZERO | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim_vs_Outbox | authority | UNKNOWN_AS_FAILURE | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim_vs_Outbox | authority | UNKNOWN_EXCLUDED | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim_vs_Outbox | semantic | UNKNOWN_AS_ZERO | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim_vs_Outbox | semantic | UNKNOWN_AS_FAILURE | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim_vs_Outbox | semantic | UNKNOWN_EXCLUDED | 0.000000 | 0.000000 | 0.000000 | 14 | 0 |
| Claim_vs_Outbox | successor | UNKNOWN_AS_ZERO | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim_vs_Outbox | successor | UNKNOWN_AS_FAILURE | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim_vs_Outbox | successor | UNKNOWN_EXCLUDED | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim_vs_Outbox | outdated | UNKNOWN_AS_ZERO | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim_vs_Outbox | outdated | UNKNOWN_AS_FAILURE | 0.000000 | 0.000000 | 0.000000 | 25 | 0 |
| Claim_vs_Outbox | outdated | UNKNOWN_EXCLUDED | 0.000000 | 0.000000 | 0.000000 | 14 | 0 |

authority 与 successor 的 UNKNOWN 格数是 0，所以这两种指标的三行完全相同。semantic 与 outdated 会随政策变化。

semantic 的 Claim vs Direct 在主口径下有 11 个不一致簇，全部是 Claim 为 0、Direct 为 1。这些 Claim 里多数 `semantic_validity` 是 UNKNOWN，Direct 是 INVALID。`UNKNOWN_AS_FAILURE` 把这些 Claim 改记为 1，与 Direct 的 1 相减后变成 0。S17 的 p24 与 p25 两侧都已观测：Claim 是 VALID，Direct 是 INVALID，差仍是 −1。p06:S04 的方向相反：Claim 是 UNKNOWN，Direct 是 VALID；改记为失败后，这对差变成 +1。25 个簇的平均是 (−2 + 1) / 25 = −0.040000。`UNKNOWN_EXCLUDED` 拿掉至少一侧未知的 11 个簇，剩下 14 个簇里有 2 个 −1，平均是 −2/14 = −0.142857。点估计仍为负，区间不再整体低于 0。

outdated 的 Claim vs Direct 在主口径下只有一个不一致簇，p19:S13。Claim 的 semantic 是 UNKNOWN，按 0 计；Direct 满足 outdated 的失败条件。`UNKNOWN_EXCLUDED` 拿掉这个簇之后，剩下 14 个簇的差都是 0。`UNKNOWN_AS_FAILURE` 把 Claim 侧的语义未知改记为 1。Direct 的 outdated 指标在若干簇上仍是 0，于是配对差变成 0.360000。主指标继续使用 `UNKNOWN_AS_ZERO` 的 −0.040000。

Claim vs Admission 的 semantic 在主口径下是 −0.200000，区间 [−0.360000, −0.040000]。`UNKNOWN_AS_FAILURE` 把它移到 −0.080000，区间上界是 0。`UNKNOWN_EXCLUDED` 是 −0.142857，上界也是 0。authority 与 successor 的三对比较不随 UNKNOWN 政策变化。

Claim vs Outbox 在三种政策、四个指标下的配对差都是 0。semantic 与 outdated 的 `UNKNOWN_EXCLUDED` 把簇数降到 14，差仍是 0。这个 0 仍是描述。机制解释属于独立性审计。

## 去掉 S17 与 S18

这一节把 S17 与 S18 移出主比较，其余过滤不变，政策仍是 `UNKNOWN_AS_ZERO`。比较仍是原来的三对。这是敏感性重算，不是新的确认性比较。

| comparison | metric | paired_diff | ci_low | ci_high | n_clusters | n_discordant |
| --- | --- | --- | --- | --- | --- | --- |
| Claim_vs_Direct | authority | -0.333333 | -0.523810 | -0.142857 | 21 | 7 |
| Claim_vs_Direct | semantic | -0.333333 | -0.523810 | -0.142857 | 21 | 7 |
| Claim_vs_Direct | successor | 0.000000 | 0.000000 | 0.000000 | 21 | 0 |
| Claim_vs_Direct | outdated | -0.047619 | -0.142857 | 0.000000 | 21 | 1 |
| Claim_vs_Admission | authority | 0.000000 | 0.000000 | 0.000000 | 21 | 0 |
| Claim_vs_Admission | semantic | -0.047619 | -0.142857 | 0.000000 | 21 | 1 |
| Claim_vs_Admission | successor | 0.000000 | 0.000000 | 0.000000 | 21 | 0 |
| Claim_vs_Admission | outdated | -0.047619 | -0.142857 | 0.000000 | 21 | 1 |
| Claim_vs_Outbox | authority | 0.000000 | 0.000000 | 0.000000 | 21 | 0 |
| Claim_vs_Outbox | semantic | 0.000000 | 0.000000 | 0.000000 | 21 | 0 |
| Claim_vs_Outbox | successor | 0.000000 | 0.000000 | 0.000000 | 21 | 0 |
| Claim_vs_Outbox | outdated | 0.000000 | 0.000000 | 0.000000 | 21 | 0 |

S17 与 S18 共 4 个簇。在主口径下，这 4 个簇的 authority 与 semantic 都是 Claim 为 0、Direct 为 1。去掉之后，11 个负向不一致簇剩下 7 个，分母从 25 变为 21，配对差是 −7/21 = -0.333333。authority 与 semantic 的区间都是 [-0.523810, -0.142857]，整体低于 0。方向不翻转。

successor 的 Claim vs Direct 在全样本上是 −0.160000，4 个不一致簇都落在 S17 与 S18。去掉之后，这项配对差是 0.000000。Claim vs Admission 的 authority 也同样变为 0。这些变化说明通知冻结场景承担了 successor 的全部不一致，也承担了 authority、semantic 里的一部分不一致。authority 与 semantic 相对 Direct 的负号在去掉这 4 个簇之后仍然存在。

## 终态 MATCH 上的分项比率

主比较里 `terminal_outcome = MATCH` 的格子是 34，`MISMATCH` 是 66。这 34 格的 `joint_observed` 都为真，`adjudication = UNRESOLVED` 的是 0。下面四项以这 34 格为成功门，再按各指标自己的排除规则确定分母。分母彼此不同，不能相加，也不合并成一个复合比率。本节标题不用复合指标的名字。

成功门上的 UNKNOWN 同时离开分子和分母。这是这四项比率自己的规则，和预注册配对差的主口径 `UNKNOWN_AS_ZERO` 分开。配对差的主口径没有改用失败编码。

| metric_id | 分子 | 分母 | 分数 | unknown_handling | evidenced |
| --- | --- | --- | --- | --- | --- |
| `EFE_authority@success` | 1 | 34 | 1/34 | UNKNOWN_EXCLUDED | true |
| `EFE_semantic@success` | 0 | 21 | 0/21 | UNKNOWN_EXCLUDED | false |
| `EFE_successor@success` | 0 | 4 | 0/4 | UNKNOWN_EXCLUDED | false |
| `EFE_outdated@success` | 0 | 17 | 0/17 | UNKNOWN_EXCLUDED | false |

`EFE_authority@success` 的分子是 MATCH 且 `authority_decision = STALE_ACCEPTED`。唯一的分子格是 `w3-p06-Direct-1`，场景 S04，路径 Direct，`semantic_validity = VALID`。其余 33 个 MATCH 格是 `NO_STALE_ACCEPTANCE_RECORDED`。成功门里 authority UNKNOWN 是 0，所以分母等于 MATCH 格数 34。排除项是主比较之外的格子、终态不是 MATCH 的格子，以及 `authority_decision = UNKNOWN`。这项有观察到的分子。

`EFE_semantic@success` 的分子是 MATCH 且 `semantic_validity = INVALID`，本次是 0。分母是 MATCH 且语义为 VALID 或 INVALID 的 21 格，全部是 VALID。13 个 MATCH 且语义为 UNKNOWN 的格子同时离开分子和分母，不把未知写成“没有失败”。若把这 13 格留在分母并记为 0，分数会写成 0/34，分子仍是 0。两种分母下，成功终态上都没有观察到 semantic INVALID。分子为 0，这项未被证据化。主比较里另外 48 个 semantic INVALID 落在非 MATCH 终态，不进入本项。

`EFE_successor@success` 的分子是 MATCH、后继为 REQUIRED，并且状态不是完成，也不是 `CORRECT_STOP`、`NOT_APPLICABLE` 或 `UNKNOWN`。完成状态是 `COMPLETED` 与 `COMPLETED_VALID`。本次分子是 0。分母是 MATCH、REQUIRED，且状态不在 `CORRECT_STOP`、`NOT_APPLICABLE`、`UNKNOWN` 里的格子，共 4 格，都是 S17 的 Claim 与 Outbox，状态 `COMPLETED_VALID`。另外 4 个 MATCH 且 REQUIRED 且 `CORRECT_STOP` 的格子是 S18 的 Claim 与 Outbox，按既定规则排除：正确停止不是后继失败。适用分母是 4，不是 0，所以这里写 0/4，不写 0/0。分子为 0，这项未被证据化。

`EFE_outdated@success` 只在场景标签存在时计算。标签存在指 `outdated_scenario_label` 为 `TIMELY_NOTICE` 或 `NOTICE_MISSING_OR_DELAYED`。分子是其中的 `NOTICE_MISSING_OR_DELAYED`，本次是 0。分母是 17，全部是 `TIMELY_NOTICE`。17 个标签为 UNKNOWN 的 MATCH 格排除，因为这项只在标签存在时计算。`TIMELY_NOTICE` 留在分母，不改写成 `STALE_ACCEPTED`。分子为 0，这项未被证据化。主比较里另有 `NOTICE_MISSING_OR_DELAYED` 的格子，终态不是 MATCH，不进入本项。

## 按场景家族

家族来自两处已经写好的标注，本分析没有按研究假设另判。

S13 到 S18 用 `week3/scenario_manifest` 里的 `family` 字段：S13、S14、S15、S16 是 recovery，S17、S18 是 successor。这些字段与联合表的 `scenario_family` 一致。

S01 到 S12 的 Week 3 清单没有对应文件，Week 2 清单有 `mechanism_role`，没有 family 字段。这 12 个场景使用联合表里已经写入的 `scenario_family`：S01、S02、S04、S11 是 authority，S03 是 semantic，S08 是 recovery，S12 是 successor，S05、S07、S10 是 other。S06 与 S09 的联合表标注是 excluded，而且不在主比较里，所以不进入下面的家族表。

S05、S07、S10 保持 other。它们不归入 authority、semantic、recovery、successor。other 行留在表里，是为了让主比较的 100 格能对上，不是新造的家族。

下表的失败率是家族内格子数作分母的主口径比率，UNKNOWN 记为 0。UNKNOWN rate 是至少一项主 oracle 为 UNKNOWN 的格子比例。最右四列是该家族内 Claim vs Direct 的簇平均配对差。Claim vs Admission、Claim vs Outbox 的差写在 `week4/metrics_by_family.csv`。这些差是描述，不是新的确认性比较。

| family | scenarios | n_cells | n_clusters | unknown_any_rate | authority | semantic | successor | outdated | Direct authority | Direct semantic | Direct successor | Direct outdated |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| authority | S01, S02, S04, S11 | 20 | 5 | 0.750000 | 5/20 | 4/20 | 0/20 | 0/20 | -1.000000 | -0.800000 | 0.000000 | 0.000000 |
| semantic | S03 | 20 | 5 | 0.000000 | 0/20 | 20/20 | 0/20 | 20/20 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| recovery | S08, S13, S14, S15, S16 | 24 | 6 | 0.083333 | 0/24 | 14/24 | 0/24 | 14/24 | 0.000000 | -0.166667 | 0.000000 | -0.166667 |
| successor | S12, S17, S18 | 24 | 6 | 0.416667 | 10/24 | 10/24 | 16/24 | 0/24 | -1.000000 | -1.000000 | -0.666667 | 0.000000 |
| other | S05, S07, S10 | 12 | 3 | 0.333333 | 0/12 | 0/12 | 0/12 | 0/12 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |

按上表顺序，UNKNOWN 格数是 15、0、2、10、4，加总为 31。格子数是 20、20、24、24、12，加总为 100。

authority 家族 5 个簇的 Claim vs Direct authority 配对差是 −1.000000，5 个簇都不一致。同一家族里 Claim vs Admission、Claim vs Outbox 的 authority 差是 0。semantic 家族 S03 的四条路径 semantic 与 outdated 都是 20/20，三对配对差都是 0；这条语义失败在路径之间没有分开。recovery 的 Claim vs Direct semantic 与 outdated 都是 −0.166667，不一致簇是 p19:S13。successor 家族 6 个簇的 Claim vs Direct authority 与 semantic 都是 −1.000000。该家族 Claim vs Admission 的 authority、semantic、successor 都是 −0.666667。各家族的 Claim vs Outbox 配对差在这个样本里都是 0，仍按描述阅读。

## Bootstrap 种子

三个种子是 20261120、20261121、20261122。每个比较、每种政策、每个种子都重新开始抽样。主比较的 `UNKNOWN_AS_ZERO` 共 12 个区间，三个种子的上下界相同，相对 20261120 的移动是 0。

把三种政策和去掉 S17/S18 的重算也放进同一张种子表之后，唯一的非零移动出现在主样本、`UNKNOWN_AS_FAILURE`、Claim vs Direct、outdated。种子 20261120 与 20261122 的区间是 [0.200000, 0.560000]，种子 20261121 的区间是 [0.160000, 0.560000]。下界移动 0.040000，上界移动 0。0.040000 不超过 0.05。三个种子下这个敏感性区间都整体高于 0。

因此没有任何端点移动超过 0.05。种子稳定性标记：否。完整三种子表在 `week4/sensitivity_seed_stability.csv`。五种子文件 `week4/bootstrap_seed_stability.csv` 不属于本笔记。

## 归档与目录

`docs/开发文档/week1-historical-audit/archived_joint.csv` 的 SHA256 是 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`。与要求的 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda` 一致。

写入本目录之前，`week2/` 共 383 个文件，目录指纹 `f70bb89c361e00ddd1d3fc0ae63607acf7ed2f9f54951db6dc3ce87bafc5a510`。`week3/` 共 1403 个文件，目录指纹 `45d9051a8b847f82c9e8d9e442a56cf51fa958ace62f81639fe0cb5b41585b6f`。写出本笔记之后再次计算，两个指纹与写入前相同。本分析没有修改 `week2/` 或 `week3/`。

本分析写出的文件是 `week4/metrics_sensitivity.csv`、`week4/metrics_by_terminal.csv`、`week4/metrics_by_family.csv`、`week4/sensitivity_seed_stability.csv` 和 `week4/statistical_sensitivity.md`。

