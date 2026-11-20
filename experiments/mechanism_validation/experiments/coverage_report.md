# 第二生成器回放覆盖

A3 对新代码的判定是 INDEPENDENT。这次回放仍然不建立 Claim 优于 Outbox 的结论；独立性只说明新的提交、持久化、状态转移、恢复和确认不是同一个函数。

生成器比较结论是 DIFFERS。越过预先冻结阈值的轴：semantic_class_tv（0.25）、sku_dest_alias_rate（差 0.875）、world_state_tv（0.458）。没有越过的轴：载荷长度相对差 0.096、幂等键唯一率差 0、oracle 可裁定率差 0、authority_tv 0.125、successor_tv 0.056。第二代有一条 `effect_forbidden` 提案，合法载荷长度是 0，所以长度区间含 0。阈值文件在比较之前写入，比较之后没有改。
第二生成器是确定性的 `structured_factor_generator_v2`，不是语言模型。
可选模型生成：NOT_AVAILABLE。已有配置指向 `https://api.deepseek.com/v1`，那是远程付费接口。本次没有调用，也没有编造补全。

冻结清单里的提案-场景簇：24。实际写出的簇：24。单元格：96。
路径计数：{'Direct': 24, 'Admission': 24, 'Claim': 24, 'Outbox': 24}。
家族：authority, recovery, semantic, successor。
错误行：0。`semantic_validity` 为 `UNKNOWN` 的行：45。它们是没有物理字节时 Week 2 `semantics_oracle` 的返回值（权威家族的停发、恢复家族里 Direct 与 Admission 的 `NO_DURABLE_STATE`、后继家族的停发），不是失败后的自动重发。这些行都留在 `replay_joint.csv`。

每条路径使用提案自己的幂等键。单元格之间由实验控制器清空目标账本，避免上一条路径的效果变成下一条路径的重放。
恢复家族在 Claim 和 Outbox 上从各自的 SQLite 行继续；Direct 和 Admission 没有可恢复的行，结果是 `NO_DURABLE_STATE`。这是机制适用性差异，不是 Claim 优于 Outbox。
`RESTORED_BYTES` 只表示从本机制的持久行读回了字节。`confirmed_effect` 才表示审计进程在目标账本里看见了合法效果。
`process_kill=false`，`power_loss_claim=false`。这里没有 `TerminateProcess`，也没有把异常当成崩溃。

Week 4 的 w4l01–w4l18 只把散列抄进 `week5/proposals/registry.jsonl`。原始字节没有改，也没有拿来填这次回放。Week 1 到 Week 4 的格子没有写入本文件。
