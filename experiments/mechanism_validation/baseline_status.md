# Week 5 冻结基线

这是 Week 5 开始前的冻结基线和主张账本。总门是 NOT_READY。这里没有最终验收，也没有 READY_FOR_SUBMISSION。

## 冻结了什么

Git 提交是 `b6dd92b491f5235b89a649132e63a889df598316`。`week2/`、`week3/`、`week4/` 和 Week 1 审计目录都不在这个提交里，工作区把它们显示为未跟踪。`week5/` 在写入前不存在，现在也未跟踪。已跟踪文件里原先就有 `results/runtime/correctness/after/e4/cordis.patch.yml` 的改动，本基线没有动它。

`docs/开发文档/week1-historical-audit/archived_joint.csv` 的 SHA256 在写入本目录之前和之后都是 `d4721a05d96fe19aec26bc3431a1b6551e516cf52f89f5d0c822e55a5b092cda`，与 Week 3、Week 4 记录的值一致。归档文件 76 行：72 行是确认批 m4-confirm，其中 52 行 `joint_observed` 为真；另外 4 行是 dedicated。EFE 是 28/52，EFE@success 是 6/16。Week 1 决定仍是 CONDITIONAL GO。审计当时没有可读的会议 PDF。工作区里现在有一个未跟踪的 `docs/EpochGate-conference.pdf`，大小 216057 字节；它没有进入本账本，不能用来声称 Figure 2 已经核对。

从文件重新数过的样本是：Week 2 试点 15 个提案、60 个格子、60 个 `run_id`，另有 12 次冒烟；主比较去掉 S09 后是 56 格。`EFE_authority@success` 是 1/26，不是复合 EFE。Week 3 正式运行是 27 个簇、108 个格子、108 个 `formal_runs` 目录；主比较是 25 个簇、100 个格子。Claim 对 Direct 的 authority 和 semantic 在 UNKNOWN_AS_ZERO 下都是 -0.44，区间 [-0.64, -0.24]。semantic 在把 UNKNOWN 当作失败时区间跨过 0。独立性决定是 PARTIALLY_SHARED。S17/S18 是一条 durable claim 冻结路径。Week 3 崩溃矩阵 80 行，`process_kill` 全为 true，`power_loss_claim` 全为 false，结束调用是 `week3/kill_runner.py` 里的 TerminateProcess。Week 4 扩展矩阵 60 行，外加 RSR 消融 40 行，`power_loss_claim` 都是 false。第二生成器 18 条提案的 `external_llm_called` 都是 false，路径没有重跑，结论是 INCONCLUSIVE。

## 哈希方法

目录树使用 Week 4 独立性审计里已经对上的算法。对一个或按顺序排列的多个目录，取 `sorted(path.rglob("*"))`，跳过非文件。相对路径是相对工作区根目录的 POSIX 形式。每个文件先对原始字节计算 SHA256 十六进制，再把路径的 UTF-8、一个 NUL、摘要的 ASCII 和一个 LF 写入同一个 SHA256。`__pycache__` 和 `.pyc` 包含在内。

按这个方法，`week2/` 是 `109cb0598ce1e6dcd7eb8e367e3c9b528906f20dc698fa37305e843acd97dc22`，383 个文件，4013023 字节。`week3/` 是 `68a38ef694081256ecf1c688eb3e1ca17fd79f69c19bdba971b041ea65277cd8`，1403 个文件，5753281 字节。`week4/` 是 `28df3e1329440c9dc063dd58ea8ed59a12d9e23bbabbe9cac291e62e0933636c`，805 个文件，3024310 字节。`week2/` 之后紧接 `week3/` 的同一次哈希是 `9c0db4425bf0344ca61a16f5ec718e8862bdd103122463005e5eafd07662332f`，1786 个文件，9766304 字节，与 `week4/independence_decision.json` 里的 `week2_week3_tree_sha256_before_week4` 相同。这三个目录在 git 里没有树对象，所以用内容哈希核对。

## 不得越过的说法

主张账本里的 FORBIDDEN 行是论文上限。不得写：Claim 优于 Outbox；Claim 与 Outbox 独立等价；结果是 agent-general；崩溃实验证明掉电耐久性；任意 HTTP 恰好一次；跨库原子性；复合 EFE 是 Week 2 或 Week 3 的主结果；全批 72 格上的 EFE@success；Figure 2 已按会议 PDF 核对。

可以保留的窄说法也有边界。52/72 只覆盖有联合观察的确认批。1/26 只是成功门上的过期凭证接纳。-0.44 只在 UNKNOWN_AS_ZERO 和这 25 个簇上整体低于 0；semantic 对 UNKNOWN 的处理不稳健。零配对差只表示这 25 个簇没有不一致对。S17/S18 写成同一条 durable claim 路径不发布旧字节。

版本围栏、第二生成器正式回放和提交就绪在账本里是 PENDING。没有这些实验的记录，本基线不补做，也不把健全性对照改写成正式回放。

## 这不是最终验收报告

本目录只有基线清单、主张账本、总门和这份状态说明。没有 `final_acceptance_report.md`。G1 到 G5 都是 PENDING，总门是 NOT_READY。新颖性审查、形式模型、分离实现、新实验、独立统计、论文和评审都没有完成，P0 项仍然开放。
