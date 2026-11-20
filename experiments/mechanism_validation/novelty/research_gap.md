# 研究缺口

本文记录的是攻击性对照之后仍然空着的位置。空着不等于本仓库已经把它做成了贡献。`week5/formal` 与 `week5/semantic` 在本次审查时不存在。

## 已经被公开工作占住的位置

恢复时把证据、当前权限和发生史分开，Matoi（arXiv:2610.21394）和 *Permission to Retry*（arXiv:2610.31916）已经写成协议和有限一致性实验。未知结果必须保持未知，Xu（arXiv:2611.23353）、Marla 与 Bhatnagar、Matoi、RetryBench（arXiv:2610.15884）都这样计数。幂等抑制重复不等于知道动作是否发生，是 Xu 的标题和测量结果：去掉对账后，已知效应后崩溃 100/100 仍含糊，同时重复效应可以是 0。

提交时按当前目标状态决定是否发布，也已经有标准接口和智能体协议。RFC 9110 的 `If-Match` 要求源服务器在执行方法之前做强比较。AID-Guard（arXiv:2608.21159）要求提供者执行 version、ETag 或 lock。Matoi 在恢复事务内比较期望的所有者代际。Xu 的 OP3 用先验状态拒绝后续的同一迁移。事务性 outbox 的原模式页写明中继可能发布多于一次，消费者必须幂等；这不是跨库原子，也不是任意 HTTP 恰好一次。

MCP `2025-06-18` 的 `ToolAnnotations` 把 `idempotentHint` 定义为提示，默认 false，并且客户端不得把不可信服务器的注解当成行为保证。Trofimov 与 Novikov 的普查说明这些提示表达不了权威结果、补偿或交换性。工具合同缺口是接口问题，不是本仓库本地 `effect.bin` 已经填上的问题。

## 本仓库没有填上的位置

1. 目标侧的原子条件发布。RSR 在 `semantics_oracle` 返回之后才发布。S14 与 S16 的 40 次预检全是 `INVALID`，没有测量 `VALID` 返回之后、写入之前世界再变的窗口。Week 4 写明这不消除 TOCTOU。
2. 独立的机制对比。`independence_decision.json` 是 `PARTIALLY_SHARED`。Claim 与 Outbox 的持久行函数不同，运行器、接收端和恢复入口共享。25 个簇上四个指标的配对差都是 0。这个 0 不是等价检验。
3. 不依赖 UNKNOWN 编码的语义效应。semantic 的 −0.440000 只有在 `UNKNOWN_AS_ZERO` 下才使区间整体低于 0。`UNKNOWN_AS_FAILURE` 的区间跨过 0，`UNKNOWN_EXCLUDED` 的上界是 0。
4. 第二套已计分分布。第二生成器是确定性格点 `coprime_stride_lattice_v1`，结论 INCONCLUSIVE，没有重跑四条路径。第一批提案也来自确定性的 `make_world`。主结果不是 agent-general。
5. 崩溃模型以外的故障。已做的是 Windows `TerminateProcess`。`power_loss_claim` 为 false。接收端是本地共享接收器，不是任意 HTTP，也不是两个数据库之间的原子提交。
6. 形式保证。ARIES、Sagas、RIFL、Beldi 和 Durable Functions 分别提供日志恢复、补偿、恰好一次 RPC、无服务器事务和记录重放的语义。本仓库没有对应的证明对象。Durable Functions 的等价证明只说明确定性重放可以符合无故障编排语义，不说明外部世界版本仍有效。

## 若要成为一个还没被占住的问题，测量必须先改变

一个尚未被上述论文直接报出的问题可以表述为：在目标提供版本化条件写的前提下，密封字节、当前授权和当前世界版本这三项在同一次目标侧提交里分别失败时，物理效应、错误发布和未解决预约各是多少。这个问题要求三件本仓库现在没有的东西：目标原子地执行期望版本，Claim 与 Outbox 不再共享恢复入口，以及第二套生成程序真正跑完四条路径。只把现有 RSR 表或 −0.440000 重新命名，填不上这个空位。

Xu 的公开页没有存放可执行的原始试验，*Permission to Retry* 写明此版本没有公开执行仓库。它们仍然是先行陈述。本仓库可以复现自己的本地矩阵，这个可复现性不把先行陈述变成不存在。
