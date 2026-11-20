# Git 工具恢复中的效果归属

先导成立，长文待验证。研究问题是一次 Git 引用更新丢失响应后，客户端能否区分本次发布成功、其他写者达到相同状态，以及本次发布成功后又被撤回。[先导协议](PROTOCOL.md)

## 实测

Git 2.41.0.windows.2 上运行了 24 格，交叉四种历史、三种 reflog 条件和两种标记配置。每组四种历史里，两种发生过目标操作，两种没有发生。

| 客户端可用证据 | 结果 |
| --- | --- |
| 当前分支等于预期提交 | 把其他写者误认作本次操作成功，并漏掉本次操作已发生但随后撤回的历史。24 格里各出现 6 次。 |
| reflog 中相同的前后 OID | 日志保留时仍会把其他写者的同一跃迁误认作本次操作。 |
| 可读 reflog 中匹配操作 ID | 在日志保留的两种标记配置中确认全部 4 个历史成功，误确认 0。日志禁用或过期时无法确认。 |
| 写入同一 ref 事务的操作标记且可查询 | 12 个启用标记的格子中确认全部 6 个已发生效果，误确认 0。其他格返回未知。 |
| 服务端有标记，客户端的 Git 传输视图隐藏该命名空间 | Git ls-remote 看不到标记，无法据此确认历史效果。 |

另外做了两条真实条件重试。客户端在标记隐藏时两次都看到分支指向 A。第一条的原操作从未发出，第二条已 A→B、再被另一写者撤回到 A。同一条以 A 为旧值的条件更新在两条轨迹都成功，第二条重试改变了独立回滚完成后的状态。该回滚是否应保留取决于真实工作流的任务约定。

[24 格原始证据](../../results/experiments/git-effect-evidence/pilot-01/evidence.json) · [重算报告](../../results/experiments/git-effect-evidence/pilot-01/report.json) · [实际重试轨迹](../../results/experiments/git-effect-evidence/pilot-01/retry-report.json)

## 长文验收

[外部工作流资格审查](WORKFLOW_AUDIT.md) 固定检查了 Flux 和 create-pull-request 的实现。两个候选均未通过。普通推送的补充控制已完成，外部复现与宿主测量按协议停止。

先从独立维护的发布或机器人工作流找到明确的回滚语义，用它真实采用的原生审计或操作状态接口作强对照。只有在这些证据的可见性确实改变了确认或重试决策，而且在第二套工具或公开真实回放任务上能核对，才值得扩成长文。若最强原生接口已经覆盖该问题，则保留先导工件。

现有证据限于本地裸仓库、真实 Git 条件更新、file:// 本地传输和受控顺序。响应由控制器隐藏。未测试托管服务、真实 Agent、非协作写者、并发事务读和掉电。操作标记是现有 Git 能力组成的实验对照。Git 已有条件更新、日志、日志过期及引用隐藏机制。[Git update-ref](https://git-scm.com/docs/git-update-ref) · [Git reflog](https://git-scm.com/docs/git-reflog) · [Git 配置](https://git-scm.com/docs/git-config)

## 复现

从仓库根目录运行。每次运行使用新的输出路径，隔离裸仓库放在本地，归档保存可移植的证据和摘要。

```powershell
.venv/Scripts/python.exe -m experiments.git_effect_evidence.run audit results/experiments/git-effect-evidence/pilot-01
.venv/Scripts/python.exe -m experiments.git_effect_evidence.run run .epochgate/git-effect-evidence-new
.venv/Scripts/python.exe -m experiments.git_effect_evidence.retry .epochgate/git-effect-evidence-retry-new
```
