# 新颖性评估

审查日期 2026-11-20。立场是攻击 EpochGate 的新颖性。材料是 `week4/independence_decision.json`、`week4/statistical_sensitivity.md`、`week4/crash_evidence_extended.md`、`week4/rsr_ablation.md`、`week3/week3_report.md`，以及 `week4/second_model_sanity.md` 里已经写下的第二生成器结论。本次工作区里没有 `week5/formal` 或 `week5/semantic`，因此没有任何形式化证明或额外语义定理可以算进贡献。

结论：**NOVELTY_NOT_ESTABLISHED**。

三篇指定标题都找到了正文，都不是 NOT_FOUND。Song Xu，*Idempotency Is Not Recovery: A Fault-Injection Study of Language-Model Agent Tool Actions*，arXiv:2611.23353，预印本、未同行评审，提交信息见 2026-11-17 的公开页。Deekshith Marla 与 Kushagra Bhatnagar，*Permission to Retry: A Study of Authorization and Recovery in AI Agent Workflows*，arXiv:2610.31916，2026-10-22。Artem Trofimov 与 Boris Novikov，*When Tool Calls Succeed but Workflows Fail: Anomalies at the Agent–Tool Boundary*，arXiv:2609.15397，正文使用 2026-07-27 的 MCP 快照。下面把 Xu 2026 当作与本仓库实验最强的一篇重叠；Matoi 与 AID-Guard 分别占住三轴措辞和版本条件提交。预印本不是同行评审结论，但已经公开，足以阻止“我们首先提出”这一句。

## 1. 恢复时分开证据、权限和语义，是否已有直接先例

有。这一句必须删掉。

Zexun Wang 的 Matoi（arXiv:2610.21394，2026-10-15）摘要的第一件事就是把可复用的后果证据、当前执行权限和耐久发生史分开。恢复重新检查证据，不确定的预约继续保留。论文把拒绝、需要复核和派发后超时造成的未知写成三种不能合并的结果。恢复事务比较期望的所有者代际，并在同一事务里推进它。

Marla 与 Bhatnagar 把恢复写成四项义务：检查点状态、现在是否还有派发许可、外部效应的证据、是否有权对账。每宿主 42 条历史里，留存账本对账得到 21 次检查后完成、15 次拒绝和 6 次未知且预约仍在。未知被保留，不写成完成。提案是脚本，不是模型输出。

Xu 2026 把派发前授权、运行时账本和目标侧证据分开。对账启用时，恢复区分已观察到的效应、观察到的无效应和未知；未知保持未解决。去掉对账之后，已知的效应后崩溃全部含糊（100/100），同时没有观察到重复。失败关闭挡住了注入的未授权动作（F1 上 B1–B3 为 0/100，失败开放为 100/100）。

本仓库的证据状态机、`authority_decision` 和 `semantic_validity` 是这组区分在本地 SQLite 与文件接收端上的一种编码。它没有先于上述公开文本。

## 2. 恢复时的语义再验证是否已经存在

已经存在，而且有的做法比本仓库更强。恢复时语义再验证不能写成新贡献。

AID-Guard（Yingzhe Tong、Leyu Dai、Songhui Guo，arXiv:2608.21159）在提交边界 D2 重载规范请求和当前提供者状态。授权依赖可变资源时，提供者必须执行被批准的 version、ETag 或 lock 前置条件。原子合同把领取、提供者变更和结果记录放进同一事务。受控幂等合同先耐久化提交尝试和不可变 outbox，再按精确范围和请求体绑定幂等键。

Matoi 在每个被覆盖效应的提交边界检查适用的状态或权限代际，并原子地执行范围、随机数和剩余预算。本地恢复把期望的所有者代际和证据恢复放在同一事务。观察不再适用时，旧证据不能继续授权执行。

Xu 的 OP3 在目标上检查先验状态。第一次迁移成功后，同样的后续请求因前置条件被拒绝。这是目标侧的当前状态检查，不是调用方在发布之后补打的分数。

