# Week 3 统计计划

本文件在查看 Week 3 正式运行结果之前冻结。后文的比较只使用这里登记的指标、比较和区间。没有写在这里的对比都是探索性描述，不参与决定。

## 单位

主单位是提案-场景簇（`proposal_id` + `scenario_id`）。格子是簇内的路径观测，不是独立样本。Bootstrap 重抽的是簇，不是格子。

Week 2 的 60 个 `PILOT_FIRST_BATCH` 格子不进入本周正式样本，只按该标签引用。Week 1 归档的 52 行不用来填本周缺口。

## 正式样本

- 队列标签：`WEEK3_FORMAL`。
- 每条路径每个簇一次主运行。B-1 的崩溃重复只留在 `crash_matrix.csv`，不并入主比较。
- 主比较排除 S06 与 S09。S06 的机制角色是 `NON_DISCRIMINATIVE`。S09 的机制角色是 `MECHANISM_SPECIFIC_DIAGNOSTIC`。
- `UNKNOWN`、`BLOCKED`、未观测格子留在分母里。不把它们记成失败，也不从分母删除。
- `NOT_APPLICABLE` 不是失败。`CORRECT_STOP` 不是后继失败。`CONFLICT` 不是 `UNKNOWN` 与 `VALID` 之间的有序中点。`RESTORED_BYTES` 不是 `CONFIRMED_EFFECT`。`OUTDATED` 不是 `STALE_AUTHORITY`。

## 四个分开的二元指标

这些指标不合成一个 EFE。一个格子可以同时进入多个分子；禁止把四个比率相加后当成总失败率。

对主比较里的每个格子，指标只在命中下列条件时为 1，其余记录值包括 `UNKNOWN` 都为 0：

1. `authority`：`authority_decision == STALE_ACCEPTED`。
2. `semantic`：`semantic_validity == INVALID`。
3. `successor`：`successor_status` 为 `FAILED` 或 `NOT_COMPLETED`。`CORRECT_STOP`、`COMPLETED`、`COMPLETED_VALID`、`NOT_APPLICABLE` 都不是 1。
4. `outdated`：`semantic_validity == INVALID`，并且 `authority_decision == NO_STALE_ACCEPTANCE_RECORDED`，并且物理上能看见字节（`CONFIRMED_EFFECT`、`NOT_LEGAL`、`PRESENT_UNVERIFIED` 或 `RESTORED_BYTES`）。权威已经过期的格子不算 outdated。

每个指标同时报告分子、分母和 `UNKNOWN` 计数。分母是主比较的全部格子。

## 预注册比较

只做三对：Claim 对 Direct，Claim 对 Admission，Claim 对 Outbox。每对都计算上面四个指标。其余路径对都是探索性的。

簇内配对差是 `I(Claim) - I(对照)`。点估计是主比较簇上的平均配对差。正值表示 Claim 更常出现该指标。

区间是簇 bootstrap 的 95% 百分位区间。`B = 4000`。种子 `20261120`。每次比较都从该种子重新开始。排序后取下标 `floor(0.025 * (B - 1))` 与 `floor(0.975 * (B - 1))`。

McNemar 精确双侧检验只作为补充，使用不一致对 `b`（仅 Claim 为 1）和 `c`（仅对照为 1），零假设是 `Bin(b + c, 0.5)`。它不决定门，也不额外增加比较。

## 不在本计划里的说法

- 不发布单一复合 EFE 作为主要结果。
- 不把格子当成独立样本，不做逐对搜索。
- 不把进程崩溃注入说成掉电，不把 Week 2 的进程内故障说成操作系统级结束。
- 不声称 Outbox 普遍更优，不声称任意 HTTP 恰好一次，不声称跨库原子性。
