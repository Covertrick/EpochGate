# Git 外部工作流资格审查结果

日期：2026-11-21。**两个候选均未通过 G1。本轮停止外部工作流复现和宿主验证。** 结论限于下面两个固定修订和检查的恢复路径。先导的 Git 引用回退反例成立。当前证据还不能把它归为这两个工作流的缺陷。

## Flux Image Automation

固定修订 `81b6a10eeca53a9bad1de8b739ae130accc30475`。

| 待核查项 | 结果 |
| --- | --- |
| 首次推送结果不确定后是否有恢复 | 有，`GitPushRetryOnFailure` 是可选功能，默认关闭。源码在受限重试循环内刷新远端。维护方已有覆盖“目标已写、响应超时”的测试。 |
| 是否原样重送第一次推送 | 查到远端尖端仍为原提交时确认第一推送，可按配置继续推送 refspec。否则重克隆、重新应用当前运行所持有的策略、重新提交并推送。如果刷新后发现已无变更，也可能直接结束。 |
| 独立引用回退是否构成停止该次操作的协议消息 | 已核对的文档以暂停自动化、暂停镜像扫描或修改镜像策略表示控制操作。没有找到把单独把推送分支退回旧 OID 定义为撤销进行中发布的契约。源码在进入协调前检查暂停。这项审查没有测试已开始的协调能否被实时取消。 |

**判定：具备恢复路径，撤回语义未建立。** 活跃策略仍要求同一镜像时，重新计算并恢复那个版本可能符合协调器职责。即使本地造出相同目标轨迹，也不能在没有真实撤回义务时标为误重试。

[推送重试实现](https://github.com/fluxcd/image-automation-controller/blob/81b6a10eeca53a9bad1de8b739ae130accc30475/internal/controller/push_retry.go#L79) · [CompletePush 与远端刷新实现](https://github.com/fluxcd/image-automation-controller/blob/81b6a10eeca53a9bad1de8b739ae130accc30475/internal/source/source.go#L425) · [源码中的推送超时测试](https://github.com/fluxcd/image-automation-controller/blob/81b6a10eeca53a9bad1de8b739ae130accc30475/internal/controller/push_retry_test.go#L216) · [维护方撤回与暂停指南](https://fluxcd.io/flux/guides/image-update/)

## create-pull-request

固定修订 `3ffccacd46053766916f087ce41d627c0b8cf2d1`，标签 `v8.1.3`。

无签名提交分支等待一次 `git push --force-with-lease`。Git 包装层执行一次命令，推送错误传播到该动作的失败处理。代码里的 API 重试和新建 PR 后的一致性重试属于其他调用。工作流可能由外部触发再次运行，但本次没有找到一个已配置的真实工作流，把它作为同一次未确认推送的恢复并规定必须保留独立撤回。

**判定：该入口没有满足同次恢复条件。** 没有把再次触发、人工 rerun 或 API 限流重试计作 Git 推送恢复。

[推送调用](https://github.com/peter-evans/create-pull-request/blob/3ffccacd46053766916f087ce41d627c0b8cf2d1/src/create-pull-request.ts#L239) · [Git 包装层](https://github.com/peter-evans/create-pull-request/blob/3ffccacd46053766916f087ce41d627c0b8cf2d1/src/git-command-manager.ts#L260) · [失败处理](https://github.com/peter-evans/create-pull-request/blob/3ffccacd46053766916f087ce41d627c0b8cf2d1/src/create-pull-request.ts#L322)

## 追加的真实 Git 传输控制

用隔离的发送与接收裸仓库运行普通 `git push`，三个控制均通过。没有使用强推或租约。

| 目标历史 | 发送 B 到目标分支的结果 |
| --- | --- |
| 初次未提交，目标在 A | 推送成功，目标到 B。 |
| 已到 B，随后独立重置到 A | 推送成功，目标再次到 B。 |
| 已到 B，随后产生以 B 为父的补偿提交 R | 推送旧 B 被拒，目标仍在 R。 |

补偿提交控制验证的是引用祖先关系。夹具使用合成提交，不测具体部署内容。运行入口是 `.venv/Scripts/python.exe -m experiments.git_effect_evidence.transport_control .epochgate/git-transport-control-new`，需使用新输出目录。[逐格结果](../../results/experiments/git-effect-evidence/workflow-screen-01/transport-control.json)

## 研究结论

G0 的技术反例得到补充证实，G1 在两个候选上均未通过。未运行候选项目的整套测试、未注入外部客户端故障，也没有向任何托管仓库推送。当前不支持把反例写成已验证的工作流缺陷，也不建议凭这批证据投 CCF C Full Paper。后续只有找到带明确撤回事务或操作取消契约的实际工作流，才值得重开复现与宿主测试。
