# Week 4 统计稳健性

主方向在本样本里对单场景删除和 bootstrap 种子是稳健的。Claim 对 Direct 的 authority 与 semantic 配对差都是 −0.4400，95% 区间都是 [−0.6400, −0.2400]。去掉任意一个 S01–S18 之后，点估计仍为负，区间仍不含 0，没有场景被标成「主结论依赖该场景」。五个种子上，区间两端的极差都是 0.0000，没有端点移动超过 0.05。

这只说明当前 25 个主比较簇。它不把方向推广到另一套场景混合。第二生成路线的结论是 INCONCLUSIVE，主方向没有跨过生成程序这一关。论文应写成机制条件化的回放结果，不能写成 agent-general。

## 样本、过滤与配对

正式联合表是 `week3/week3_replay_joint.csv`，108 行，队列都是 `WEEK3_FORMAL`。统计单位是提案-场景簇（`cluster_key` = `proposal_id` + `scenario_id`）。格子留在簇内，bootstrap 重抽簇，不用 Wilson。

主比较过滤以该表的 `in_main_comparison` 为准，与 `week3/statistical_plan.md` 一致。S06 的 `mechanism_role` 是 `NON_DISCRIMINATIVE`，S09 是 `MECHANISM_SPECIFIC_DIAGNOSTIC`，这两行的 `in_main_comparison` 为 false。S01–S05、S07、S08、S10–S18 为 true。主比较因此是 25 个簇。S06 与 S09 仍各有一行，标记为排除，不进入 −0.44 的分母。

预注册比较只有三对：Claim 对 Direct，Claim 对 Admission，Claim 对 Outbox。簇内配对差是 I(Claim) − I(对照)。`authority` 仅在 `authority_decision == STALE_ACCEPTED` 时为 1。`semantic` 仅在 `semantic_validity == INVALID` 时为 1。其余记录值，包括 `UNKNOWN`，记为 0，并留在分母里。点估计是主比较簇上的平均配对差。区间是簇 bootstrap 的 95% 百分位区间，`B = 4000`，与 `week3/contract.py` 的 `BOOTSTRAP_DRAWS` 相同。下标是 `floor(0.025 × (B − 1))` 与 `floor(0.975 × (B − 1))`。每次比较都从给定种子重新开始。种子 20261120 复现了 Week 3 已发表的 −0.4400 与 [−0.6400, −0.2400]。Claim 侧不一致对 b = 0，对照侧不一致对 c = 11。

## 哪些场景构成 −0.44

−0.44 = −11 / 25。11 个不一致簇全部是对照为 1、Claim 为 0，方向是 `control_only_negative`。Claim 侧不一致为 0。authority 与 semantic 的合计相同，承载它们的簇并不相同。

| 场景 | authority 贡献 | semantic 贡献 | 不一致簇 |
| --- | --- | --- | --- |
| S01 | −2 | −2 | p01:S01，p02:S01 |
| S02 | −1 | −1 | p03:S02 |
| S04 | −1 | 0 | p06:S04，仅 authority |
| S11 | −1 | −1 | p14:S11 |
| S12 | −2 | −2 | p15:S12，p23:S12 |
| S13 | 0 | −1 | p19:S13，仅 semantic |
| S17 | −2 | −2 | p24:S17，p25:S17 |
| S18 | −2 | −2 | p26:S18，p27:S18 |
| 合计 | −11 | −11 | |

两边共同的 10 个簇是 p01、p02、p03、p14、p15、p23、p24、p25、p26、p27。p06 只进入 authority。p19 只进入 semantic。

配对差为 0、因而对分子没有贡献的主比较场景：

- authority：S03、S05、S07、S08、S10、S13、S14、S15、S16。
- semantic：S03、S04、S05、S07、S08、S10、S14、S15、S16。

两边分子都为 0 的是 S03、S05、S07、S08、S10、S14、S15、S16。S03 有 5 个簇：authority 双方都为 0，semantic 双方都为 1，配对差仍是 0。贡献为零指的是配对差，包括双方同为 1。