本仓库的 RSR 只在 `ByteClaim.commit` 被 `TerminateProcess` 打断之后、`SharedReceiver.accept` 之前调用 `semantics_oracle`。`INVALID` 记为 `SEMANTICALLY_SUPERSEDED` 并且不发布，`UNKNOWN` 不发布。S14 与 S16 各 10 次上，无效恢复从 10/10 降到 0/10，`SEMANTICALLY_SUPERSEDED` 从 0 升到 10。40 次预检全部是 `INVALID`，`VALID` 分支没有被这组运行走到。作者已经写明预检和发布不是同一次原子提交，TOCTOU 仍在。这是一次局部、单分支、非原子的预检，落在 AID-Guard 和 Matoi 已经公开的提交时重检之后。

## 3. 目标上按期望世界版本做原子条件发布，是否已经存在

已经存在。本仓库自己没有这项机制，所以也不能把它算成贡献。

RFC 9110 第 13.1.1 节规定 `If-Match`：源服务器必须在执行状态改变方法之前用强比较检查目标当前表示的实体标签；条件为假则不得执行该方法。这就是按期望版本在目标上决定是否发布。

AID-Guard 的 D2 把同一要求放进提供者合同：version、ETag 或 lock。Matoi 在恢复事务内比较期望的所有者代际。Xu 的 OP3 用先验状态拒绝已经迁移之后的重复请求。RIFL 解决的是另一件事，即耐久保存已完成 RPC 的结果，使重试不再执行；它不检查业务世界版本，但说明“至少一次”与“恰好一次”的差别在 2015 年已经是存储系统论文的对象。

Week 4 写明 RSR 的 `VALID` 只覆盖调用预言的那一刻。预言返回之后、写入效果文件之前，世界仍可能变化。S14 与 S16 没有测量这个窗口。

## 4. UNKNOWN 与 INVALID 是新贡献，还是普通设计

普通设计。必须删掉“UNKNOWN 与 INVALID 的区分是贡献”。

Xu 把未知、未授权效应、重复效应、丢失效应和孤立效应分成不同结果，并写明未知观察保持未知。Permission to Retry 把拒绝和未知预约分开计数。RetryBench（You Liu、Yue Liu、Quanchao Lu，arXiv:2610.15884）把重试许可、已结算效应和完成分开，因为只数成功会奖励碰巧安全的重试，只数重复会奖励永远不恢复。Matoi 要求返回已提交、拒绝或未知，并且缺少当前权限不能用一次缓存命中补上。Trofimov 与 Novikov 把 unknown 定义成运行时没有权威结果，同时世界中的结果可以已经确定。

本仓库的敏感性说明这个区分会改变结论，而不是创造了一个新性质。主口径 `UNKNOWN_AS_ZERO` 下，Claim 相对 Direct 的 semantic 配对差是 −0.440000，区间 [−0.640000, −0.240000]。`UNKNOWN_AS_FAILURE` 变成 −0.040000，区间 [−0.160000, 0.080000]，跨过 0。`UNKNOWN_EXCLUDED` 变成 −0.142857，区间上界是 0，簇数从 25 降到 14。authority 的 −0.440000 在三种政策下不变，因为主比较里 authority 的 UNKNOWN 格数是 0。semantic 上“区间整体低于 0”依赖把 UNKNOWN 记为 0。

## 5. 相对已有的智能体恢复工作，本项目多测到了什么

没有一项可以写成已有工作之外的新性质。

可以保留的只是对本样本的描述，而且每一条都带有已写下的限制。

- Claim 相对 Direct 的 authority 配对差在三种 UNKNOWN 政策下都是 −0.440000，区间不跨 0。去掉 S17 与 S18 之后是 −0.333333，区间仍低于 0。Xu 的 F1 已经测量“派发前拒绝无效授权则未授权效应为 0”。本仓库这条差使用共享运行器，对照的是 Direct，不是一个独立实现的授权协议。
- semantic 的 −0.440000 只在 `UNKNOWN_AS_ZERO` 下使区间整体低于 0。这是编码敏感性，不是新的语义保证。
- RSR 在 S14 与 S16 把无效恢复从 10/10 降到 0/10，代价是 `SEMANTICALLY_SUPERSEDED` 升到 10/10。TOCTOU 未消除，`VALID` 与 `UNKNOWN` 分支没有被这组运行测量。
- Claim 相对 Outbox 在四个指标、三种 UNKNOWN 政策下的配对差都是 0，不一致簇数是 0。独立性审计把这个 0 限制为同一样本上的描述。

