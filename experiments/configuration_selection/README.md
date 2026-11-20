# 配置选择先导实验

**G1 通过。G2 对原主张为 No-Go。** 18 格验收和 96 格先导均从封存证据重算。送达时 S1 推荐 Admission，完整历史评价 S2 输出无可推荐配置。改变来自 Claim 与 Admission 共同遗漏的后继义务，未发现支持改选 Claim 的证据。按执行前标准停止确认批，也保留未使用的留出验证。

| 通知分配 | S0 终态 | S1 加授权 | S2 加语义 | S2 加后继 | S2 完整历史 |
| --- | --- | --- | --- | --- | --- |
| 送达 | Admission | Admission | Admission | 无可推荐 | 无可推荐 |
| 未送达 | Direct、Admission 并列 | 无可推荐 | 无可推荐 | 无可推荐 | 无可推荐 |

这是固定夹具、既定零观察违规和全任务完成门槛下的推荐。无可推荐表示每个配置都有实测不合格项。S1 到 S2 的变化是撤回推荐，属于历史判定改变合格性，没有形成配置之间的严格翻转。

| 分配 | 路径 | 终态失败 | 授权违规 | 语义违规 | 后继遗漏 | S1 通过而 S2 失败 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 送达 | Direct | 0 | 2 | 5 | 0 | 5 |
| 送达 | Admission | 0 | 0 | 0 | 3 | 3 |
| 送达 | Claim | 0 | 0 | 0 | 3 | 3 |
| 未送达 | Direct | 0 | 2 | 5 | 0 | 5 |
| 未送达 | Admission | 0 | 2 | 5 | 0 | 5 |
| 未送达 | Claim | 0 | 2 | 5 | 0 | 5 |

各行分母为 16 个配对实例。Claim 对 Admission 在每个通知条件都是 0 个不一致对，主指标配对差和通知交互的观测值均为 0。这个结果不证明等效。送达时 Admission 与 Claim 各提交 18 次，Claim 另有 18 条持久 Claim 记录。成本结论限于这些计数。

## 看到的差异与没有识别的差异

- 目标记录确认，业务状态失效和真正撤销是两类变化。送达后两种受保护配置在共同检查点拒绝了旧请求，Direct 仍落地。
- 后继义务只在原预留被拒后适用。先导给三个场景明示了额外确认义务，三条既有路径均无此应用行为。送达时 Admission 和 Claim 写成了正确的拒绝处置记录，但未完成额外确认。这是事前设定的诊断控制，不能当成真实部署曾发生的缺陷。
- Admission 和 Claim 在本实验的通知检查点读取同一份局部授权状态，目标使用相同的接收器。Claim 有额外耐久状态，其恢复、确认后竞争和跨事务行为没有进入本轮。相同结果有实现上的解释，扩大同一矩阵不提供新的区分能力。
- 终态定义为正确记录订单处置，允许正确拒绝。它没有把「预留成功」作为全部任务的共同终点。现场使用含义需另找部署策略和任务义务依据。
- 使用确定性合成输入，16 个实例复用五个场景层。未执行正式确认、留出、Docker 清洁环境验收或外部投稿。完整长文与录取均没有得到本轮证据支持。

## 复现

从 `experiments/` 运行。审计模式核对每个归档 SHA-256，再读取 SQLite 目标账本、Claim 账本以及输入与时序记录重算。

```powershell
../.venv/Scripts/python.exe -m configuration_selection.run audit acceptance ../results/experiments/configuration-selection/acceptance-01
../.venv/Scripts/python.exe -m configuration_selection.run audit pilot ../results/experiments/configuration-selection/pilot-01
```

重跑使用新路径。先运行 `run acceptance <新路径>`，再运行 `run pilot <新路径> --acceptance <已验收路径>`。现有运行时机制测试通过 11 项。

[执行前协议](PROTOCOL.md) · [六格验收与控制结果](../../results/experiments/configuration-selection/acceptance-01/report.json) · [先导逐格结果](../../results/experiments/configuration-selection/pilot-01/report.json) · [研究计划](../../docs/实验/EpochGate配置选择实证研究计划.md)