S06 与 S09 不在主比较里。S06 对 Direct 的 authority 与 semantic 都是双方为 1，配对差为 0。S09 对 Direct 的这两个指标都是双方为 0，配对差为 0。它们不进入 −0.44。

Claim 对 Admission 的不一致对也在 `week4/discordant_by_scenario.csv`，这里只把位置记下来，不另做检验。authority 的对照侧不一致是 S17 的 p24、p25 与 S18 的 p26、p27，合计 −4，对应已发表的 −0.1600。semantic 再加 S13 的 p19，合计 −5，对应已发表的 −0.2000。Claim 侧不一致为 0。

Claim 对 Outbox 在 S01–S18 的 authority、semantic、successor、outdated 上不一致对数都是 0，包括被排除的 S06 与 S09。四个指标的主比较配对差都是 0。全零表示这 25 个簇里没有不一致对。写入本报告时 `week4/independence_decision.json` 的 `decision` 为 `PARTIALLY_SHARED`。该审计写明：25 对主比较格子按同一 proposal 对齐，计分走共享运行器、共享接收端和共享恢复入口；ByteClaim 与 Outbox 的持久行写入不是同一个函数。零配对差不是两种机制的独立等价，也不是 Claim 优于 Outbox。S17 与 S18 在该文件中的解释是 `SHARED_ONE_PATH`：同一条 durable claim 路径不发布旧字节。

## 留一场景

每次去掉一个场景的全部主比较簇，用种子 20261120、4000 次簇重抽，重算 Claim 对 Direct 的 authority 与 semantic。完整结果在 `week4/leave_one_scenario_out.csv`。

没有任何一行的符号变成非负，也没有任何一行的区间覆盖 0。`depends_on_scenario` 全部为 false，`mark` 为空。S06 与 S09 本来就不在主比较里，去掉它们以后估计仍是 −0.4400，区间仍是 [−0.6400, −0.2400]，标记为 `excluded_already`。

离 0 最近的点估计是 −0.3913，出现在去掉 S01、S12、S17 或 S18 的时候，这些场景各带走 2 个不一致簇，剩下 23 个簇、分子 −9。离 0 最近的区间上界是 −0.2083，出现在去掉 S02（两个指标）、S04 的 authority、S11（两个指标）或 S13 的 semantic 时。上界仍在 0 下方。

去掉只含零配对差的场景会把分母变小、估计的绝对值变大。去掉 S03 的 5 个簇之后，估计变为 −0.5500，区间为 [−0.7500, −0.3500]。

留一之后区间仍为负，只描述这 25 个簇里没有任何一个场景单独把区间推过 0。这不是对任意场景混合都成立的普遍结论。

## Bootstrap 种子

只对主比较的 Claim 对 Direct、authority 与 semantic 做了种子检查。种子是 20261120、20261121、20261122、20261123、20261124。每个种子、每个指标都从该种子重新开始，重抽 4000 次。结果在 `week4/bootstrap_seed_stability.csv`。

五个种子给出的估计都是 −0.4400，区间都是 [−0.6400, −0.2400]。authority 与 semantic 的下界极差、上界极差都是 0.0000。`bound_shift_exceeds_0_05` 为 false。种子稳定性旗标没有触发。

## 外部效度

第二提案生成路线只做生成健全性，没有重跑 Direct、Admission、Claim、Outbox。结论是 INCONCLUSIVE，详见 `week4/second_model_sanity.md`。四个结构分布都没有越过预先写下的实质差异阈值，仓库里也没有可在本地运行的第二提案模型。因此主方向对生成路线没有得到独立确认。论文限于机制条件化的回放结果，不能写成 agent-general。

## 归档

`docs/开发文档/week1-historical-audit/archived_joint.csv` 的 SHA256 为 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`，与 Week 3 要求的值一致。计算过程中 `week2/` 与 `week3/` 的文件树散列在写入 `week4/` 前后相同。本分析没有改写这两个目录，也没有改写归档文件。
