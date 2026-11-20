# 第二提案生成路线

结论：**INCONCLUSIVE**

四个预先指定的结构分布都没有越过实质差异阈值，同时仓库里没有可在本地运行的第二提案模型。按事先定下的规则，结论停在 INCONCLUSIVE。论文限于机制条件化的回放结果，不能写成 agent-general。本次没有重跑 Direct、Admission、Claim、Outbox。

## 第一生成器

Week 2 与 Week 3 的冻结提案来自 `week2/freeze_artifacts.py` 的 `make_world(seed, index)`。Week 3 的 `week3/proposals.py` 在 `_build_new` 里复用同一个函数。这不是语言模型。

该函数把 sku 与 dest 都绑在 `index % 3` 上，数量是 `1 + (index % 4)`，订单号是 `ORD-{seed}`，幂等键是 `effect:{proposal_id}`。主载荷由共享谓词 `legal_payload` 从 `world_before` 序列化。第一批是 Week 2 的 p01–p15 与 Week 3 的 p16–p27，共 27 条。磁盘上的载荷散列与两份 `proposal_registry.jsonl` 一致。

## 为什么没有第二模型

提案冻结器本身没有模型客户端。仓库里的 HTTP 客户端指向外部接口 `https://api.deepseek.com`（见 `results/runtime/model_probe.json` 与 `.env.example`），不是本地第二提案模型。本次没有调用该接口，也没有编造模型补全或 API 返回。

替代程序是确定性的 `coprime_stride_lattice_v1`。它不调用 `make_world`，也不调用 `_build_new`。

## 第二生成器

18 条新记录，每条对应 S01–S18 中的一个场景，标识为 w4l01–w4l18，种子 4201–4218。字节在 `week4/proposals/`，登记在 `week4/proposals/second_lattice_registry.jsonl`。没有写入 `week2/proposals` 或 Week 3 的提案目录。

与第一生成器并排的差异：

- sku 取 `i % 3`，dest 取 `(2 * i) % 3`。步长 2 与 3 互素，dest 不再跟 sku 锁在同一个下标上。
- 数量是 `10 + ((5 * i) % 17)`，落在 10–26，与第一生成器的 1–4 分开。
- 订单号是 `LAT-{i:02d}`。后继数量是 `qty + 1`，漂移数量是 `qty + 11`。
- 幂等键是 `lattice:{scenario_id}:{i:02d}`。
- 主载荷仍用共享谓词 `legal_payload` 序列化，oracle 才能对它打分。谓词是两边共用的评分规则，不是第一生成器。
- 场景故障只用来冻结 `world_after`。这一步不是四条路径的运行。

## 四个分布

比较写在 `week4/second_model_proposal_sanity.csv`。实质差异阈值在计算前固定：语义类总变差大于 0.20；幂等键唯一率之差大于 0.05；载荷长度均值的相对差大于 0.25，或两端长度区间不相交；oracle 可裁定率之差大于 0.05。越过任一阈值则结论改为 DIFFERS。键的字面字符串不参与判定，因为两批标识本来就不会重合；判定看的是一批内部是否唯一。

| 轴 | 第一批（27） | 第二批（18） | 差异 | 越过阈值 |
| --- | --- | --- | --- | --- |
| 语义类 | LEGAL_INITIAL 27/27 | LEGAL_INITIAL 18/18 | 总变差 0.0000 | 否 |
| 幂等键 | 27/27 唯一 | 18/18 唯一 | 唯一率之差 0.0000 | 否 |
| 载荷长度 | 最短 25，最长 26，均值 25.6667 | 最短 24，最长 25，均值 24.6667 | 相对差 0.0390，区间重叠 | 否 |
| oracle 可裁定 | 27/27 | 18/18 | 率差 0.0000 | 否 |

可裁定指冻结记录带有谓词版本 `predicate.fulfillment.v1`、世界字段、权威 token/epoch/eligible、非空幂等键，并且 `legal_payload` 能对 `world_before` 求值。这是提案结构能不能交给现有 oracle，不是路径跑完之后的判决。

另有一项没有进入上面四条轴：第一批 27/27 的 sku 与 dest 共用下标，别名率 1.0000；第二批别名率 0.3333。这项记在 CSV 的 `factor_alias` 里。路径没有重跑，别名差不能改写配对差，所以它不把结论抬成 DIFFERS。它也使结论到不了 CONSISTENT：第一生成器的世界表把 sku 和 dest 锁在一起，第二批格子没有在运行时被计分。

## 对论文的限制

结论是 INCONCLUSIVE。−0.44 来自已经冻结的第一批提案在四条路径上的回放。第二路线只检查了提案的结构分布，没有给出另一条生成程序下的配对差。论文应写成机制条件化的回放结果，不能写成 agent-general。
