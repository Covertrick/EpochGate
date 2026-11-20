# 投稿去向

本稿不能投。下面判断的是现有证据实际靠近哪一类读者，以及那位读者会为什么拒稿。不因为某个名称出现在 CCF 列表上就推荐它。

## 实际贡献落在哪里

现有材料是一次受控故障注入和簇配对测量。对象是同一运行器上的 Direct、Admission、ByteClaim 和 same-DB outbox，接收端是本地文件与回执，崩溃是 Windows `TerminateProcess`。统计单位是 25 个簇、100 个格子。semantic 的主区间依赖 `UNKNOWN_AS_ZERO`。Claim 与 Outbox 由 `independence_decision.json` 定为 `PARTIALLY_SHARED`。第二生成器没有产生第二套四路径结果。

这靠近软件测试和软件可靠性里的实证测量：预先固定故障点，分开报告未知，并检查结论是否随编码改变。它不是分布式系统机制论文，因为没有跨库原子性，也没有任意 HTTP 恰好一次；outbox 的再次发送是 Richardson 模式页已经写明的至少一次中继。它不是形式化方法论文，因为 `week5/formal` 不存在，正文也没有定理。它不是新的智能体基础设施论文，因为证据、权限、未知和提交时状态检查已经出现在 Xu（arXiv:2611.23353）、Marla 与 Bhatnagar（arXiv:2610.31916）、Matoi（arXiv:2610.21394）和 AID-Guard（arXiv:2608.21159）。

## 唯一值得对审稿人说明理由的 C 类会议

ICST，IEEE International Conference on Software Testing, Verification and Validation。CCF 分领域页面列出该会议全称：<https://www.ccf.org.cn/Academic_Evaluation/TCSE_SS_PDL/>。2022 年目录的公开转载把它放在软件工程、系统软件与程序设计语言的 C 类。本次没有核对 2022 年之后是否发行了新目录，因此“现在仍是 C 类”以该公开页面和 2022 年转载为准。

ICST 2027 研究轨征稿已核对：<https://conf.researchr.org/track/icst-2027/icst-2027-research-papers>。征稿写明范围包含 software reliability、replications and empirical studies、testing AI-based systems。全文截止 2026-11-02（AoE），初次通知 2026-12-22。以审查日 2026-11-20 计算，研究轨全文截止已经过去。征稿同时要求工作推进现状；复制和实证论文可以投，但必须是新的、原始的研究。工业影响更大的稿件被指向工业轨，本次没有核对工业轨是否仍开放。

一位 ICST 审稿人可能看上的部分，只有这几件已经写明的测量习惯：故障点预先固定，authority、semantic、successor、outdated 分开，UNKNOWN 不从分母里删掉，并用第二种编码检查 semantic 区间是否还整体低于 0。征稿里的“测试人工智能系统”和“软件可靠性”对得上这个外壳。

主要不匹配也在这里。研究轨要的是推进测试或可靠性知识的证据。本稿若把 Claim 与 Outbox 写成两种机制，与 `PARTIALLY_SHARED` 冲突。若把 semantic −0.440000 写成稳健效应，与 `UNKNOWN_AS_FAILURE` 区间跨 0 冲突。若把 RSR 写成版本围栏，与“不消除 TOCTOU”冲突。若把对象写成智能体，与确定性 `make_world` 和 INCONCLUSIVE 的格点生成器冲突。收成负结果之后，Xu 已经用每平台 7513 次试验报告了“幂等不是恢复”，并且同样声明进程终止不是掉电。ICST 审稿人没有理由把这篇更小的本地测量当成该问题的第一篇实证论文。

因此 ICST 只说明读者群落，不构成投稿建议。

## 核过、但不推荐的邻近征稿

EDCC 2027 会议页写明地点是 Trondheim，会期 2027-04-06 至 2027-04-09：<https://edcc2027.idi.ntnu.no/>。HotCRP 截止日期页面写明研究轨提交截止 2026-10-28，状态为已经过去：<https://edcc2027.hotcrp.com/deadlines>。会议新闻在 2026-11-18 仍列出 fast abstracts、学生论坛和海报。CCF 等级本次未核对，不把 EDCC 称为 C 类。依赖性研究轨审稿人会要求崩溃模型覆盖所声称的故障；本稿是 `TerminateProcess`。快摘要不能装下这篇未建立新颖性的机制叙述。不推荐。

PRDC 2027 的征稿本次没有找到，记为 unverified。第三方页面记录 PRDC 2026 的全文截止为 2026-09-04，会期 2026-12-02 至 2026-12-04，并把它标成 CORE C，不是 CCF 目录核对结果：<https://www.conferences-computer.science/prdc/2026/>。不推荐。

ISSRE 在同一份 2022 年 CCF 转载里位于 B 类，不是这里要提名的 C 类。ISSRE 2027 征稿本次未核对，记为 unverified。不推荐。

分布式系统会议会把 RIFL、Beldi、Durable Functions、outbox 和 RFC 9110 当作背景，然后发现本仓库没有跨库原子性，Claim 与 Outbox 又共享恢复入口。形式化会议会发现没有证明对象。这两类都比 ICST 更不匹配。

## 结论

不投稿。最近的读者是软件测试与可靠性的实证审稿人。主要不匹配是：恢复时分开证据、权限和语义，以及按期望版本在目标上原子发布，已经由 Matoi、*Permission to Retry* 和 AID-Guard 公开；本仓库的 RSR 不关闭 TOCTOU；Claim 与 Outbox 是 `PARTIALLY_SHARED`；semantic 主区间依赖 `UNKNOWN_AS_ZERO`。ICST 2027 研究轨的范围碰得到这个外壳，全文截止已经过去，而且现有结果没有达到该轨对原创实证工作的要求。