Xu 已经用更大的故障注入报告：完整配置在 200 个已知崩溃重放对上重复效应为 0/200，较简单基线为 117/200；去掉对账则结果含糊。本仓库的矩阵是 `TerminateProcess`、本地接收端、预先固定的重复次数，不是失败概率，也不是掉电。

## 6. Claim 与 Outbox 的实现差异够不够做机制对比

不够。`week4/independence_decision.json` 的决定是 `PARTIALLY_SHARED`。本次没有读到推翻这个决定的代码结论；该文件自己已经列出两边不共享的写入函数和表。

共享的部分包括连接与模式、权威读取、故障控制器、冻结提案运行器的执行和恢复入口、记录型接收端、`week3.execute` 的格子运行，以及杀死子进程和核对的路径。Claim 独有 `ByteClaim` 和 `byte_claims`。Outbox 独有 `Outbox`、`business_effects`、`outbox` 和 `RETRY_LIMIT=3`。25 个主比较簇的科学输入按同一提案对齐，计分走共享运行器、共享接收端和共享恢复入口。配对差为 0 只说明这次已计分结果一致。它不是两种机制的独立等价，也不是 Claim 优于 Outbox。S17 与 S18 是 `SHARED_ONE_PATH`：两条路径名写入同一冻结分支，发现只能写成耐久 claim 路径不发布旧字节。

机制对比这一主张必须删掉。

## 7. 第二生成器有没有改变实验分布

没有。它没有完成四路径回放，因此不存在另一条已计分的实验分布。

`week4/second_model_sanity.md` 的结论是 INCONCLUSIVE。替代程序是确定性的 `coprime_stride_lattice_v1`，不是语言模型，也不调用 `make_world`。它产生 18 条记录，对应 S01–S18，没有重跑 Direct、Admission、Claim、Outbox。四个预先指定的结构分布都没有越过实质差异阈值：语义类总变差 0，幂等键唯一率之差 0，载荷长度相对差 0.0390，oracle 可裁定率之差 0。sku 与 dest 的别名率从 1 降到 0.3333，但路径没有重跑，这个别名差不能改写配对差，规则也没有把它抬成 DIFFERS。

−0.440000 只属于已经冻结的第一批提案。论文不能写成 agent-general，也不能写成第二生成器确认或改变了主结果。第一批提案同样来自确定性的 `make_world`，这一点只加强“没有第二套已计分分布”，不构成额外贡献。

## 必须删掉或收窄的主张

下列句子没有剩余的新颖性可供收窄后保留。

| 主张 | 处理 | 依据 |
| --- | --- | --- |
| 首次在恢复时分开证据、权限和语义 | 删掉 | Matoi 的三轴；Permission to Retry 的四项义务；Xu 的授权、目标证据和未知 |
| 恢复时语义再验证是新机制 | 删掉 | AID-Guard D2；Matoi 的代际检查；Xu OP3。本仓库 RSR 非原子，且 S14/S16 只走到 INVALID |
| 目标按期望世界版本原子发布 | 删掉 | RFC 9110 `If-Match`；AID-Guard 的 version、ETag 或 lock。本仓库写明不消除 TOCTOU |
| UNKNOWN 与 INVALID 的区分是贡献 | 删掉 | 上述论文都把未知与拒绝、失败或重复分开。本仓库 semantic 区间依赖 `UNKNOWN_AS_ZERO` |
| Claim 与 Outbox 的机制对比、等价或优劣 | 删掉 | `PARTIALLY_SHARED`；配对差 0 只是描述 |
| 第二生成器改变了分布或支持 agent-general | 删掉 | INCONCLUSIVE；确定性格点；没有四路径回放 |
| 任意 HTTP 恰好一次、跨库原子、掉电耐久 | 维持删除 | Week 3 与 Week 4 已写明。崩溃是 `TerminateProcess`，`power_loss_claim` 为 false |

收窄之后还剩下的文字，只是一次单机、共享运行器上的描述性测量。这不足以建立新颖性。
